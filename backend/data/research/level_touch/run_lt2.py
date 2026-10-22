"""H-LT2: after a touch, continuation vs reversal. Implements SPEC_LT2_2026-10-23.md exactly. Writes results_lt2.json beside this file."""
import json
import os
from math import sqrt

import numpy as np
import pandas as pd

import run_study as R

HERE = os.path.dirname(os.path.abspath(__file__))
RACE = 20
HALF = 0.5
ANCHOR = os.environ.get("LT2_ANCHOR", "LEVEL")                # LEVEL = spec as written; CLOSE = addendum: targets anchored on the touch-bar close (symmetric race)


def events(d):
    rows = []
    prev = None; prev_sw = []
    for day, g in d.groupby("day", sort=True):
        g = g.reset_index(drop=True)
        if len(g) < 340:
            prev = g; prev_sw = R.swings(g) if len(g) > 60 else []; continue
        hi, lo, cl, ev, mod = g.high.values, g.low.values, g.close.values, g.ev.values, g["mod"].values
        orh, orl = hi[:15].max(), lo[:15].min(); sw_today = R.swings(g)
        ph, pl = (prev.high.max(), prev.low.min()) if prev is not None else (None, None)
        for t in range(15, 336, 10):
            if t + R.H + RACE >= len(g) or mod[t + R.H + RACE] != mod[t] + R.H + RACE or np.isnan(ev[t]) or ev[t] <= 0:
                continue
            P = cl[t]; s10 = sqrt(R.H * ev[t]); unit = s10 * P
            levels = [(P * (1 + sd * dd * s10), "PLAIN") for dd in R.DS for sd in (1, -1)]
            cands = [(ph, "PDH"), (pl, "PDL"), (orh, "ORH"), (orl, "ORL")] + [(px, "SW" + k) for cm, k, px in prev_sw] + [(px, "SW" + k) for cm, k, px in sw_today if cm <= t + 1]
            for L, kind in cands:
                if L is not None and 0.25 <= abs(L - P) / unit <= 2.0:
                    levels.append((L, "STRUCT:" + kind))
            for L, src in levels:
                above = L > P
                w = range(t + 1, t + R.H + 1)
                j = next((k for k in w if (hi[k] >= L if above else lo[k] <= L)), None)
                if j is None:
                    continue
                beyond = cl[j] > L if above else cl[j] < L
                base = L if ANCHOR == "LEVEL" else cl[j]
                ct, rt = (base + HALF * unit, base - HALF * unit) if above else (base - HALF * unit, base + HALF * unit)
                out = "NONE"
                for k in range(j + 1, j + RACE + 1):
                    c = hi[k] >= ct if above else lo[k] <= ct
                    r = lo[k] <= rt if above else hi[k] >= rt
                    if c and r:
                        break
                    if c:
                        out = "CONT"; break
                    if r:
                        out = "REV"; break
                rows.append((day, "STRUCT" if src.startswith("STRUCT") else "PLAIN", src, "UP" if above else "DOWN", "BEYOND" if beyond else "REJECTED", out))
        prev, prev_sw = g, sw_today
    return pd.DataFrame(rows, columns=["day", "source", "kind", "side", "cls", "out"])


def share(df):
    c = (df.out == "CONT").sum(); r = (df.out == "REV").sum()
    return (c / (c + r)) if (c + r) else np.nan


def boot_diff(a, b, B=1000, seed=7):
    """CI of share(a) - share(b), resampling days. a, b are event frames."""
    days = np.array(sorted(set(a.day) | set(b.day))); rng = np.random.default_rng(seed)
    def agg(x):
        t = x.assign(c=(x.out == "CONT").astype(int), r=(x.out == "REV").astype(int)).groupby("day")[["c", "r"]].sum()
        return t.reindex(days, fill_value=0).values
    A, Bm = agg(a), agg(b); out = []
    for _ in range(B):
        i = rng.integers(0, len(days), len(days)); sa = A[i].sum(0); sb = Bm[i].sum(0)
        if sa.sum() and sb.sum():
            out.append(sa[0] / sa.sum() - sb[0] / sb.sum())
    pt = (A[:, 0].sum() / max(A.sum(), 1)) - (Bm[:, 0].sum() / max(Bm.sum(), 1))
    return round(float(pt), 4), [round(float(np.percentile(out, 2.5)), 4), round(float(np.percentile(out, 97.5)), 4)]


def main():
    res = {}
    for inst, (path, kind) in R.FILES.items():
        ev = events(R.load(kind, path)); res[inst] = {}
        for per, (a, b) in R.PERIODS.items():
            e = ev[(ev.day >= a) & (ev.day <= b)]
            if not len(e):
                continue
            r = {"events": int(len(e)), "none_share": round(float((e.out == "NONE").mean()), 3), "cells": {}, "class_effect": {}, "level_effect": {}}
            for src in ("PLAIN", "STRUCT"):
                for side in ("UP", "DOWN", "BOTH"):
                    for cls in ("BEYOND", "REJECTED"):
                        x = e[(e.source == src) & (e.cls == cls) & ((e.side == side) | (side == "BOTH"))]
                        r["cells"][f"{src}/{side}/{cls}"] = {"n": int(len(x)), "cont_share": None if np.isnan(share(x)) else round(float(share(x)), 4)}
            for src in ("PLAIN", "STRUCT"):
                x = e[e.source == src]
                r["class_effect"][src] = boot_diff(x[x.cls == "BEYOND"], x[x.cls == "REJECTED"])
            for cls in ("BEYOND", "REJECTED"):
                x = e[e.cls == cls]
                r["level_effect"][cls] = boot_diff(x[x.source == "STRUCT"], x[x.source == "PLAIN"])
            res[inst][per] = r
            print(inst, per, "events", r["events"], "none", r["none_share"])
            print("   cont_share:", {k: v["cont_share"] for k, v in r["cells"].items() if "/BOTH/" in k})
            print("   class_effect:", r["class_effect"], "| level_effect:", r["level_effect"])
    json.dump(res, open(os.path.join(HERE, "results_lt2.json" if ANCHOR == "LEVEL" else "results_lt2_close_anchor.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
