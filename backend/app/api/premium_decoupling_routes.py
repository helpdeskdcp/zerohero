"""Premium-vs-Spot Decoupling -- read-only. GET computes the current window
live from already-captured ticks and logs it (idempotent); no order path,
no gating, nothing else in the app consults this."""
from fastapi import APIRouter

from ..premium_decoupling import engine, store

router = APIRouter(prefix="/api/premium-decoupling", tags=["premium-decoupling"])

_WATCHLIST = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX")


@router.get("/{underlying}")
def api_current(underlying: str, window_sec: int = 300):
    result = engine.compute_window(underlying.upper(), window_sec=window_sec)
    store.log_result(result)
    return result


@router.get("/{underlying}/history")
def api_history(underlying: str, limit: int = 50):
    rows = store.recent(underlying.upper(), limit=limit)
    return {"underlying": underlying.upper(), "count": len(rows), "rows": rows}


@router.get("/{underlying}/summary")
def api_summary(underlying: str, since_ts: str | None = None):
    counts = store.summary_counts(underlying.upper(), since_ts=since_ts)
    return {"underlying": underlying.upper(), "since_ts": since_ts, "counts": counts}


@router.get("")
def api_all_current(window_sec: int = 300):
    out = {}
    for sym in _WATCHLIST:
        result = engine.compute_window(sym, window_sec=window_sec)
        store.log_result(result)
        out[sym] = result
    return out
