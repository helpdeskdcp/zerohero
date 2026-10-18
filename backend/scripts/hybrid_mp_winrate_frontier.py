"""Win-rate / payoff FRONTIER of Hybrid MP on NIFTY 5m, with the engine's own honest mechanics (executable next-open fills, adverse-first, gaps, cost).
Pre-registered grid: entry filter {previous min_risk_atr 0.5, corrected 2.0} x exit structure {single exit at T1 with T1 = m*R, m in .2/.25/.33/.4/.5/.75/1.0 ;
SCALE_BE with targets m*(1,2,3,4), m in .25/.5/1.0}. 'win' = trade net P&L > 0 after 2 bps. Reports TRAIN / VALID / 2024+. READ-ONLY.
Question answered: can the win rate be raised to ~71% for real, and what does it cost in expectancy?"""
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import corrected_params, fixed_params, resample, run  # noqa: E402
from app.research_engines.smart_bos.data import load_1m  # noqa: E402
from app.research_engines.smart_bos.splits import OOS, TRAIN, VALID, of_split  # noqa: E402

COST = 2.0
LINES = []


def say(s=""):
    print(s)
    LINES.append(s)


b1 = load_1m("NIFTY", "INDEX", start="2017-01-01", end="2027-01-01", prefer="ANGELONE_CANDLES")
ch = resample(b1, 5, 555, 930)


def m(sub):
    if not sub:
        return "n=0"
    g = np.array([t.bps for t in sub]) - COST
    w, l = g[g > 0], g[g <= 0]
    be = (-l.mean() / (w.mean() - l.mean())) * 100 if len(w) and len(l) else float("nan")
    pf = w.sum() / -l.sum() if l.sum() < 0 else float("inf")
    return f"n={len(g):4d} win {np.mean(g > 0) * 100:4.1f}% (b/e {be:4.1f}%) net {g.mean():+6.2f} PF {pf:4.2f}"


say(f"{'entry':9s} {'exit structure':20s} | {'TRAIN 2017-21':50s} | {'VALID 2022-23':50s} | 2024+")
res = {}
for ename, ef in (("previous", fixed_params), ("CORRECT", corrected_params)):
    structs = [(f"T1 @ {mm}R (all out)", dict(exit_model="T1", target_mults=(mm, 2 * mm, 3 * mm, 4 * mm))) for mm in (0.2, 0.25, 0.33, 0.4, 0.5, 0.75, 1.0)]
    structs += [(f"SCALE_BE m={mm}", dict(exit_model="SCALE_BE", target_mults=(mm, 2 * mm, 3 * mm, 4 * mm))) for mm in (0.25, 0.5, 1.0)]
    for sname, kw in structs:
        tr, _ = run(ch, ef(chart_tf=5, **kw), b1=b1)
        res[(ename, sname)] = tr
        say(f"{ename:9s} {sname:20s} | {m(of_split(tr, TRAIN)):50s} | {m(of_split(tr, VALID)):50s} | {m(of_split(tr, OOS))}")
open("data/research/strategy_lab/hybrid_winrate_frontier_2026-10-18.txt", "w").write("\n".join(LINES) + "\n")
