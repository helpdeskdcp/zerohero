"""
Path-correct target-distance sensitivity sweep, reusing
scripts/natgas_futures_backtest.py's exact _simulate_outcome() and the
persisted real 5m bars + raw signals from that run. Re-simulates (not
approximates from stored MFE/MAE, which would ignore path order) at several
candidate t1_atr values, holding sl_atr/trail_atr/max_hold_sec fixed at
NATGAS's actual production values.
"""
import json
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).parents[1]))
import scripts.natgas_futures_backtest as ngb

OUT_DIR = ngb.OUT_DIR

all_5m = json.load(open(OUT_DIR / "natgas_futures_5m_bars.json"))
signals = json.load(open(OUT_DIR / "natgas_futures_signals.json"))
signals = [s for s in signals if not s["blocked"]]

CANDIDATES = [0.6, 0.8, 1.0, 1.2, 1.4, 1.7]

print(f"n signals (live-filter-passing) = {len(signals)}\n")
print(f"{'t1_atr':>7} {'T1_touch%':>10} {'win%':>7} {'expectancy':>11} {'PF':>7}")
for t1 in CANDIDATES:
    ngb.T1_ATR = t1
    pts, touches = [], 0
    for s in signals:
        if s["atr"] is None or s["atr"] <= 0:
            continue
        future = all_5m[s["bar_index"] + 1:]
        o = ngb._simulate_outcome(s["direction"], s["entry"], s["atr"], future)
        if o is None:
            continue
        pts.append(o["points"])
        if o["exit_reason"] == "TARGET":
            touches += 1
    win = [p for p in pts if p > 0]
    loss = [p for p in pts if p < 0]
    pf = sum(win) / abs(sum(loss)) if loss else float("inf")
    marker = "  <- current" if abs(t1 - 1.7) < 1e-9 else ""
    print(f"{t1:>7.1f} {touches/len(pts)*100:>9.1f}% {len(win)/len(pts)*100:>6.1f}% "
          f"{mean(pts):>11.4f} {pf:>7.3f}{marker}")
