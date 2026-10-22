"""H-GAP1 per SPEC_GAP_2026-10-23.md. Writes results_gap.json."""
import json, os
import numpy as np, pandas as pd
import run_study as R
HERE = os.path.dirname(os.path.abspath(__file__))


def sessions(d):
    rows = []; prev_close = None; hist = []
    for day, g in d.groupby("day", sort=True):
        if len(g) < 340:
            continue
        g = g.reset_index(drop=True)
        if prev_close is not None and len(hist) >= 20:
            sd = np.std(hist[-20:], ddof=1)
            gap = g.open.iloc[0] / prev_close - 1
            ent = g.close[g["mod"] == 14]; ex = g.close[g["mod"] == 359]
            if sd > 0 and len(ent) and len(ex):
                R_ = (ex.iloc[0] / ent.iloc[0] - 1) * 1e4
                fill = bool((g.low[g["mod"] >= 15 ].min() <= prev_close) if gap > 0 else (g.high[g["mod"] >= 15].max() >= prev_close))
                rows.append((day, gap / sd, np.sign(gap) * R_, fill))
        if prev_close is not None:
            hist.append(g.close.iloc[-1] / prev_close - 1)
        prev_close = g.close.iloc[-1]
    return pd.DataFrame(rows, columns=["day", "z", "cont", "fill"])


def boot(x, B=2000, seed=7):
    rng = np.random.default_rng(seed); m = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(B)]
    return [round(float(np.percentile(m, 2.5)), 2), round(float(np.percentile(m, 97.5)), 2)]


def main():
    out = {}; cells = []
    for inst, (path, kind) in R.FILES.items():
        s = sessions(R.load(kind, path)); s = s[s.z.abs() >= 0.5]; out[inst] = {}
        for per, (a, b) in R.PERIODS.items():
            x = s[(s.day >= a) & (s.day <= b)]
            if not len(x): continue
            allm = float(x.cont.mean()); ci = boot(x.cont.values)
            r = {"n": int(len(x)), "mean_cont_bps": round(allm, 2), "ci95": ci, "hit_cont": round(float((x.cont > 0).mean()), 3), "gap_fill_share": round(float(x.fill.mean()), 3), "buckets": {}}
            for name, lo, hi in (("A 0.5-1", 0.5, 1), ("B 1-2", 1, 2), ("C >=2", 2, 99)):
                y = x[(x.z.abs() >= lo) & (x.z.abs() < hi)]
                if len(y): r["buckets"][name] = {"n": int(len(y)), "mean_cont_bps": round(float(y.cont.mean()), 2), "ci95": boot(y.cont.values)}
            out[inst][per] = r; cells.append((allm, ci))
            print(inst, per, "n", r["n"], "mean CONT bps", r["mean_cont_bps"], ci, "hit", r["hit_cont"], "fill", r["gap_fill_share"], {k: (v["n"], v["mean_cont_bps"]) for k, v in r["buckets"].items()})
    sgn = {np.sign(m) for m, _ in cells}; excl = all(c[0] > 0 or c[1] < 0 for _, c in cells); big = all(abs(m) >= 8 for m, _ in cells)
    out["verdict"] = "EDGE" if (len(sgn) == 1 and excl and big) else "NO GAP EDGE"
    print("VERDICT:", out["verdict"], "| same sign", len(sgn) == 1, "| CI excludes 0 in all", excl, "| |mean|>=8bps in all", big)
    json.dump(out, open(os.path.join(HERE, "results_gap.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
