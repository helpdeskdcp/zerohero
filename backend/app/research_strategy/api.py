"""
Read-only API for the research strategies. No order path, no live-signal
emission -- these endpoints only read data and run a backtest / scan.
"""
from __future__ import annotations

from fastapi import APIRouter

from . import hcr

router = APIRouter(prefix="/api/research", tags=["research-strategy"])


@router.get("/hcr/summary")
def hcr_summary():
    """High-Conviction Runner backtest summary (from the committed CSV)."""
    return hcr.backtest_summary()


@router.get("/hcr/signals")
def hcr_signals(limit: int = 25, scan_days: int = 12):
    """Recent historical HCR signals + a live read-only scan of the last
    `scan_days` NIFTY 5m sessions (incl. today if captured)."""
    return {
        "recent_backtest_signals": hcr.recent_signals(limit=max(1, min(100, limit))),
        "live_scan": hcr.live_scan(n_days=max(1, min(30, scan_days))),
    }
