"""Hybrid MP v13 (fixed_params, NIFTY 5m): effect of the 1m source choice on the recent period. READ-ONLY, research only, no wiring to any
live engine. A = legacy load_1m (all sources, first row), B = load_1m(prefer=ANGELONE_CANDLES). Parameters/costs are NOT tuned:
same fixed_params + 0/2 bps as data/research/hybrid_mp/audit/baseline_reproduction.txt. Window C = 2026-09-01.. (the only period where
Angel 1m exists; tiny sample -> descriptive only, no go/no-go claim).  Usage: python scripts/hybrid_mp_angel_prefer_backtest.py"""
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import fixed_params, resample, run  # noqa: E402
from app.research_engines.smart_bos.data import load_1m  # noqa: E402
from app.research_engines.smart_bos.sim import summarize  # noqa: E402
from app.research_engines.smart_bos.splits import OOS, of_split  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
W0 = int(dt.datetime(2026, 9, 1, tzinfo=IST).timestamp())


def go(label, **kw):
    b1 = load_1m("NIFTY", "INDEX", start="2017-01-01", end="2027-01-01", use_cache=False, **kw)
    chart = resample(b1, 5, 555, 930)
    tr, _ = run(chart, fixed_params(chart_tf=5), b1=b1)
    ent = [int(chart["t"][t.entry_i]) for t in tr]
    print(f"\n[{label}] 1m bars={len(b1['t'])} 5m bars={len(chart['t'])} trades={len(tr)} last 5m bar={dt.datetime.fromtimestamp(int(chart['t'][-1]), IST):%Y-%m-%d %H:%M}")
    for name, sub in (("OOS 2024+", of_split(tr, OOS)), ("WINDOW 2026-09-01+", [t for t, e in zip(tr, ent) if e >= W0])):
        if not sub:
            print(f"   {name}: n=0"); continue
        g, n2 = summarize(sub, cost_bps=0, boot=600), summarize(sub, cost_bps=2, boot=600)
        print(f"   {name:19s} n={g['n']:4d} gross meanR={g['mean_r']:+.3f} CI=({g['ci95_r'][0]:+.3f},{g['ci95_r'][1]:+.3f}) net2bps={n2['mean_r']:+.3f} win={g['win']*100:.1f}%")
    return {(e, t.side, round(t.entry, 2)) for t, e in zip(tr, ent) if e >= W0}


a = go("A legacy loader (first row of any source)")
b = go("B prefer ANGELONE_CANDLES", prefer="ANGELONE_CANDLES")
print(f"\nwindow trades A={len(a)} B={len(b)}  identical={len(a & b)}  only-A={len(a - b)}  only-B={len(b - a)}")
print("NOTE: window sample is tiny and 1m has gaps (see CANDLE_PARITY_REPORT.md); no edge/NO-GO claim is made from it. Baseline verdict unchanged: NO-GO.")
