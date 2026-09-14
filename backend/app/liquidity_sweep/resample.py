"""
Real-data OHLCV resampling -- UP-aggregation only (5m -> 15m/30m/1h/4h/1d),
never down-fabrication. This is the opposite direction from what section 6
of the index-first brief forbids ("if 3m or 1m data is unavailable, mark it
UNAVAILABLE, do NOT fabricate it"): building a coarser candle from several
REAL finer candles is an exact, lossless aggregation of real data (the
coarser bar's open/high/low/close/volume are mathematically determined by
its real constituent bars), not an invented one. It exists because Stage 1's
original backtest walk only ever passed `bars_by_tf={"5m": ...}` into
engine.evaluate() -- `structure.htf_bias()` only recognises 15m/30m/1h/4h/1d
(see its `_HTF_ORDER`), so every one of the 2,110 Stage-1 signals silently
got htf bias "RANGE"/score 0.0 (the function's own documented fallback for
"no timeframe has enough history"), not a real measurement. That is a data-
plumbing gap, not a genuine "regime has no edge" finding -- fixed here for
the Phase 1 feature-discrimination analysis by supplying REAL resampled
higher timeframes instead of leaving the gap silently masked as RANGE.

Bucketing is done in IST wall-clock time (the Kaggle timestamps are real UTC;
NSE trades 09:15-15:30 IST = 03:45-10:00 UTC) so that hourly/4h buckets align
to the exchange's own convention -- UTC-native bucketing would misalign 1h/4h
boundaries because the UTC/IST offset (5h30m) is not a multiple of 60.
"""
from __future__ import annotations

from datetime import datetime, timedelta

_IST_OFFSET = timedelta(hours=5, minutes=30)


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    s = ts.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None) + dt.utcoffset()
    return dt + _IST_OFFSET   # -> naive IST wall-clock


def _session_date_ist(bar: dict) -> str:
    dt = _parse_ts(bar.get("t"))
    return dt.strftime("%Y-%m-%d") if dt else ""


def resample_bars(bars: list[dict], minutes: int) -> list[dict]:
    """Aggregate real `bars` (assumed <=5m granularity, chronological) into
    `minutes`-wide candles, bucketed on IST wall-clock boundaries. The last
    bucket is naturally partial when `bars` ends mid-bucket -- correct and
    causal (a live system mid-candle has exactly this same partial view),
    never filled in with future data."""
    if not bars:
        return []
    buckets: dict[str, list[dict]] = {}
    order: list[str] = []
    for b in bars:
        dt = _parse_ts(b.get("t"))
        if dt is None:
            continue
        floor_min = (dt.hour * 60 + dt.minute) // minutes * minutes
        key = f"{dt.strftime('%Y-%m-%d')}T{floor_min // 60:02d}:{floor_min % 60:02d}"
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(b)
    out = []
    for key in order:
        group = buckets[key]
        out.append({
            "t": group[-1]["t"], "o": group[0]["o"],
            "h": max(g["h"] for g in group), "l": min(g["l"] for g in group),
            "c": group[-1]["c"], "v": sum((g.get("v") or 0) for g in group),
        })
    return out


def resample_daily(bars: list[dict]) -> list[dict]:
    """Aggregate real `bars` into one candle per session (IST calendar date).
    Same exact-aggregation contract as `resample_bars`."""
    if not bars:
        return []
    buckets: dict[str, list[dict]] = {}
    order: list[str] = []
    for b in bars:
        key = _session_date_ist(b)
        if not key:
            continue
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(b)
    out = []
    for key in order:
        group = buckets[key]
        out.append({
            "t": group[-1]["t"], "o": group[0]["o"],
            "h": max(g["h"] for g in group), "l": min(g["l"] for g in group),
            "c": group[-1]["c"], "v": sum((g.get("v") or 0) for g in group),
        })
    return out
