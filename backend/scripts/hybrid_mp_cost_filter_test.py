"""Pre-registered correction test for Hybrid MP's measured failure mode (cost 0.11-0.14 R vs gross +0.03 R; the only positive bucket is large stops).
Variants (fixed in advance): min_risk_atr in {0.5 (current), 1.0, 1.5, 2.0}, max_risk_atr = max(3.0, min+1). Everything else = fixed_params.
Selection uses TRAIN only; VALID (2022-23) is the clean confirmation; OOS 2024+ is NOT independent for this idea (its stop-size buckets were printed by the
failure analysis before this test) and is reported only for completeness. Then the promotion gate. READ-ONLY."""
import sys

import numpy as np

sys.path.insert(0, ".")
from app.hybrid_orderflow.policy import load_policy  # noqa: E402
from app.research_engines.hybrid_mp.engine import fixed_params, resample, run  # noqa: E402
from app.research_engines.smart_bos.data import load_1m  # noqa: E402
from app.research_engines.smart_bos.splits import OOS, TRAIN, VALID, of_split  # noqa: E402
from app.strategy_lab import edge_scan as ES  # noqa: E402
from app.strategy_lab import gate as G  # noqa: E402

COST = 2.0
b1 = load_1m("NIFTY", "INDEX", start="2017-01-01", end="2027-01-01", prefer="ANGELONE_CANDLES")
ch = resample(b1, 5, 555, 930)
PR = load_policy()["promotion"]
LINES = []


def say(s=""):
    print(s)
    LINES.append(s)


def stats(sub):
    if not sub:
        return "n=0"
    g = np.array([t.bps for t in sub]); d = np.array([t.day for t in sub])
    mu, p, n, nd = ES.cluster_t(d, g - COST)
    return f"n={n:4d} gross {g.mean():+5.2f} net {mu:+5.2f} bps p(>0)={p:.3f}"


say(f"cost {COST} bps; variant: min_risk_atr / max_risk_atr")
say(f"{'variant':16s} | {'TRAIN':44s} | {'VALID (clean)':44s} | OOS (not independent)")
res = {}
for mn in (0.5, 1.0, 1.5, 2.0):
    mx = max(3.0, mn + 1.0)
    tr, _ = run(ch, fixed_params(chart_tf=5, min_risk_atr=mn, max_risk_atr=mx), b1=b1)
    res[mn] = tr
    say(f"{mn:4.1f} / {mx:4.1f}      | {stats(of_split(tr, TRAIN)):44s} | {stats(of_split(tr, VALID)):44s} | {stats(of_split(tr, OOS))}")
say("\nGate on OOS trades for each variant (informational: OOS not independent for the stop-size idea):")
for mn, tr in res.items():
    sub = sorted(of_split(tr, OOS), key=lambda t: (t.day, t.entry_i))
    g = G.evaluate([t.bps for t in sub], [t.day for t in sub], COST, PR)
    say(f"  min_risk_atr {mn}: {g['verdict']} {g['failed']}")
open("data/research/strategy_lab/hybrid_cost_filter_test_2026-10-18.txt", "w").write("\n".join(LINES) + "\n")
