#!/usr/bin/env python3
"""FORWARD (true out-of-sample) record of the corrected Hybrid MP variant. Frozen on 2026-10-18: only trades on sessions AFTER the freeze date count, so nothing
here has been seen when the rule was chosen. Deterministic engine re-run on the captured 1m bars (histcap keeps capturing), judged by the net-of-cost promotion
gate once enough trades exist (INSUFFICIENT_SAMPLE until then). READ-ONLY / research; appends one report line per run.  Usage: python scripts/hybrid_mp_forward_report.py"""
import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, ".")
from app.hybrid_orderflow.policy import load_policy  # noqa: E402
from app.research_engines.hybrid_mp.engine import corrected_params, resample, run  # noqa: E402
from app.research_engines.smart_bos.data import load_1m  # noqa: E402
from app.strategy_lab import edge_scan as ES  # noqa: E402
from app.strategy_lab import gate as G  # noqa: E402

FREEZE = dt.date(2026, 10, 18)
FREEZE_DAY = (FREEZE - dt.date(1970, 1, 1)).days
COST = 2.0
PR = load_policy()["promotion"]
OUT = "data/ops/hybrid_mp_forward_report.txt"
lines = [f"=== {dt.datetime.now():%Y-%m-%d %H:%M} IST  corrected Hybrid MP, trades on sessions after {FREEZE} (cost {COST} bps) ==="]
allg, alld = [], []
for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
    try:
        b1 = load_1m(sym, "INDEX", start="2026-08-01", end="2027-12-31", prefer="ANGELONE_CANDLES", use_cache=False)
        tr, _ = run(resample(b1, 5, 555, 930), corrected_params(chart_tf=5), b1=b1)
    except Exception as e:
        lines.append(f"  {sym}: not evaluated ({type(e).__name__}: {e})")
        continue
    fw = [t for t in tr if t.day > FREEZE_DAY]
    g = np.array([t.bps for t in fw])
    last = dt.date(1970, 1, 1) + dt.timedelta(days=int(max(t.day for t in tr))) if tr else None
    lines.append(f"  {sym}: forward trades {len(fw)}" + (f", gross {g.mean():+.2f} bps, net@2 {g.mean() - COST:+.2f}, net@4 {g.mean() - 4.0:+.2f}, net@6.3 {g.mean() - 6.3:+.2f} bps (REALISTIC NIFTY futures round trip since 2026-04-01 is ~6.3 bps: STT 0.05% on the sell side, see audit sec. 21), win@2 {np.mean(g - COST > 0) * 100:.0f}%" if len(fw) else "") + f"  (latest trade day {last})")
    allg += list(g); alld += [t.day for t in fw]
order = np.argsort(alld, kind="stable") if alld else []
res = G.evaluate(np.array(allg)[order] if alld else [], np.array(alld)[order] if alld else [], COST, PR)
lines.append(f"  POOLED: n={res['n']} distinct days={res.get('distinct_days')} net={res.get('net_mean_bps')} -> gate {res['verdict']} {res['failed']}")
os.makedirs("data/ops", exist_ok=True)
with open(OUT, "a") as f:
    f.write("\n".join(lines) + "\n")
print("\n".join(lines))
