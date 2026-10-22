"""H-LT1 level-touch study. Implements data/research/level_touch/SPEC_2026-10-23.md exactly. Read-only (reads the two CSVs, prints JSON, writes results.json beside this file)."""
import json
import os
import sys
from math import erf, sqrt

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
K = os.path.join(os.path.dirname(os.path.dirname(HERE)), "historical", "kaggle")
FILES = {
    "NIFTY": (os.path.join(K, "mavrick136__10-years-of-nifty-50-1-minute-data-20152025", "NIFTY_1MIN_2015-2025.csv"), "nifty"),
    "BANKNIFTY": (os.path.join(K, "sandeepkapri__banknifty-data-upto-2024", "bank-nifty-1m-data.csv"), "bnf"),
}
DS = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0]
HL = 30            # EWMA halflife in bars
H = 10             # touch window in bars
SW = 3             # fractal half-width on 5-minute bars
PERIODS = {"P1": ("2015-01-01", "2019-12-31"), "P2": ("2020-01-01", "2099-01-01")}


def phi(x):
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def load(kind, path):
    if kind == "nifty":
        d = pd.read_csv(path, usecols=["datetime", "open", "high", "low", "close"])
        ts = pd.to_datetime(d["datetime"].str.slice(0, 19))
    else:
        d = pd.read_csv(path)
        ts = pd.to_datetime(d["Date"] + " " + d["Time"], format="%d-%m-%Y %H:%M:%S")
        d = d.rename(columns=str.lower)
    d = d.assign(ts=ts).loc[:, ["ts", "open", "high", "low", "close"]].dropna()
    d = d[(d.ts.dt.hour * 60 + d.ts.dt.minute >= 9 * 60 + 15) & (d.ts.dt.hour * 60 + d.ts.dt.minute <= 15 * 60 + 29)]   # 09:15..15:29 bars
    d = d.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    d["day"] = d.ts.dt.date.astype(str)
    d["mod"] = d.ts.dt.hour * 60 + d.ts.dt.minute - (9 * 60 + 15)
    r = np.log(d.close).diff()
    r[d.day != d.day.shift()] = np.nan                                  # overnight gap is not a 1-minute return
    d["ev"] = (r ** 2).ewm(halflife=HL, ignore_na=True).mean()
    return d


def swings(day_df):
    """Confirmed fractal swings on 5-minute bars of one day: list of (confirm_mod, 'H'|'L', price). Confirmed only SW 5-minute bars after the swing bar."""
    g = day_df.groupby(day_df["mod"] // 5)
    b = pd.DataFrame({"h": g.high.max(), "l": g.low.min()}).sort_index()
    out = []
    hv, lv = b.h.values, b.l.values
    for i in range(SW, len(b) - SW):
        if hv[i] > hv[i - SW:i].max() and hv[i] > hv[i + 1:i + SW + 1].max():
            out.append(((b.index[i + SW] + 1) * 5, "H", float(hv[i])))     # known at the close of the confirming 5m bar
        if lv[i] < lv[i - SW:i].min() and lv[i] < lv[i + 1:i + SW + 1].min():
            out.append(((b.index[i + SW] + 1) * 5, "L", float(lv[i])))
    return out


def study(d):
    days = list(d.groupby("day", sort=True))
    probe, struct = [], []
    prev = None; prev_sw = []
    for day, g in days:
        g = g.reset_index(drop=True)
        if len(g) < 340:
            prev = g; prev_sw = swings(g) if len(g) > 60 else []; continue
        hi, lo, cl, ev, mod = g.high.values, g.low.values, g.close.values, g.ev.values, g["mod"].values
        orh, orl = hi[:15].max(), lo[:15].min()                          # bars 09:15..09:29
        sw_today = swings(g)
        ph, pl = (prev.high.max(), prev.low.min()) if prev is not None else (None, None)
        for t in range(15, 336, 10):                                      # 09:30 .. 14:50 inclusive (mod 15,25,..,335)
            if t + H >= len(g) or mod[t + H] != mod[t] + H or np.isnan(ev[t]) or ev[t] <= 0:
                continue
            P = cl[t]; s10 = sqrt(H * ev[t]); wh = hi[t + 1:t + H + 1].max(); wl = lo[t + 1:t + H + 1].min()
            for dd in DS:
                for side in (1, -1):
                    L = P * (1 + side * dd * s10)
                    hit = (wh >= L) if side == 1 else (wl <= L)
                    probe.append((day, dd, min(1.0, 2 * (1 - phi(dd))), int(hit)))
            cands = [(ph, "PDH"), (pl, "PDL"), (orh, "ORH"), (orl, "ORL")]
            for cm, k, px in prev_sw:                                    # yesterday's swings are all known
                cands.append((px, "SW" + k))
            for cm, k, px in sw_today:
                if cm <= t + 1:                                          # confirmed by the close of bar t
                    cands.append((px, "SW" + k))
            for L, kind in cands:
                if L is None:
                    continue
                dd = abs(L - P) / (P * s10)
                if 0.25 <= dd <= 2.0:
                    above = L > P
                    hit = (wh >= L) if above else (wl <= L)
                    struct.append((day, kind, dd, min(1.0, 2 * (1 - phi(dd))), int(hit)))
        prev, prev_sw = g, sw_today
    return (pd.DataFrame(probe, columns=["day", "d", "p", "y"]), pd.DataFrame(struct, columns=["day", "kind", "d", "p", "y"]))


def logit_slope(p, y):
    x = np.log(np.clip(p, 1e-4, 1 - 1e-4) / (1 - np.clip(p, 1e-4, 1 - 1e-4)))
    X = np.c_[np.ones_like(x), x]; b = np.array([0.0, 1.0])
    for _ in range(50):
        z = X @ b; mu = 1 / (1 + np.exp(-z)); W = mu * (1 - mu) + 1e-9
        step = np.linalg.solve(X.T @ (X * W[:, None]) + 1e-9 * np.eye(2), X.T @ (y - mu))
        b = b + step
        if np.abs(step).max() < 1e-8:
            break
    return float(b[1]), float(b[0])


def calib(df):
    bins = np.minimum((df.p.values * 10).astype(int), 9); rows = []; err = 0.0
    for k in range(10):
        m = bins == k
        if m.sum():
            rows.append({"bin": f"{k/10:.1f}-{(k+1)/10:.1f}", "n": int(m.sum()), "pred": round(float(df.p.values[m].mean()), 4), "obs": round(float(df.y.values[m].mean()), 4)})
            err += m.sum() * abs(df.y.values[m].mean() - df.p.values[m].mean())
    base = df.y.mean(); brier = float(((df.p - df.y) ** 2).mean()); brier0 = float(((base - df.y) ** 2).mean())
    slope, icpt = logit_slope(df.p.values, df.y.values)
    return {"n": int(len(df)), "reliability_error": round(err / len(df), 4), "slope": round(slope, 3), "intercept": round(icpt, 3),
            "brier": round(brier, 4), "brier_skill_vs_base": round(1 - brier / brier0, 4) if brier0 else None, "touch_rate": round(float(base), 4), "bins": rows}


def lift(sdf, B=1000, seed=7):
    res = {}
    rng = np.random.default_rng(seed)
    for name, g in [("ALL", sdf)] + [(k, x) for k, x in sdf.groupby("kind")]:
        if len(g) < 200:
            continue
        agg = g.assign(diff=g.y - g.p).groupby("day").agg(s=("diff", "sum"), n=("diff", "size"))
        S, N = agg.s.values, agg.n.values; nd = len(S)
        boots = []
        for _ in range(B):
            i = rng.integers(0, nd, nd); boots.append(S[i].sum() / N[i].sum())
        res[name] = {"n": int(len(g)), "mean_lift": round(float(S.sum() / N.sum()), 4), "ci95": [round(float(np.percentile(boots, 2.5)), 4), round(float(np.percentile(boots, 97.5)), 4)]}
    return res


def main():
    out = {}
    for inst, (path, kind) in FILES.items():
        d = load(kind, path); out[inst] = {"bars": int(len(d)), "from": str(d.day.iloc[0]), "to": str(d.day.iloc[-1])}
        probe, struct = study(d)
        for per, (a, b) in PERIODS.items():
            pp = probe[(probe.day >= a) & (probe.day <= b)]; ss = struct[(struct.day >= a) & (struct.day <= b)]
            if len(pp) == 0:
                continue
            c = calib(pp)
            ok = c["reliability_error"] <= 0.03 and 0.8 <= c["slope"] <= 1.2
            out[inst][per] = {"calibration": c, "model_ok": bool(ok), "days": int(pp.day.nunique()), "structural_lift": lift(ss) if len(ss) else {}}
        print(inst, "done", file=sys.stderr)
    json.dump(out, open(os.path.join(HERE, "results.json"), "w"), indent=1)
    for inst in out:
        for per in PERIODS:
            if per in out[inst]:
                x = out[inst][per]; c = x["calibration"]
                print(inst, per, "days", x["days"], "n", c["n"], "rel_err", c["reliability_error"], "slope", c["slope"], "brier_skill", c["brier_skill_vs_base"], "MODEL_OK" if x["model_ok"] else "MISCALIBRATED")
                print("   lift:", json.dumps(x["structural_lift"]))


if __name__ == "__main__":
    main()
