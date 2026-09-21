"""
Phase 6 -- regime classification for research events. Deterministic, from
real OHLC bars only (the same shape app.reverse_engineering.capture.
index_window's slots use: {"ts","o","h","l","c","v"}). Self-contained
(no import of app.behavior_engine / app.engines.state_classifier) so this
research package stays fully disconnected from the live decision path.

Defaults to UNKNOWN whenever there isn't enough real data to support a
call -- this module must never force a regime label onto sparse data.
"""
from __future__ import annotations

_MIN_BARS_FOR_TREND = 10
_MIN_BARS_FOR_ATR = 5

REGIMES = (
    "TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOLATILITY", "LOW_VOLATILITY",
    "BREAKOUT", "BREAKDOWN", "REVERSAL_ATTEMPT", "EXPIRY_DISTORTION", "UNKNOWN",
)


def _closes(bars: list[dict]) -> list[float]:
    return [b["c"] for b in bars if b and b.get("c") is not None]


def ema(values: list[float], period: int) -> float | None:
    """Standard EMA; None if fewer real points than `period`."""
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def atr(bars: list[dict], period: int = 14) -> float | None:
    """Average True Range from real bars only. None if fewer than
    `min(period, _MIN_BARS_FOR_ATR)` usable (o/h/l/c all present) bars."""
    usable = [b for b in bars if b and None not in (b.get("h"), b.get("l"), b.get("c"))]
    if len(usable) < _MIN_BARS_FOR_ATR:
        return None
    trs = []
    prev_close = usable[0]["c"]
    for b in usable[1:]:
        tr = max(b["h"] - b["l"], abs(b["h"] - prev_close), abs(b["l"] - prev_close))
        trs.append(tr)
        prev_close = b["c"]
    if not trs:
        return None
    n = min(period, len(trs))
    return sum(trs[-n:]) / n


def classify_regime(bars: list[dict], *, is_expiry_day: bool = False) -> dict:
    """`bars` must be chronological (oldest first), each a dict with at
    least o/h/l/c or None (missing slots from capture.index_window are
    filtered out, they don't count as evidence). Returns
    {"regime": ..., "evidence": {...}} -- evidence lets a caller see WHY,
    never just a bare label."""
    usable = [b for b in bars if b]
    closes = _closes(usable)

    if len(closes) < _MIN_BARS_FOR_TREND:
        return {"regime": "UNKNOWN", "evidence": {"reason": "fewer than "
                f"{_MIN_BARS_FOR_TREND} real closes", "n_bars": len(closes)}}

    fast = ema(closes, min(5, len(closes)))
    slow = ema(closes, min(_MIN_BARS_FOR_TREND, len(closes)))
    a = atr(usable)
    last_close = closes[-1]

    volatility = "UNKNOWN"
    atr_pct = None
    if a is not None and last_close:
        atr_pct = abs(a) / abs(last_close) * 100.0
        # Same 0.5/3.0 thresholds this codebase already uses elsewhere
        # (app.behavior_engine) for HIGH/LOW volatility buckets -- kept
        # identical so a cross-reference between the two doesn't look like
        # an arbitrary second scale.
        volatility = "HIGH" if atr_pct > 3.0 else ("LOW" if atr_pct < 0.5 else "NORMAL")

    if is_expiry_day and volatility == "HIGH":
        return {"regime": "EXPIRY_DISTORTION",
               "evidence": {"is_expiry_day": True, "atr_pct": atr_pct}}

    recent_high = max(b["h"] for b in usable if b.get("h") is not None)
    recent_low = min(b["l"] for b in usable if b.get("l") is not None)
    band = recent_high - recent_low

    evidence = {"fast_ema": fast, "slow_ema": slow, "atr_pct": atr_pct,
               "last_close": last_close, "recent_high": recent_high, "recent_low": recent_low}

    if fast is None or slow is None:
        return {"regime": "UNKNOWN", "evidence": {**evidence, "reason": "EMA undefined"}}

    if last_close >= recent_high and fast > slow:
        return {"regime": "BREAKOUT", "evidence": evidence}
    if last_close <= recent_low and fast < slow:
        return {"regime": "BREAKDOWN", "evidence": evidence}

    trend_up = fast > slow
    trend_down = fast < slow
    # A reversal attempt: last 3 closes moving opposite to the established
    # EMA trend -- an early, unconfirmed signal, not a new trend call.
    if len(closes) >= 3:
        last3_dir = closes[-1] - closes[-3]
        if trend_up and last3_dir < 0:
            return {"regime": "REVERSAL_ATTEMPT", "evidence": evidence}
        if trend_down and last3_dir > 0:
            return {"regime": "REVERSAL_ATTEMPT", "evidence": evidence}

    if volatility == "HIGH":
        return {"regime": "HIGH_VOLATILITY", "evidence": evidence}
    if volatility == "LOW":
        return {"regime": "LOW_VOLATILITY", "evidence": evidence}

    if band > 0 and last_close and abs(fast - slow) / abs(last_close) < 0.001:
        return {"regime": "RANGE", "evidence": evidence}

    if trend_up:
        return {"regime": "TREND_UP", "evidence": evidence}
    if trend_down:
        return {"regime": "TREND_DOWN", "evidence": evidence}
    return {"regime": "RANGE", "evidence": evidence}
