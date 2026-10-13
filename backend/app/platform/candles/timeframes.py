"""Higher timeframes derived ONLY from real closed 1-second candles (never from synthetic data).

Buckets are aligned to the SESSION OPEN (e.g. NSE 09:15) so 1H bars are 09:15-10:15, not clock hours.
A higher-timeframe candle is closed only when its bucket end has passed (watermark) -- until then
`current()` exposes a partial candle flagged closed=False for live charts.
"""
from __future__ import annotations

from .engine_1s import IST_OFFSET, Candle

DEFAULT_TFS = (5, 15, 30, 60, 120, 180, 300, 900, 1800, 3600)
SESSION_OPEN_MIN = {"NSE": 555, "BSE": 555, "MCX": 540}


def bucket_start(ts: int, tf_sec: int, session_open_min: int) -> int:
    ist = int(ts) + IST_OFFSET
    day0 = ist - ist % 86400
    origin = day0 + session_open_min * 60
    return origin + ((ist - origin) // tf_sec) * tf_sec - IST_OFFSET


def merge(candles: list[Candle], tf_sec: int, start_ts: int, closed: bool) -> Candle:
    first, last = candles[0], candles[-1]
    vols = [c.volume for c in candles]
    known = [v for v in vols if v is not None]
    ois = [c.oi_change for c in candles if c.oi_change is not None]
    return Candle(first.instrument, tf_sec, start_ts, last.provider, first.o, max(c.h for c in candles),
                  min(c.l for c in candles), last.c, sum(c.tick_count for c in candles),
                  volume=(sum(known) if known else None),
                  volume_complete=bool(known) and len(known) == len(vols) and all(c.volume_complete for c in candles),
                  oi=last.oi, oi_change=(sum(ois) if ois else None), first_ts=first.first_ts, last_ts=last.last_ts,
                  closed=closed)


def aggregate(candles_1s: list[Candle], tf_sec: int, session_open_min: int = 555) -> list[Candle]:
    """Batch: closed 1s candles -> tf candles (all buckets treated as closed)."""
    groups: dict = {}
    for c in sorted(candles_1s, key=lambda x: x.start_ts):
        groups.setdefault(bucket_start(c.start_ts, tf_sec, session_open_min), []).append(c)
    return [merge(v, tf_sec, k, True) for k, v in sorted(groups.items())]


class MultiTF:
    """Incremental: feed closed 1s candles in order; returns newly CLOSED higher-timeframe candles."""

    def __init__(self, tfs=DEFAULT_TFS, session_open_min: int | dict = None):
        self.tfs = tuple(tfs)
        self._som = session_open_min
        self._acc: dict = {}                      # (instrument, tf) -> (bucket_start, [candles])

    def _open_min(self, instrument: str) -> int:
        if isinstance(self._som, int):
            return self._som
        table = {**SESSION_OPEN_MIN, **(self._som or {})}
        return table.get(instrument.split(":", 1)[0].upper(), 555)

    def add(self, c: Candle) -> list[Candle]:
        out = []
        som = self._open_min(c.instrument)
        for tf in self.tfs:
            key = (c.instrument, tf)
            b = bucket_start(c.start_ts, tf, som)
            cur = self._acc.get(key)
            if cur is not None and cur[0] != b:
                out.append(merge(cur[1], tf, cur[0], True))
                cur = None
            if cur is None:
                self._acc[key] = (b, [c])
            else:
                cur[1].append(c)
        return out

    def current(self, instrument: str, tf: int) -> Candle | None:
        cur = self._acc.get((instrument, tf))
        return merge(cur[1], tf, cur[0], False) if cur else None
