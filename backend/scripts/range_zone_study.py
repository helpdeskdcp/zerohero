"""Range & zone study, exactly as pre-registered (data/research/range_zone/SPEC_2026-10-24.md). Writes results.json."""
import json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from app.index_signal_research.data import load_index_5m
from app.candle_ai import levels as L, range_zone as RZ

HZ = (3, 6, 12); COST = 1.0; ALPHA = 0.05 / 8; MIN_N = 300; EDGE = RZ.EDGE_ATR; TIME_STOP = 24
EVALS = ("h3", "h6", "h12", "STRUCT")


def fixed(df, idx, side, h, cost=COST):
    o = df["o"].values.astype(float); c = df["c"].values.astype(float); date = L.session_date(df["t"]).values; n = len(df); out = []; last = -10 ** 9
    for i in idx:
        if i + 1 >= n or i + h >= n or date[i + h] != date[i] or i <= last + h: continue
        last = i; out.append((date[i], side * (c[i + h] / o[i + 1] - 1) * 1e4 - cost))
    return pd.DataFrame(out, columns=["date", "net"])


def struct(df, sg, variant, idx, cost=COST):
    o = df["o"].values.astype(float); h = df["h"].values.astype(float); l = df["l"].values.astype(float); c = df["c"].values.astype(float); date = L.session_date(df["t"]).values; n = len(df)
    RH, RL, MID, A = sg["rh"].values, sg["rl"].values, sg["mid"].values, sg["atr"].values; side = RZ.SIDE[variant]; out = []; last = -10 ** 9
    for i in idx:
        if i + 1 >= n or i <= last + TIME_STOP or date[i + 1] != date[i]: continue
        ent = o[i + 1]; ht = RH[i] - RL[i]
        stop, tgt = {"BRK_UP": (RH[i] - EDGE * A[i], ent + ht), "BRK_DN": (RL[i] + EDGE * A[i], ent - ht), "FADE_LONG": (RL[i] - EDGE * A[i], MID[i]), "FADE_SHORT": (RH[i] + EDGE * A[i], MID[i])}[variant]
        if (side == 1 and not (stop < ent < tgt)) or (side == -1 and not (tgt < ent < stop)): continue            # entry already beyond a level: not a valid setup
        exit_px = None
        for j in range(i + 1, min(n, i + 1 + TIME_STOP)):
            if date[j] != date[i]: exit_px = c[j - 1]; break
            hit_stop = (l[j] <= stop) if side == 1 else (h[j] >= stop); hit_tgt = (h[j] >= tgt) if side == 1 else (l[j] <= tgt)
            if hit_stop: exit_px = stop; break
            if hit_tgt: exit_px = tgt; break
        if exit_px is None: exit_px = c[min(n - 1, i + TIME_STOP)]
        last = i; out.append((date[i], side * (exit_px / ent - 1) * 1e4 - cost))
    return pd.DataFrame(out, columns=["date", "net"])


def cell(t):
    if len(t) < 30: return {"n": int(len(t))}
    g = t.groupby("date")["net"]; s = g.sum().values; n = g.count().values; rng = np.random.default_rng(7); bs = [s[k].sum() / n[k].sum() for k in (rng.integers(0, len(s), len(s)) for _ in range(2000))]
    return {"n": int(len(t)), "mean_net": float(t["net"].mean()), "hit": float((t["net"] > 0).mean()), "ci_lo_bonf": float(np.percentile(bs, 100 * ALPHA / 2)), "ci95": [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))]}


def main():
    res = {"cells": {}, "verdict": {}}
    for sym in ("NIFTY", "BANKNIFTY"):
        df = load_index_5m(sym); yr = pd.DatetimeIndex(L.session_date(df["t"])).year.values
        sgs = {"full": RZ.signals(df, True), "no_edge_touch": RZ.signals(df, False)}
        for v in RZ.VARIANTS:
            for name, sg in sgs.items():
                for per, (a, b) in (("P1", (2015, 2020)), ("P2", (2021, 2026))):
                    idx = np.where(sg[v].values & (yr >= a) & (yr <= b))[0]
                    for h in HZ: res["cells"][f"{sym}|{v}|{name}|{per}|h{h}"] = cell(fixed(df, idx, RZ.SIDE[v], h))
                    res["cells"][f"{sym}|{v}|{name}|{per}|STRUCT"] = cell(struct(df, sg, v, idx))
        print(sym, "done", flush=True)
    for v in RZ.VARIANTS:
        enough = True; ok_e = 0
        for e in EVALS:
            good = True
            for sym in ("NIFTY", "BANKNIFTY"):
                for per in ("P1", "P2"):
                    c = res["cells"][f"{sym}|{v}|full|{per}|{e}"]; enough &= c.get("n", 0) >= MIN_N; good &= bool(c.get("n", 0) >= MIN_N and c.get("mean_net", -1) > 0 and c.get("ci_lo_bonf", -1) > 0)
            ok_e += good
        res["verdict"][v] = "INSUFFICIENT_SAMPLE" if not enough else ("GO-SHADOW" if ok_e >= 2 else "NO-GO"); res["verdict"][v + "_evals_passing"] = ok_e
    json.dump(res, open(os.path.join(HERE, "data", "research", "range_zone", "results.json"), "w"), indent=1, default=float)
    print(json.dumps(res["verdict"]))
    print(f"{'variant':11}{'inst':10}{'per':4}{'eval':7}{'n':>6}{'mean net':>10}{'hit%':>6}  CI95")
    for v in RZ.VARIANTS:
        for sym in ("NIFTY", "BANKNIFTY"):
            for per in ("P1", "P2"):
                for e in ("h6", "STRUCT"):
                    c = res["cells"][f"{sym}|{v}|full|{per}|{e}"]
                    print(f"{v:11}{sym:10}{per:4}{e:7}{c['n']:6d}" + (f"{c['mean_net']:10.2f}{100 * c['hit']:6.0f}  {[round(x, 1) for x in c['ci95']]}" if 'mean_net' in c else "  too few"))


if __name__ == "__main__":
    main()
