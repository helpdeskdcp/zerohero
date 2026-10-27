"""Chart candles from Upstox (authorized source only), with correct aggregation and honest freshness metadata.

Upstox serves only 1minute / 30minute / day. The other timeframes are AGGREGATED here:
  3m, 5m, 15m  <- 1minute          1h <- 30minute          30m, 1d <- native
Sub-daily buckets are anchored to the NSE/BSE session open (09:15 IST), so 1h bars are 09:15, 10:15 ... 14:15, 15:15
(the last is a 15-minute stub), as on most charting platforms. Nothing is invented: a bucket exists only if at
least one source bar fell inside it. Index volume is not published by Upstox (always 0) and is flagged.

Live bars: the current session is refreshed through Upstox's intraday REST endpoint at most every
LIVE_TAIL_TTL_SEC per instrument (shared by all viewers, single-flight). This is REST polling, not a socket, and
the metadata says so. If a refresh fails the cached series is still served, marked stale, with the error text.
"""
from __future__ import annotations

import threading
import time
from bisect import bisect_left
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone

from .. import market_calendar
from . import config, upstox_ext
from .symbols import Instrument

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_OPEN_MIN = 9 * 60 + 15
TIMEFRAMES = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400}
NATIVE_FOR = {"1m": "1minute", "3m": "1minute", "5m": "1minute", "15m": "1minute", "30m": "30minute", "1h": "30minute", "1d": "day"}
NATIVE_SEC = {"1minute": 60, "30minute": 1800, "day": 86400}
SESSION_MIN = 375
MAX_FETCHES_PER_CALL = 40


class CandleError(RuntimeError):
    pass


# ------------------------------------------------------------------------------------ aggregation
def _day_open_ms(ms: int) -> int:
    """epoch ms of 09:15 IST on the IST calendar day containing `ms`."""
    d = datetime.fromtimestamp(ms / 1000, IST)
    return int(datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST).timestamp() * 1000)


def aggregate(bars: list[tuple], tf_sec: int) -> list[tuple]:
    """Aggregate ascending (ms,o,h,l,c,v) source bars into session-anchored buckets of tf_sec seconds."""
    if tf_sec >= 86400:
        return list(bars)
    width = tf_sec * 1000
    out: list[list] = []
    cur_key = None
    for ms, o, h, lo, c, v in bars:
        base = _day_open_ms(ms)
        key = base + ((ms - base) // width) * width if ms >= base else base - width * (((base - ms) + width - 1) // width)
        if key != cur_key:
            out.append([key, o, h, lo, c, v])
            cur_key = key
        else:
            b = out[-1]
            if h > b[2]:
                b[2] = h
            if lo < b[3]:
                b[3] = lo
            b[4] = c
            b[5] += v
    return [tuple(b) for b in out]


# ------------------------------------------------------------------------------------ cache
class _Series:
    def __init__(self):
        self.bars: list[tuple] = []
        self.lock = threading.RLock()
        self.oldest_date: date | None = None     # oldest calendar day requested from history
        self.exhausted = False                   # history reached its start
        self.tail_at = 0.0                       # monotonic time of last successful tail refresh
        self.tail_wall = 0.0
        self.last_error: str | None = None
        self.last_error_at = 0.0

    def merge(self, new: list[tuple]) -> None:
        if not new:
            return
        bars = self.bars
        if not bars:
            self.bars = list(new)
        elif new[-1][0] < bars[0][0]:
            self.bars = list(new) + bars
        elif new[0][0] >= bars[0][0]:
            idx = bisect_left(bars, new[0][0], key=lambda b: b[0])
            del bars[idx:]
            bars.extend(new)
        else:                                    # overlapping prepend: older chunk reaching into stored range
            first = bars[0][0]
            self.bars = [b for b in new if b[0] < first] + bars
        if len(self.bars) > config.CACHE_MAX_BARS_PER_ENTRY:
            self.bars = self.bars[-config.CACHE_MAX_BARS_PER_ENTRY:]
            self.oldest_date = datetime.fromtimestamp(self.bars[0][0] / 1000, IST).date()


_cache: "OrderedDict[tuple, _Series]" = OrderedDict()
_cache_lock = threading.Lock()


def _series(key: str, native: str) -> _Series:
    k = (key, native)
    with _cache_lock:
        s = _cache.get(k)
        if s is None:
            s = _cache[k] = _Series()
            while len(_cache) > config.CACHE_MAX_ENTRIES:
                _cache.popitem(last=False)
        else:
            _cache.move_to_end(k)
        return s


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def cache_stats() -> dict:
    with _cache_lock:
        return {"entries": len(_cache), "bars": sum(len(s.bars) for s in _cache.values()),
                "max_entries": config.CACHE_MAX_ENTRIES, "max_bars_per_entry": config.CACHE_MAX_BARS_PER_ENTRY}


def _today() -> date:
    return datetime.now(IST).date()


def _market_open(exchange: str) -> bool:
    try:
        return market_calendar.is_trading(exchange)
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------------------------ fetching
def _refresh_tail(s: _Series, inst: Instrument, native: str, *, force: bool = False) -> None:
    ttl = config.LIVE_TAIL_TTL_SEC if _market_open(inst.exchange) else 120.0
    if not force and s.tail_at and time.monotonic() - s.tail_at < ttl:
        return
    # A failed refresh must not hammer Upstox either: back off for the same TTL after an error.
    if not force and s.last_error_at and time.monotonic() - s.last_error_at < max(ttl, 5.0):
        return
    try:
        if native == "day":
            t = _today()
            new = upstox_ext.fetch_historical(inst.key, "day", t.isoformat(), (t - timedelta(days=7)).isoformat())
        else:
            new = upstox_ext.fetch_intraday(inst.key, native)
            if not s.bars and not new:
                pass                              # market closed / holiday: history window below supplies the data
        # Intraday returns only today's session: merge as a tail (replaces from its first bar on).
        s.merge(new)
        s.tail_at, s.tail_wall = time.monotonic(), time.time()
        s.last_error = None
    except upstox_ext.UpstoxError as e:
        s.last_error, s.last_error_at = str(e), time.monotonic()
    except Exception as e:  # noqa: BLE001 - network/JSON problems degrade to cached data
        s.last_error, s.last_error_at = f"{type(e).__name__}: {e}"[:200], time.monotonic()


def _ensure_history(s: _Series, inst: Instrument, native: str, need_before_ms: int | None, need_bars: int) -> None:
    """Load history windows (newest-first, walking backwards) until `need_bars` source bars older than `need_before_ms` exist."""
    window = upstox_ext.WINDOW_DAYS[native]
    calls = 0
    while calls < MAX_FETCHES_PER_CALL and not s.exhausted:
        have = len(s.bars) if need_before_ms is None else bisect_left(s.bars, need_before_ms, key=lambda b: b[0])
        if have >= need_bars:
            return
        to_d = _today() if s.oldest_date is None else s.oldest_date - timedelta(days=1)
        from_d = to_d - timedelta(days=window - 1)
        calls += 1
        try:
            new = upstox_ext.fetch_historical(inst.key, native, to_d.isoformat(), from_d.isoformat())
        except upstox_ext.UpstoxError as e:
            s.last_error, s.last_error_at = str(e), time.monotonic()
            return
        except Exception as e:  # noqa: BLE001
            s.last_error, s.last_error_at = f"{type(e).__name__}: {e}"[:200], time.monotonic()
            return
        s.oldest_date = from_d
        if not new:
            s.exhausted = True
            return
        s.merge(new)


# ------------------------------------------------------------------------------------ public API
def _row(b: tuple) -> dict:
    return {"time": b[0], "open": b[1], "high": b[2], "low": b[3], "close": b[4], "volume": b[5]}


def get_candles(inst: Instrument, tf: str, limit: int = config.DEFAULT_CANDLES, before_ms: int | None = None,
                *, refresh: bool = True) -> dict:
    """Newest `limit` candles of `tf` (strictly older than `before_ms` when given) + metadata. Raises CandleError when no data exists at all."""
    if tf not in TIMEFRAMES:
        raise CandleError(f"timeframe {tf!r} not supported; supported: {', '.join(TIMEFRAMES)}")
    limit = max(1, min(int(limit), config.MAX_CANDLES))
    native = NATIVE_FOR[tf]
    tf_sec = TIMEFRAMES[tf]
    ratio = max(1, tf_sec // NATIVE_SEC[native])
    s = _series(inst.key, native)
    with s.lock:
        if refresh and before_ms is None:
            _refresh_tail(s, inst, native)
        # source bars needed: limit buckets * ratio, + slack for sessions that are shorter than a full day
        need = limit * ratio + (2 * SESSION_MIN if native != "day" else 5)
        _ensure_history(s, inst, native, before_ms, need)
        src = s.bars
        if not src:
            raise CandleError(s.last_error or f"no candles returned for {inst.id} ({native})")
        if before_ms is not None:
            cut = bisect_left(src, before_ms, key=lambda b: b[0])
            src = src[:cut]
        window = src[-need:] if len(src) > need else src
        err, err_at, tail_wall = s.last_error, s.last_error_at, s.tail_wall
        exhausted = s.exhausted
        window = list(window)
    agg = aggregate(window, tf_sec) if tf_sec < 86400 else window
    if before_ms is not None:
        agg = [b for b in agg if b[0] < before_ms]
    # the first aggregated bucket may be partial because the window was cut mid-bucket: drop it when more history exists
    if tf_sec < 86400 and ratio > 1 and len(src) > len(window) and agg:
        agg = agg[1:]
    agg = agg[-limit:]
    now_ms = int(time.time() * 1000)
    last = agg[-1] if agg else None
    open_now = _market_open(inst.exchange)
    forming = bool(last and open_now and last[0] + tf_sec * 1000 > now_ms and before_ms is None)
    return {
        "symbol": inst.id, "tf": tf,
        "candles": [_row(b) for b in agg],
        "meta": {
            "source": "upstox", "endpoint": "historical-candle v2 (+ intraday for the live session)",
            "native_interval": native, "aggregated": tf_sec > NATIVE_SEC[native],
            "transport": "rest-poll", "live_ttl_sec": config.LIVE_TAIL_TTL_SEC,
            "volume_available": inst.has_volume, "instrument_kind": inst.kind, "exchange": inst.exchange,
            "market_open": open_now, "last_bar_forming": forming,
            "last_bar_open_ms": last[0] if last else None,
            "server_time_ms": now_ms,
            "tail_refreshed_ms": int(tail_wall * 1000) if tail_wall else None,
            "history_exhausted": exhausted,
            "stale": bool(err and err_at and time.monotonic() - err_at < 600),
            "error": err,
        },
    }
