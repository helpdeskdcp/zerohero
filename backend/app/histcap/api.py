"""Read-only HTTP surface for the historical capture store (status + retrieval)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

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
            as_of: Optional[str] = None, limit: int = Query(2000, le=20000)):
    """Look-ahead-safe: rows with bar_start <= as_of (UTC ISO), oldest first."""
    return hist_store().get_candles(symbol, tf, as_of=as_of, kind=kind, limit=limit)


@router.get("/quotes")
def quotes(symbol: str, kind: Optional[str] = None, expiry: Optional[str] = None,
           strike: Optional[float] = None, option_type: Optional[str] = None,
           as_of: Optional[str] = None, limit: int = Query(2000, le=20000)):
    return hist_store().get_quotes(symbol, as_of=as_of, kind=kind, expiry=expiry,
                                   strike=strike, option_type=option_type, limit=limit)


@router.get("/greeks")
def greeks(underlying: str, expiry: Optional[str] = None, strike: Optional[float] = None,
           option_type: Optional[str] = None, as_of: Optional[str] = None,
           limit: int = Query(2000, le=20000)):
    return hist_store().get_greeks(underlying, as_of=as_of, expiry=expiry,
                                   strike=strike, option_type=option_type, limit=limit)
