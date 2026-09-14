"""
Technical indicators for the setup/confidence score -- section 3/7 of the
index-first brief (VWAP, EMA, RSI, MACD, ADX, ATR, Volume).

RSI/MACD/ADX/ATR/EMA are imported directly from app.engines.signal_engine's
existing private helpers -- read-only reuse of already-implemented,
already-live-tested math, the SAME convention app.engines.regime_mtf already
uses (`from .signal_engine import _adx, _atr, _ema_series`). Nothing in
signal_engine.py is modified; this file only reads from it.

VWAP is reimplemented fresh (not signal_engine._vwap, which is a rolling
N-bar average, not session-anchored) because the standard intraday
definition resets at the session open -- a materially different indicator,
not a style preference.
"""
from __future__ import annotations

from ..engines.signal_engine import _adx, _atr, _ema_series, _macd, _rsi


def _num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _session_date(bar) -> str:
    return str(bar.get("t") or "")[:10]


def session_vwap(bars: list[dict]) -> float | None:
    """Anchored to the start of the LAST session present in `bars` (bars are
    already truncated to <= T by the caller, so "last session" is always
    "today so far")."""
    if not bars:
        return None
    session = _session_date(bars[-1])
    today = [b for b in bars if _session_date(b) == session]
    pv = vv = 0.0
    for b in today:
        h, l, c, v = _num(b.get("h")), _num(b.get("l")), _num(b.get("c")), _num(b.get("v")) or 0.0
        if None in (h, l, c):
            continue
        pv += (h + l + c) / 3 * v
        vv += v
    return round(pv / vv, 4) if vv > 0 else None


def snapshot(bars: list[dict]) -> dict:
    """One indicator read as of the LAST bar in `bars` (already truncated to
    <= T). Any indicator needing more history than is available returns
    None rather than a value computed on a too-short window."""
    clean = [(_num(b.get("h")), _num(b.get("l")), _num(b.get("c"))) for b in bars]
    clean = [row for row in clean if None not in row]
    if not clean:
        return {"status": "NO_BARS"}
    highs, lows, closes = (list(col) for col in zip(*clean))
    n = len(closes)
    ema20 = _ema_series(closes, 20)
    ema50 = _ema_series(closes, 50)
    rsi14 = _rsi(closes, 14)
    macd = _macd(closes)
    atr14 = _atr(highs, lows, closes, n, 14)
    adx = _adx(highs, lows, closes, n, 14)
    vwap = session_vwap(bars)
    vol = _num(bars[-1].get("v"))
    return {
        "status": "OK", "close": closes[-1], "ema20": round(ema20, 4) if ema20 else None,
        "ema50": round(ema50, 4) if ema50 else None,
        "rsi14": round(rsi14, 2) if rsi14 is not None else None,
        "macd": round(macd, 4) if macd is not None else None,
        "atr14": round(atr14, 4) if atr14 is not None else None,
        "adx": round(adx["adx"], 2) if adx else None,
        "vwap": vwap, "volume": vol,
        "above_vwap": (closes[-1] > vwap) if vwap is not None else None,
        "above_ema20": (closes[-1] > ema20) if ema20 is not None else None,
    }
