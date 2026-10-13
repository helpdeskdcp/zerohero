#!/usr/bin/env python3
"""Report on data/renko_shadow.db tick stream (read-only). See app/autoscalp/renko_shadow_analysis.py."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from app.autoscalp import renko_shadow as RS  # noqa: E402
from app.autoscalp import renko_shadow_analysis as A  # noqa: E402

SHADOW = os.path.abspath(os.path.join(HERE, "..", "data", "renko_shadow.db"))
HIST = os.path.abspath(os.path.join(HERE, "..", "data", "market_history.db"))


def main():
    if not os.path.exists(SHADOW):
        print("no shadow DB yet (logger not started or no bar observed)")
        return 0
    ticks = A.load_ticks(SHADOW)
    print(f"tick rows: {len(ticks)}")
    bars = {}
    for sym in sorted({t['symbol'] for t in ticks}):
        ts = [A._epoch(t["last_bar_ts"]) for t in ticks if t["symbol"] == sym]
        bars[sym] = A.load_bars(HIST, sym, RS.SERIES[sym][0], min(ts), max(ts) + 3600)
        print(f"  {sym}: {len(ts)} observations, {len(bars[sym])} history bars")
    for h, r in A.analyse(ticks, bars).items():
        print(f"H={h}m", {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
