#!/usr/bin/env python3
"""
optionchain_qualified_scan.py -- READ-ONLY.

Meant to run on a cron every 5 min during market hours. Calls
app.optionchain.qualified_watch.scan_and_alert() -- see that module's
docstring for the full contract (structure-only gate, dedup, Telegram).

Usage:
  venv/bin/python scripts/optionchain_qualified_scan.py
  venv/bin/python scripts/optionchain_qualified_scan.py --symbols NIFTY,SENSEX
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.optionchain.qualified_watch import WATCHLIST, scan_and_alert  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="option-chain QUALIFIED-verdict watch (read-only)")
    ap.add_argument("--symbols", default=",".join(WATCHLIST))
    args = ap.parse_args()
    symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())

    out = scan_and_alert(symbols)
    for sym, r in out["results"].items():
        print(f"{sym}: {r}")
    print(f"TOTAL new alerts: {len(out['alerted'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
