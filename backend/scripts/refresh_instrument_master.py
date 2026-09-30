#!/usr/bin/env python3
"""Refresh app.instruments' cached AngelOne scrip master (data/instrument_
master.json). Read-only: downloads AngelOne's public scrip-master URL, no
auth, no broker session, never touches a live order or a trading table.

Why this exists: the cache is only ever populated once, on first use
(instruments.master_rows() only refreshes when the file is MISSING, never
by age) -- found live 2026-09-30, the cached file was over a month stale
(last written 29 Aug), so list_nse_option_expiries("NIFTY")'s AUTO
resolution silently skipped every currently-listed near-term weekly
expiry (29SEP/06OCT/13OCT/20OCT2026 were all simply absent from the stale
cache) and resolved to 27OCT2026 instead -- a real expiry, but not the one
actually being captured/traded, so the default Option Chain view showed a
dead chain (no bid/ask, no Greeks, no OI-change) for NIFTY/BANKNIFTY/
FINNIFTY/MIDCPNIFTY's default AUTO selection."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import instruments  # noqa: E402


def main() -> int:
    result = instruments.refresh_master()
    print(result)
    return 0 if result.get("status") == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
