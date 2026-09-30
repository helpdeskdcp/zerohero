"""Read-only HTTP surface for the historical capture store (status + retrieval)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from .cross_broker import compare_captured_quotes
from .store import hist_store

router = APIRouter(prefix="/api/histcap", tags=["histcap"])

_worker = None   # set by app.main at startup


def bind_worker(w):
    global _worker
    _worker = w


@router.get("/status")
def status():
    if _worker is not None:
        return _worker.status()
    return {"running": False, "store": hist_store().summary()}


@router.get("/runs")
def runs(limit: int = Query(50, le=500)):
    return hist_store().runs(limit)


@router.get("/candles")
def candles(symbol: str, tf: str = "5m", kind: str = "FUTURE",
            as_of: str | None = None, limit: int = Query(2000, le=20000)):
    """Look-ahead-safe: rows with bar_start <= as_of (UTC ISO), oldest first."""
    return hist_store().get_candles(symbol, tf, as_of=as_of, kind=kind, limit=limit)


@router.get("/quotes")
def quotes(symbol: str, kind: str | None = None, expiry: str | None = None,
           strike: float | None = None, option_type: str | None = None,
           as_of: str | None = None, limit: int = Query(2000, le=20000)):
    return hist_store().get_quotes(symbol, as_of=as_of, kind=kind, expiry=expiry,
                                   strike=strike, option_type=option_type, limit=limit)


@router.get("/greeks")
def greeks(underlying: str, expiry: str | None = None, strike: float | None = None,
           option_type: str | None = None, as_of: str | None = None,
           limit: int = Query(2000, le=20000)):
    return hist_store().get_greeks(underlying, as_of=as_of, expiry=expiry,
                                   strike=strike, option_type=option_type, limit=limit)


@router.get("/cross-broker")
def cross_broker(symbol: str, expiry: str, since: str | None = None,
                 limit: int = Query(5000, le=20000)):
    """MCP 3.3 priority #5, item 4: real, measured agreement between the
    most-recently-captured ANGELONE_QUOTE_FULL and DHAN_CHAIN rows for
    `symbol`/`expiry` -- never manufactured equivalence (see
    compare_captured_quotes()'s own docstring)."""
    return compare_captured_quotes(symbol, expiry, since=since, limit=limit)
