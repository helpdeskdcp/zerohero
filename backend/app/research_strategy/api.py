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
        "forward_test": _forward_test_summary(),
    }


def _forward_test_summary() -> dict:
    """Read-only tally of the forward-test log (data/hcr_forward_test.jsonl)."""
    import json
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "data", "hcr_forward_test.jsonl")
    if not os.path.exists(p):
        return {"sessions_observed": 0, "fired": 0, "graded": 0}
    recs = []
    for ln in open(p):
        try:
            recs.append(json.loads(ln))
        except ValueError:
            pass
    fired = [r for r in recs if r.get("hcr_fired")]
    graded = [r for r in fired if "day_blended_R" in r]
    greens = sum(1 for r in graded if r.get("day_close_green"))
    return {
        "sessions_observed": len(recs), "fired": len(fired), "graded": len(graded),
        "close_green": greens,
        "net_blended_R": round(sum(r["day_blended_R"] for r in graded), 3) if graded else None,
        "last_session": recs[-1]["session"] if recs else None,
        "last_result": ("no fire" if recs and not recs[-1].get("hcr_fired")
                        else (recs[-1].get("day_blended_R") if recs else None)),
        "note": "forward-test in progress -- a verdict needs dozens of fired signals across >=2 regimes",
    }
