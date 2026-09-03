"""
Causal price-action deriver for the replay (slice 5/6).

`MathematicalConfluenceEngine.evaluate()` consumes `breakout_state`,
`retest_state`, `reversal_candidate` and `candle_signals` as CALLER-SUPPLIED
inputs (see MATHEMATICAL_CONFLUENCE.md "Known limitations" — a full price-action
sub-engine is future work). Live callers don't derive them yet; the replay needs
them or the engine can never leave WAIT.

Every function here is a pure function of `bars[:k]` that have already been
truncated to bars closed at/ before the replay timestamp, so there is no
look-ahead. The rules are simple, HEURISTIC and UNCALIBRATED — they exist to
exercise the pipeline over real captured data, not as a validated detector.
"""
from __future__ import annotations

_NEAR = 0.0012      # 0.12% "at the level" band


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def derive(bars: list[dict], *, pdh: float | None, pdl: float | None,
           day_high: float | None, day_low: float | None) -> dict:
    """-> {breakout_state, retest_state, reversal_candidate, candle_signals}."""
    out = {"breakout_state": None, "retest_state": None,
           "reversal_candidate": False, "candle_signals": []}
    cs = [b for b in (bars or []) if _f(b.get("close")) is not None]
    if len(cs) < 4:
        return out
    closes = [_f(b["close"]) for b in cs]
    highs = [_f(b.get("high")) or closes[i] for i, b in enumerate(cs)]
    lows = [_f(b.get("low")) or closes[i] for i, b in enumerate(cs)]
    last, prev = closes[-1], closes[-2]
    look = closes[-4:-1]      # the 3 bars before the current one

    # -------- breakout / breakdown vs the previous day's range ----------
    if pdh:
        if last > pdh and min(look) <= pdh:
            out["breakout_state"] = "BREAKOUT_CONFIRMED"
        elif abs(last - pdh) <= pdh * _NEAR and last > prev:
            out["breakout_state"] = "BREAKOUT_WATCH"
    if pdl and out["breakout_state"] is None:
        if last < pdl and max(look) >= pdl:
            out["breakout_state"] = "BREAKDOWN_CONFIRMED"
        elif abs(last - pdl) <= pdl * _NEAR and last < prev:
            out["breakout_state"] = "BREAKDOWN_WATCH"

    # -------- retest: broke a level, dipped back to it, closed back through
    if out["breakout_state"] == "BREAKOUT_CONFIRMED" and pdh:
        dipped = any(l <= pdh * (1 + _NEAR) for l in lows[-3:])
        if dipped and last > pdh:
            out["retest_state"] = "RETEST_SUCCESS"
    elif out["breakout_state"] == "BREAKDOWN_CONFIRMED" and pdl:
        popped = any(h >= pdl * (1 - _NEAR) for h in highs[-3:])
        if popped and last < pdl:
            out["retest_state"] = "RETEST_SUCCESS"

    # -------- reversal candidate near the day's extreme -----------------
    if day_low and min(lows[-3:]) <= day_low * (1 + _NEAR) and last > prev > closes[-3]:
        out["reversal_candidate"] = True
    if day_high and max(highs[-3:]) >= day_high * (1 - _NEAR) and last < prev < closes[-3]:
        out["reversal_candidate"] = True

    # -------- single-bar candle shape (last closed bar) ----------------
    o = _f(cs[-1].get("open")) or prev
    h, l = highs[-1], lows[-1]
    rng = max(1e-9, h - l)
    body = abs(last - o)
    upper_wick, lower_wick = h - max(last, o), min(last, o) - l
    if body <= 0.35 * rng and lower_wick >= 2 * body and lower_wick > upper_wick:
        out["candle_signals"].append("hammer")
    if body <= 0.35 * rng and upper_wick >= 2 * body and upper_wick > lower_wick:
        out["candle_signals"].append("shooting_star")
    po = _f(cs[-2].get("open")) or closes[-3]
    if last > o and prev < po and last >= po and o <= prev:
        out["candle_signals"].append("bullish_engulf")
    if last < o and prev > po and last <= po and o >= prev:
        out["candle_signals"].append("bearish_engulf")
    return out
