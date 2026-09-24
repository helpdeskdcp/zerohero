#!/usr/bin/env python3
"""
premium_decoupling_scan.py -- READ-ONLY.

Meant to run on a cron every 5 min during market hours (mirrors
scripts/stale_feed_watchdog.py's pattern). For each watched index, computes
the current spot-vs-ATM-CE/PE-premium classification from already-captured
ticks (market_history.db) and appends it to the shadow log
(data/premium_decoupling.db) via app.premium_decoupling.store. Off-hours,
compute_window() naturally returns INSUFFICIENT_DATA and store.log_result()
skips it -- so running this around the clock is harmless, just a no-op
outside market hours.

Never calls the broker, never writes to market_history.db or chanakya.db,
never gates or feeds any live decision. Exit code is always 0 (a scan gap
is not an alertable failure the way a stale feed is).

Usage:
  venv/bin/python scripts/premium_decoupling_scan.py                  # default watchlist, 5-min window
  venv/bin/python scripts/premium_decoupling_scan.py --window-sec 900
  venv/bin/python scripts/premium_decoupling_scan.py --symbols NIFTY,BANKNIFTY
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.premium_decoupling import engine, store  # noqa: E402

_WATCHLIST = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX")


def main() -> int:
    ap = argparse.ArgumentParser(description="premium-vs-spot decoupling scan (read-only, shadow)")
    ap.add_argument("--window-sec", type=int, default=300)
    ap.add_argument("--symbols", default=",".join(_WATCHLIST))
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    for sym in symbols:
        result = engine.compute_window(sym, window_sec=args.window_sec)
        row_id = store.log_result(result)
        tag = "logged" if row_id else result["classification"]
        print(f"{sym}: {result['classification']} ({tag})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
