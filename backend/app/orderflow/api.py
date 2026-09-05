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


@router.get("/smart-money")
def api_smart_money(symbol: str = Query(...), date: str = Query(..., description="YYYY-MM-DD (IST); comma-list to scan several"),
                    tf: str = Query("5m"),
                    volume_mult: float = Query(2.0, gt=1.0, le=20.0,
                                               description="spike = bar volume >= this x session avg"),
                    rr: float = Query(3.0, gt=0.0, le=10.0),
                    stop_frac: float = Query(1.0, gt=0.0, le=1.0,
                                             description="stop distance as a fraction of the spike candle range; <1 = tighter stop"),
                    trail: bool = Query(False, description="trail the stop that same distance behind the best price after entry"),
                    sig_filter: str = Query("none", pattern="^(none|candle_dir|strong_body)$",
                                            description="none=both sides; candle_dir=only the spike candle's direction; strong_body=candle_dir + body>=0.5*range")):
    """Volume-spike breakout setups: BUY above the spike candle's high / SELL
    below its low, stop `stop_frac` x the candle range away from entry, target
    at `rr` x that stop distance, with a same-session forward-walked outcome.
    Read-only; ~5m bar granularity."""
    return service.smart_money(symbol, date, tf=tf, volume_mult=volume_mult, rr=rr,
                               stop_frac=stop_frac, trail=trail, sig_filter=sig_filter)


@router.get("/backtest")
def api_backtest(symbol: str = Query(...), tf: str = Query("5m"),
                 volume_mult: float = Query(2.0, gt=1.0, le=20.0),
                 rr: float = Query(3.0, gt=0.0, le=10.0),
                 stop_frac: float = Query(1.0, gt=0.0, le=1.0,
                                          description="stop distance as a fraction of the spike candle range; <1 = tighter stop (target follows at rr x that distance)"),
                 trail: bool = Query(False, description="trail the stop that same distance behind the best price after entry"),
                 sig_filter: str = Query("none", pattern="^(none|candle_dir|strong_body)$",
                                         description="none=both sides; candle_dir=only the spike candle's direction; strong_body=candle_dir + body>=0.5*range"),
                 sessions: Optional[int] = Query(None, ge=1, le=400,
                                                 description="most-recent N captured sessions; omit = all")):
    """Runs the smart-money engine over every captured session and aggregates:
    win/loss counts, win rate, gross win points, gross loss (SL-hit) points,
    net, expectancy, profit factor, max drawdown -- overall, per side, per
    session -- plus the full per-trade list (entry / stop / target / result /
    points / the exact winning-target or SL-hit price). `reliable` is False
    below 20 resolved trades."""
    return service.backtest(symbol, tf=tf, volume_mult=volume_mult, rr=rr,
                            stop_frac=stop_frac, trail=trail, sig_filter=sig_filter,
                            sessions=sessions)
