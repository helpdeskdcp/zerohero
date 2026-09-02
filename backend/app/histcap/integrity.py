"""
Data-integrity validators for captured market data.

Rule: **flag, never mutate**. One invariant is HARD (physically impossible -> the
row is corrupt and is rejected + logged); everything else is a SOFT flag stored
alongside the row so a backtest can decide whether to trust it.
"""
from __future__ import annotations


def _n(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def candle_check(o, h, l, c, v, oi) -> tuple[bool, list[str]]:
    """(hard_ok, soft_flags). hard_ok=False -> reject (h < l is impossible)."""
    o, h, l, c, v, oi = (_n(o), _n(h), _n(l), _n(c), _n(v), _n(oi))
    flags: list[str] = []
    if h is not None and l is not None and h < l:
        return False, ["HARD:h<l"]
    if None not in (o, l) and o < l - 1e-9:
        flags.append("o<l")
    if None not in (c, l) and c < l - 1e-9:
        flags.append("c<l")
    if None not in (o, h) and o > h + 1e-9:
        flags.append("o>h")
    if None not in (c, h) and c > h + 1e-9:
        flags.append("c>h")
    if v is not None and v < 0:
        flags.append("v<0")
    if oi is not None and oi < 0:
        flags.append("oi<0")
    return True, flags


def quote_check(ltp, bid, ask, oi, oi_change) -> list[str]:
    """SOFT flags only — a quote is never rejected."""
    ltp, bid, ask, oi = (_n(ltp), _n(bid), _n(ask), _n(oi))
    flags: list[str] = []
    if None not in (bid, ask) and bid > ask + 1e-9:
        flags.append("crossed_book")
    if oi is not None and oi < 0:
        flags.append("oi<0")
    if _n(oi_change) is not None and oi is not None and oi == 0 and _n(oi_change) != 0:
        flags.append("oi0_but_change")
    if (ltp is not None and None not in (bid, ask) and bid > 0 and ask > 0
            and (ltp < bid - abs(bid) * 0.5 or ltp > ask + abs(ask) * 0.5)):
        flags.append("ltp_far_from_book")           # only when a real 2-sided book exists
    return flags


def greek_check(delta, gamma, theta, vega, iv) -> list[str]:
    delta, gamma, theta, vega, iv = (_n(delta), _n(gamma), _n(theta), _n(vega), _n(iv))
    flags: list[str] = []
    if delta is not None and not (-1.05 <= delta <= 1.05):
        flags.append("delta_oob")
    if gamma is not None and gamma < 0:
        flags.append("gamma<0")
    if vega is not None and vega < 0:
        flags.append("vega<0")
    if iv is not None and (iv <= 0 or iv > 5.0):        # >500% IV -> flag, do NOT clamp
        flags.append("iv_oob")
    return flags


def monotonic_flag(prev_ts: str | None, ts: str | None) -> list[str]:
    """Non-decreasing timestamp per (instrument, tf/token). ISO strings compare
    lexicographically when both are UTC 'Z'/'+00:00' normalised."""
    if prev_ts and ts and ts < prev_ts:
        return ["ts_regression"]
    return []


def gap_flag(prev_bar_start: str | None, bar_start: str | None, tf_minutes: int) -> list[str]:
    """A missing bar during a contiguous series (best-effort, string parse)."""
    if not (prev_bar_start and bar_start and tf_minutes):
        return []
    try:
        from datetime import datetime
        a = datetime.fromisoformat(prev_bar_start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(bar_start.replace("Z", "+00:00"))
    except ValueError:
        return []
    delta_min = (b - a).total_seconds() / 60.0
    if delta_min > tf_minutes * 1.5:
        return [f"gap_{int(delta_min)}m"]
    return []
