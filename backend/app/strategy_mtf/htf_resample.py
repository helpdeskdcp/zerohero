"""
Anti-repaint higher-timeframe resampling -- the foundation every other
module in this package builds on. Extends the exact-aggregation approach
`app.liquidity_sweep.resample` already uses (real bars only, up-aggregation
never fabricated fine-graining) up through Weekly and Monthly, which don't
exist anywhere in this codebase yet.

THE CORRECTION THIS MODULE EXISTS FOR: this session's earlier HTF work
(`app.liquidity_sweep.resample` + `app.index_signal_research.features.
add_htf_bias`) stamped a resampled bucket's timestamp as its LAST REAL
CONSTITUENT BAR's time. That is causally safe (never a future timestamp),
but it does NOT distinguish "this period has fully closed" from "this
period is still forming and its OHLC will keep changing" -- a bucket with
just one real bar in it already has SOME aggregated value, which a naive
consumer could mistake for the period's real, settled read.

This module makes that distinction EXPLICIT and provable independent of
what data happens to be present: a bucket is CONFIRMED if and only if
`as_of_ts` is at or past that bucket's CALENDAR end boundary (computed from
the calendar/clock alone -- month-end, ISO-week-end (Monday 00:00 IST),
hour/N-minute boundary -- never from "does a later bar exist"). The
in-progress bucket (if any) is returned SEPARATELY as `developing`, for
informational/live display only; nothing in this codebase may use it for a
historical/backtest decision unless a caller opts in explicitly and says so.

Every function returns bucket start/end alongside the OHLCV so a caller can
always answer "was this confirmed, and when did it close" without
recomputing anything.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..liquidity_sweep.resample import _parse_ts    # UTC -> IST wall-clock parse, already tested

TIMEFRAMES = ("5m", "15m", "30m", "1h", "1d", "1w", "1mo")
_INTRADAY_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60}


@dataclass
class HTFBar:
    bucket_start: datetime      # naive IST
    bucket_end: datetime        # naive IST -- the CALENDAR boundary, independent of data
    t: str                      # original timestamp string of the last real constituent bar
    o: float
    h: float
    l: float
    c: float
    v: float
    confirmed: bool


@dataclass
class HTFSeries:
    tf: str
    confirmed: list = field(default_factory=list)     # list[HTFBar], oldest..newest, all CLOSED
    developing: "HTFBar | None" = None                 # the current, still-forming bucket, or None


def _intraday_bucket_bounds(dt: datetime, minutes: int) -> tuple[datetime, datetime]:
    floor_min = (dt.hour * 60 + dt.minute) // minutes * minutes
    start = dt.replace(hour=floor_min // 60, minute=floor_min % 60, second=0, microsecond=0)
    return start, start + timedelta(minutes=minutes)


def _daily_bucket_bounds(dt: datetime) -> tuple[datetime, datetime]:
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _weekly_bucket_bounds(dt: datetime) -> tuple[datetime, datetime]:
    """ISO week, Monday 00:00 -> next Monday 00:00 IST. A calendar boundary,
    not a trading-session one -- NSE's real last bar of the week always
    lands on or before Friday, safely inside this window regardless."""
    start = (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=7)


def _monthly_bucket_bounds(dt: datetime) -> tuple[datetime, datetime]:
    start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days_in_month = calendar.monthrange(start.year, start.month)[1]
    return start, start + timedelta(days=days_in_month)


def _bounds_fn(tf: str):
    if tf in _INTRADAY_MINUTES:
        return lambda dt: _intraday_bucket_bounds(dt, _INTRADAY_MINUTES[tf])
    if tf == "1d":
        return _daily_bucket_bounds
    if tf == "1w":
        return _weekly_bucket_bounds
    if tf == "1mo":
        return _monthly_bucket_bounds
    raise ValueError(f"unknown timeframe {tf!r}, expected one of {TIMEFRAMES}")


def resample_confirmed(bars: list[dict], tf: str, *, as_of_ts: str | None = None) -> HTFSeries:
    """`bars`: real finer-granularity bars (already causal: every bar's own
    timestamp <= the caller's decision time). `as_of_ts`: the decision
    timestamp confirmation is judged against -- defaults to the last bar's
    own timestamp (i.e. "as of the most recent data we have"), but a caller
    grading historical decisions at an earlier point should pass it
    explicitly rather than rely on `bars` already being truncated.

    A bucket is CONFIRMED iff `as_of_ts >= bucket_end` -- a pure calendar
    fact, independent of how many real bars happen to have arrived."""
    bounds_fn = _bounds_fn(tf)
    if not bars:
        return HTFSeries(tf=tf, confirmed=[], developing=None)

    as_of = _parse_ts(as_of_ts) if as_of_ts else _parse_ts(bars[-1]["t"])
    buckets: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    bound_cache: dict[tuple, tuple[datetime, datetime]] = {}
    for b in bars:
        dt = _parse_ts(b.get("t"))
        if dt is None or dt > as_of:
            continue   # never let a bar timestamped after as_of into any bucket -- belt+braces
        start, end = bounds_fn(dt)
        key = start.isoformat()
        if key not in buckets:
            buckets[key] = []
            order.append(key)
            bound_cache[key] = (start, end)
        buckets[key].append(b)

    confirmed: list[HTFBar] = []
    developing: HTFBar | None = None
    for key in order:
        group = buckets[key]
        start, end = bound_cache[key]
        is_confirmed = as_of >= end
        htf_bar = HTFBar(
            bucket_start=start, bucket_end=end, t=group[-1]["t"],
            o=group[0]["o"], h=max(g["h"] for g in group), l=min(g["l"] for g in group),
            c=group[-1]["c"], v=sum((g.get("v") or 0) for g in group), confirmed=is_confirmed,
        )
        if is_confirmed:
            confirmed.append(htf_bar)
        else:
            developing = htf_bar   # at most one non-confirmed bucket can exist (the newest)

    return HTFSeries(tf=tf, confirmed=confirmed, developing=developing)
