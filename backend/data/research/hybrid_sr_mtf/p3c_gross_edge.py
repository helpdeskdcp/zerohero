"""Is there ANY gross (before-cost) directional information? Per candidate, OUT-OF-SAMPLE: mean gross R of the strategy, of its MIRROR (same entry, opposite side, mirrored stop / targets), and the paired difference with a 90 % day-cluster
bootstrap interval; plus gross index points per trade versus the break-even index move implied by the option friction (friction / (delta * lot))."""
import json, os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))
from app.research_engines.hybrid_sr_mtf import data as D, engine as E, metrics as M
from p3_backtest import SPLITS, VARIANTS, mirror


def cluster_ci(x, days, B=3000, seed=5):
    s = pd.Series(x).groupby(days); sums = s.sum().to_numpy(); cnt = s.count().to_numpy()
    if len(sums) < 4: return [float("nan")] * 2
    rng = np.random.default_rng(seed); out = []
    for _ in range(B):
        k = rng.integers(0, len(sums), len(sums)); out.append(sums[k].sum() / cnt[k].sum())
    return [round(float(np.percentile(out, 5)), 3), round(float(np.percentile(out, 95)), 3)]


out = {}
for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
    prep = D.prepare(sym, D.load_index_1m(sym)); a, b = SPLITS[sym]["OOS"]; out[sym] = {}
    for vname, prm in VARIANTS.items():
        for cand in E.CANDIDATES:
            tr, _, _ = E.scan(prep, cand, prm); tr = [t for t in tr if a <= t["day"] <= b]
            if len(tr) < 8: out[sym][f"{vname}/{cand}"] = {"n": len(tr)}; continue
            mir = [mirror(prep, t) for t in tr]; days = np.array([t["day"] for t in tr]); R = np.array([t["R"] for t in tr]); Rm = np.array([m["R"] for m in mir]); pts = np.array([t["pts"] for t in tr])
            fr = float(np.mean([M.net_rs(sym, 0.0)[1]])); be_pts = fr / (M.DELTA * M.LOT[sym])
            out[sym][f"{vname}/{cand}"] = {"n": len(tr), "gross_R_strategy": round(float(R.mean()), 3), "gross_R_strategy_ci90": cluster_ci(R, days), "gross_R_mirror": round(float(Rm.mean()), 3), "paired_diff_R": round(float((R - Rm).mean()), 3),
                                          "paired_diff_ci90": cluster_ci(R - Rm, days), "gross_pts_per_trade": round(float(pts.mean()), 2), "gross_pts_ci90": cluster_ci(pts, days), "breakeven_index_pts_for_option_friction": round(be_pts, 2)}
            r = out[sym][f"{vname}/{cand}"]; print(sym, vname, cand, "n", r["n"], "grossR", r["gross_R_strategy"], r["gross_R_strategy_ci90"], "mirror", r["gross_R_mirror"], "diff", r["paired_diff_R"], r["paired_diff_ci90"], "pts", r["gross_pts_per_trade"], "be", r["breakeven_index_pts_for_option_friction"], flush=True)
json.dump(out, open(os.path.join(HERE, "p3c_gross_edge.json"), "w"), indent=1)
