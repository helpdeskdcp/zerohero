"""Pivot-line reversal study, exactly as pre-registered (data/research/pivot_reversal/SPEC_2026-10-24.md). Writes results.json."""
import json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from app.index_signal_research.data import load_index_5m
from app.candle_ai import evaluate as E, levels as L, pivot_reversal as PR

HZ = (3, 6, 12); COST = 1.0; ALPHA = 0.05 / 8; MIN_N = 300
SIDE = {"ENGULF_BUY": 1, "HARAMI_BUY": 1, "ENGULF_SELL": -1, "HARAMI_SELL": -1}


def trades(df, flag, side, h, cost=COST):
    o = df["o"].values.astype(float); c = df["c"].values.astype(float); date = L.session_date(df["t"]).values; n = len(df); out = []; last = -10 ** 9
    for i in np.where(flag)[0]:
        if i + 1 >= n or i + h >= n or date[i + h] != date[i]: continue
        if i <= last + h: continue
        last = i; out.append((date[i], side * (c[i + h] / o[i + 1] - 1) * 1e4 - cost))
    return pd.DataFrame(out, columns=["date", "net"])


def cell(t):
    if len(t) < 30: return {"n": int(len(t))}
    lo = E.boot_mean(t["net"].values, t["date"].values, B=800); k = ALPHA / 0.05
    g = t.groupby("date")["net"]; s = g.sum().values; n = g.count().values; rng = np.random.default_rng(7); bs = [s[kk].sum() / n[kk].sum() for kk in (rng.integers(0, len(s), len(s)) for _ in range(2000))]
    return {"n": int(len(t)), "mean_net": float(t["net"].mean()), "hit": float((t["net"] > 0).mean()), "ci_lo_bonf": float(np.percentile(bs, 100 * ALPHA / 2)), "ci95": [lo[0], lo[1]]}


def main():
    res = {"cells": {}, "baselines": {}, "verdict": {}}
    for sym in ("NIFTY", "BANKNIFTY"):
        df = load_index_5m(sym); sg = PR.signals(df); yr = pd.DatetimeIndex(L.session_date(df["t"])).year.values
        for v in PR.VARIANTS:
            pat, near = {"ENGULF_BUY": ("eng_bull", "near_low"), "ENGULF_SELL": ("eng_bear", "near_high"), "HARAMI_BUY": ("har_bull", "near_low"), "HARAMI_SELL": ("har_bear", "near_high")}[v]
            sets = {"full": sg[v], "no_box": sg[pat] & sg[near], "no_pivot": sg[pat] & sg["box"], "pattern_only": sg[pat], "drift_all_bars": pd.Series(True, index=df.index)}
            for name, flag in sets.items():
                for per, (a, b) in (("P1", (2015, 2020)), ("P2", (2021, 2026))):
                    msk = (yr >= a) & (yr <= b); f = flag.values & msk
                    for h in HZ:
                        t = trades(df, f, SIDE[v], h); res["cells"][f"{sym}|{v}|{name}|{per}|h{h}"] = cell(t)
        print(sym, "done", flush=True)
    for v in PR.VARIANTS:
        ok_h = 0; enough = True
        for h in HZ:
            good = True
            for sym in ("NIFTY", "BANKNIFTY"):
                for per in ("P1", "P2"):
                    c = res["cells"][f"{sym}|{v}|full|{per}|h{h}"]
                    if c.get("n", 0) < MIN_N: enough = False
                    good &= bool(c.get("n", 0) >= MIN_N and c.get("mean_net", -1) > 0 and c.get("ci_lo_bonf", -1) > 0)
            ok_h += good
        res["verdict"][v] = "INSUFFICIENT_SAMPLE" if not enough else ("GO-SHADOW" if ok_h >= 2 else "NO-GO"); res["verdict"][v + "_horizons_passing"] = ok_h
    json.dump(res, open(os.path.join(HERE, "data", "research", "pivot_reversal", "results.json"), "w"), indent=1, default=float)
    print(json.dumps(res["verdict"]))
    print(f"{'cell':44}{'n':>6}{'mean net bp':>13}{'hit%':>6}{'CI95':>20}")
    for key in sorted(res["cells"]):
        sym, v, name, per, h = key.split("|")
        if per == "P1" and h == "h6" and name in ("full", "no_box", "no_pivot", "pattern_only", "drift_all_bars") and (v in ("ENGULF_BUY", "HARAMI_SELL")):
            c = res["cells"][key]; 
            if c.get("n", 0) >= 30: print(f"{key:44}{c['n']:6d}{c['mean_net']:13.2f}{100 * c['hit']:6.0f}{str([round(x, 1) for x in c['ci95']]):>20}")


if __name__ == "__main__":
    main()
