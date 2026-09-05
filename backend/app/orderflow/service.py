"""
Order-flow module — Phase 1: Volume Profile + Market Profile (TPO).

Reads captured OHLCV bars via `market_hub` (the single owner of
market_history.db), runs the pure math in `profile.py`, and adds session VWAP
via the existing shared `signal_engine._vwap` (not a re-implementation). A
small TTL cache keeps repeat dashboard polls from re-reading/re-computing.

No broker calls, no writes, not on any trading hot path.
"""
from __future__ import annotations

import threading
import time

from .. import market_hub
from ..engines.signal_engine import _vwap
from . import profile as _p
from . import smart_money as _sm
from . import backtest as _bt

_CACHE: dict = {}
_TTL = 30.0          # a completed session is immutable; today's grows slowly
_lock = threading.Lock()


def _cache_get(key):
    with _lock:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < _TTL:
            return hit[1]
    return None


def _cache_put(key, val):
    with _lock:
        _CACHE[key] = (time.time(), val)
        if len(_CACHE) > 256:
            for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:64]:
                _CACHE.pop(k, None)


def _session_vwap(bars: list):
    H = [b["h"] for b in bars if b.get("h") is not None]
    L = [b["l"] for b in bars if b.get("l") is not None]
    C = [b["c"] for b in bars if b.get("c") is not None]
    V = [b["v"] for b in bars if b.get("v") is not None]
    n = min(len(H), len(L), len(C), len(V))
    if n < 2 or not any(V[:n]):
        return None
    return _vwap(H[:n], L[:n], C[:n], V[:n], n)


def available_sessions(symbol: str, tf: str = "5m", limit: int = 30) -> list:
    return market_hub.session_dates(symbol, tf=tf, limit=limit)


def smart_money(symbol: str, session_date: str, *, tf: str = "5m",
                volume_mult: float = 2.0, rr: float = 3.0, stop_frac: float = 1.0,
                trail: bool = False, sig_filter: str = "none") -> dict:
    """Volume-spike breakout setups for one IST session (or comma-list for a
    multi-session scan). Read-only over captured bars."""
    dates = [d.strip() for d in str(session_date or "").split(",") if d.strip()]
    key = ("SM", symbol.upper(), tuple(dates), tf, volume_mult, rr, stop_frac,
           bool(trail), sig_filter)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    bars: list = []
    for d in dates:
        bars.extend(market_hub.session_bars(symbol, d, tf=tf))
    bars.sort(key=lambda b: str(b.get("bar_start") or ""))
    out = _sm.smart_money_setups(bars, volume_mult=volume_mult, rr=rr, stop_frac=stop_frac,
                                 trail=trail, sig_filter=sig_filter)
    out["symbol"] = symbol.upper()
    out["sessions"] = dates
    out["tf"] = tf
    out["bar_count"] = len(bars)
    _cache_put(key, out)
    return out


def backtest(symbol: str, *, tf: str = "5m", volume_mult: float = 2.0, rr: float = 3.0,
             stop_frac: float = 1.0, trail: bool = False, sig_filter: str = "none",
             basis: str = "index", premium_stop_pct: float = 0.0,
             sessions: int | None = None) -> dict:
    """Aggregate the smart-money engine's outcomes over the captured history.
    `basis` in {index, premium} -- premium re-prices on the captured ATM option;
    `premium_stop_pct` adds an optional hard stop on the option premium.
    Read-only; a 30s cache (a completed session's backtest is immutable)."""
    key = ("BT", symbol.upper(), tf, volume_mult, rr, stop_frac,
           bool(trail), sig_filter, basis, round(float(premium_stop_pct or 0.0), 4), sessions)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    out = _bt.backtest(symbol, tf=tf, volume_mult=volume_mult, rr=rr, stop_frac=stop_frac,
                       trail=trail, sig_filter=sig_filter, basis=basis,
                       premium_stop_pct=premium_stop_pct, sessions=sessions)
    _cache_put(key, out)
    return out


def profile(symbol: str, session_date: str, *, tf: str = "5m",
            tick_size: float | None = None, tpo_minutes: int = 30,
            value_pct: float = 0.70, which: str = "both") -> dict:
    """`which` in {"volume", "market", "both"}. `session_date` may be a single
    IST date (YYYY-MM-DD) or a comma-list for a composite profile."""
    dates = [d.strip() for d in str(session_date or "").split(",") if d.strip()]
    key = (symbol.upper(), tuple(dates), tf, tick_size, tpo_minutes, value_pct, which)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    bars: list = []
    for d in dates:
        bars.extend(market_hub.session_bars(symbol, d, tf=tf))
    bars.sort(key=lambda b: str(b.get("bar_start") or ""))

    out = {"symbol": symbol.upper(), "sessions": dates, "tf": tf,
           "bar_count": len(bars), "composite": len(dates) > 1}
    if not bars:
        out["status"] = "NO_DATA"
        out["reason"] = f"no captured {tf} bars for {symbol.upper()} on {dates}"
        _cache_put(key, out)
        return out

    out["status"] = "OK"
    out["vwap"] = _session_vwap(bars)
    if which in ("volume", "both"):
        out["volume_profile"] = _p.volume_profile(
            bars, symbol=symbol, tick_size=tick_size, value_pct=value_pct)
    if which in ("market", "both"):
        out["market_profile"] = _p.market_profile(
            bars, symbol=symbol, tick_size=tick_size,
            tpo_minutes=tpo_minutes, value_pct=value_pct)
    _cache_put(key, out)
    return out
