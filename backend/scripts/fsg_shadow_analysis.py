"""
On-demand CLI report for app.signal_gate.shadow_analysis.build_shadow_report:
joins every real gate verdict (fsg_shadow_log) against the trade's REAL
resolved outcome (trade_exit_outcomes) -- the actual question shadow mode
exists to answer: would filtering to APPROVED-only have improved real
results, using real paper trades, not a backtest reconstruction.

Run this periodically as the shadow-mode sample accumulates; it does not
modify any config or write anything back to the live system.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.signal_gate.shadow_analysis import build_shadow_report


def main():
    report = build_shadow_report(limit=20000)
    print(json.dumps(report, indent=2))
    if report.get("n_resolved", 0) == 0:
        return
    out_path = Path(__file__).parents[1] / "data" / "research" / "signal_gate" / "shadow_analysis_latest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nsaved to {out_path}")


if __name__ == "__main__":
    main()
