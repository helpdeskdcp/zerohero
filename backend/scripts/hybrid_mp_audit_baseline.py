"""Phase 1: reproduce the previously reported numbers (legacy defects switched back ON), then isolate each defect's effect.
Fixed-version primary: NIFTY 5m, fixed_params, cost 2 bps. Stored reference: data/research/hybrid_mp/fixed_all.txt."""
import sys

import numpy as np

sys.path.insert(0, ".")
import app.research_engines.hybrid_mp.engine as eng  # noqa: E402
from app.research_engines.hybrid_mp.engine import fixed_params, resample, run  # noqa: E402
from app.research_engines.smart_bos.data import load_1m  # noqa: E402
from app.research_engines.smart_bos.sim import summarize  # noqa: E402
from app.research_engines.smart_bos.splits import OOS, TRAIN, VALID, of_split  # noqa: E402

NEW_WD = eng.weekday_ist
OLD_WD = lambda t: (((np.asarray(t) + 19800) // 86400) + 4) % 7          # the defective formula (test-only)
b1 = load_1m("NIFTY", "INDEX")
chart = resample(b1, 5, 555, 930)
print(f"data: NIFTY INDEX 1m, {len(b1['t'])} bars, first={int(b1['t'][0])} last={int(b1['t'][-1])}; chart 5m bars={len(chart['t'])}")
rows = []
for label, wd, ties in (("LEGACY (weekday off-by-one + pivot ties=allow)", OLD_WD, "allow"), ("fix pivot tie rule only (TradingView rule)", OLD_WD, "tv"),
                        ("fix weekday only", NEW_WD, "allow"), ("BOTH FIXED (current engine)", NEW_WD, "tv")):
    eng.weekday_ist = wd
    tr, st = run(chart, fixed_params(chart_tf=5, pivot_ties=ties), b1=b1)
    print(f"\n[{label}] trades={len(tr)}")
    for name, sp in (("TRAIN", TRAIN), ("VALID", VALID), ("OOS", OOS)):
        sub = of_split(tr, sp)
        g, n2 = summarize(sub, cost_bps=0, boot=600), summarize(sub, cost_bps=2, boot=600)
        print(f"   {name:5s} n={g['n']:5d} gross meanR={g['mean_r']:+.3f} CI=({g['ci95_r'][0]:+.3f},{g['ci95_r'][1]:+.3f})  net2bps={n2['mean_r']:+.3f}  win={g['win']*100:.1f}%")
eng.weekday_ist = NEW_WD
