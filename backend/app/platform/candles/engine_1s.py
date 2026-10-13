"""Tick -> 1-second candles. Real data only: an empty second produces NO candle (gaps are
reported as events, never filled), volume is the delta of the provider's CUMULATIVE volume
(never a sum of last-traded quantities), OI is a snapshot (never summed as volume), and a
provider handover or counter reset restarts the volume baseline instead of creating negative or
double-counted volume.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from ..marketdata.types import Tick

IST_OFFSET = 19800


@dataclass
class Candle:
    instrument: str
    tf_sec: int
    start_ts: int                  # epoch seconds of the bucket start
    provider: str
    o: float
    h: float
    l: float
    c: float
    tick_count: int = 1
    volume: float | None = None    # sum of known cumulative-volume deltas; None = provider gave no volume
    volume_complete: bool = True   # False if any tick lacked a usable volume delta (baseline/reset/missing)
    oi: float | None = None        # last OI snapshot in the bucket
    oi_change: float | None = None  # oi - previous candle's oi (None if either unknown)
    first_ts: float = 0.0
    last_ts: float = 0.0
    closed: bool = False
    synthetic: bool = False        # always False: candles are never fabricated


@dataclass
class GapEvent:
    instrument: str
    after_ts: int
    before_ts: int
    seconds: int
    provider: str


def make_session_fn(specs: dict | None = None):
    """specs: exchange prefix -> (start_min, end_min) IST minute-of-day [start, end). Weekdays only.
    Holidays are not modelled here (a closed market simply sends no ticks)."""
    specs = specs or {"NSE": (555, 930), "BSE": (555, 930), "MCX": (540, 1410)}

    def in_session(instrument: str, ts: float) -> bool:
        spec = specs.get(instrument.split(":", 1)[0].upper())
        if spec is None:
            return False
        ist = int(ts) + IST_OFFSET
        if ((ist // 86400) + 4) % 7 >= 5:
            return False
        mod = (ist % 86400) // 60
        return spec[0] <= mod < spec[1]
    return in_session


class _Builder:
    __slots__ = ("c",)

    def __init__(self, c: Candle):
        self.c = c


class CandleEngine1s:
    def __init__(self, session_fn=None, *, late_tolerance_sec: float = 2.0, gap_after_sec: int = 30, max_gaps: int = 5000,
                 gap_threshold_fn=None):
        # Per-instrument silence is only INFORMATIONAL (sparse contracts legitimately go quiet: at a 5 s threshold
        # 5000+ gap events were logged over 3 days of real ticks). Feed-wide silence is the provider-health signal.
        self.in_session = session_fn or make_session_fn()
        self.late_tol, self.gap_after = late_tolerance_sec, gap_after_sec
        self._gap_fn = gap_threshold_fn
        self._open: dict = {}                    # instrument -> {bucket: Candle}
        self._last_cum: dict = {}                # instrument -> last cumulative volume
        self._prev_oi: dict = {}                 # instrument -> OI of the last CLOSED candle
        self._last_closed_bucket: dict = {}
        self._watermark: dict = {}
        self.gaps: deque = deque(maxlen=max_gaps)
        self.counters = {"ticks": 0, "outside_session": 0, "late_dropped": 0, "candles_closed": 0}

    def add(self, tick: Tick, reset_volume_baseline: bool = False) -> list[Candle]:
        ets = tick.event_ts()
        inst = tick.instrument
        if not self.in_session(inst, ets):
            self.counters["outside_session"] += 1
            return []
        self.counters["ticks"] += 1
        b = int(math.floor(ets))
        if reset_volume_baseline:
            self._last_cum.pop(inst, None)
        lcb = self._last_closed_bucket.get(inst)
        if lcb is not None and b <= lcb:
            self.counters["late_dropped"] += 1           # candle for that second is already closed: never rewritten
            return []
        opens = self._open.setdefault(inst, {})
        cndl = opens.get(b)
        # volume delta from CUMULATIVE volume only
        vdelta, vknown = None, False
        if tick.cum_volume is not None:
            last = self._last_cum.get(inst)
            if last is not None and tick.cum_volume >= last:
                vdelta, vknown = tick.cum_volume - last, True
            self._last_cum[inst] = tick.cum_volume       # baseline established / re-established on reset or decrease
        if cndl is None:
            cndl = Candle(inst, 1, b, tick.provider, tick.price, tick.price, tick.price, tick.price, 1,
                          first_ts=ets, last_ts=ets)
            if tick.cum_volume is not None:
                cndl.volume, cndl.volume_complete = (vdelta or 0.0), vknown
            opens[b] = cndl
        else:
            cndl.h, cndl.l = max(cndl.h, tick.price), min(cndl.l, tick.price)
            if ets >= cndl.last_ts:
                cndl.c, cndl.last_ts = tick.price, ets
            cndl.tick_count += 1
            if tick.cum_volume is not None:
                if cndl.volume is None:                      # earlier ticks in this second carried no volume
                    cndl.volume_complete = False
                cndl.volume = (cndl.volume or 0.0) + (vdelta or 0.0)
                cndl.volume_complete = cndl.volume_complete and vknown
        if tick.oi is not None:
            cndl.oi = tick.oi
        if tick.cum_volume is None and cndl.volume is not None:
            cndl.volume_complete = False
        wm = max(self._watermark.get(inst, ets), ets) - self.late_tol
        self._watermark[inst] = wm + self.late_tol
        return self._close_ready(inst, wm)

    def flush(self, now: float) -> list[Candle]:
        """Close every candle that can no longer receive ticks (timer-driven; no tick required)."""
        out: list = []
        for inst in list(self._open):
            out += self._close_ready(inst, now - self.late_tol)
        return out

    def _close_ready(self, inst: str, watermark: float) -> list[Candle]:
        opens = self._open.get(inst, {})
        done = sorted(b for b in opens if b + 1 <= watermark)
        out = []
        for b in done:
            c = opens.pop(b)
            c.closed = True
            prev_b = self._last_closed_bucket.get(inst)
            if prev_b is not None and b - prev_b > (self._gap_fn(inst) if self._gap_fn else self.gap_after):
                self.gaps.append(GapEvent(inst, prev_b, b, b - prev_b, c.provider))
            if c.oi is not None and self._prev_oi.get(inst) is not None:
                c.oi_change = c.oi - self._prev_oi[inst]
            if c.oi is not None:
                self._prev_oi[inst] = c.oi
            self._last_closed_bucket[inst] = b
            self.counters["candles_closed"] += 1
            out.append(c)
        return out
