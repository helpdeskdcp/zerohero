"""
Order-flow module API — Phase 1 (Volume Profile + Market Profile / TPO).
Read-only. No order path, no broker calls, no writes.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from . import service

router = APIRouter(prefix="/api/orderflow", tags=["orderflow"])


@router.get("/sessions")
def api_of_sessions(symbol: str = Query(...), tf: str = Query("5m"),
                    limit: int = Query(30, ge=1, le=120)):
    """IST session dates that have captured bars for `symbol` (newest first) —
    for a dashboard date picker."""
    return {"symbol": symbol.upper(), "tf": tf,
            "sessions": service.available_sessions(symbol, tf, limit)}


@router.get("/volume-profile")
def api_volume_profile(symbol: str = Query(...), date: str = Query(..., description="YYYY-MM-DD (IST); comma-list for composite"),
                       tf: str = Query("5m"), tick_size: Optional[float] = Query(None, gt=0),
                       value_pct: float = Query(0.70, gt=0.0, lt=1.0)):
    return service.profile(symbol, date, tf=tf, tick_size=tick_size,
                           value_pct=value_pct, which="volume")


@router.get("/market-profile")
def api_market_profile(symbol: str = Query(...), date: str = Query(..., description="YYYY-MM-DD (IST); comma-list for composite"),
                       tf: str = Query("5m"), tick_size: Optional[float] = Query(None, gt=0),
                       tpo_minutes: int = Query(30, ge=5, le=120),
                       value_pct: float = Query(0.70, gt=0.0, lt=1.0)):
    return service.profile(symbol, date, tf=tf, tick_size=tick_size,
                           tpo_minutes=tpo_minutes, value_pct=value_pct, which="market")


@router.get("/profile")
def api_profile(symbol: str = Query(...), date: str = Query(..., description="YYYY-MM-DD (IST); comma-list for composite"),
                tf: str = Query("5m"), tick_size: Optional[float] = Query(None, gt=0),
                tpo_minutes: int = Query(30, ge=5, le=120),
                value_pct: float = Query(0.70, gt=0.0, lt=1.0)):
    """Volume Profile + Market Profile + session VWAP in one call (dashboard)."""
    return service.profile(symbol, date, tf=tf, tick_size=tick_size,
                           tpo_minutes=tpo_minutes, value_pct=value_pct, which="both")
