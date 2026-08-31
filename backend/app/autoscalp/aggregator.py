"""Tick -> multi-timeframe OHLC candle aggregation for the live runner.

Feed a stream of (epoch_sec, price[, volume]) per instrument; get back closed
{t,o,h,l,c,v} bars at 1m / 3m / 5m / 15m / 30m. Bars are keyed to IST
wall-clock bucket starts so they line up with the historical adapter's
resample_candles() output.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone

_IST = timezone(timedelta(hours=5, minutes=30))
_TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30}
_MAXBARS = 400          # ~ a full session of 1m bars, plenty for the engines


def _bucket(epoch: float, tf_min: int) -> str:
    dt = datetime.fromtimestamp(epoch, _IST)
    m = (dt.hour * 60 + dt.minute) // tf_min * tf_min
    return f"{dt.date().isoformat()}T{m // 60:02d}:{m % 60:02d}:00"


def _epoch(t) -> float | None:
    """epoch seconds from an epoch (s or ms) or an ISO-8601 string."""
    if t is None:
        return None
    if isinstance(t, (int, float)):
        return t / 1000.0 if t > 1e12 else float(t)
    try:
        return datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


class CandleAggregator:
    """One per instrument (index or a specific option token)."""

    def __init__(self, tfs=("1m", "3m", "5m", "15m", "30m")):
        self.tfs = [tf for tf in tfs if tf in _TF_MIN]
        self._bars: dict[str, deque] = {tf: deque(maxlen=_MAXBARS) for tf in self.tfs}
        self._cur: dict[str, str] = {tf: "" for tf in self.tfs}
        self.last_price = None
        self.last_ts = None

    def add_tick(self, epoch: float, price: float, volume: float = 0.0) -> None:
        if price is None or price != price or price <= 0:
            return
        self.last_price, self.last_ts = float(price), float(epoch)
        for tf in self.tfs:
            b = _bucket(epoch, _TF_MIN[tf])
            dq = self._bars[tf]
            if b != self._cur[tf]:
                dq.append({"t": b, "o": price, "h": price, "l": price, "c": price, "v": float(volume or 0.0)})
                self._cur[tf] = b
            else:
                c = dq[-1]
                c["h"] = max(c["h"], price)
                c["l"] = min(c["l"], price)
                c["c"] = price
                c["v"] += float(volume or 0.0)

    def seed_from_ohlc(self, rows) -> int:
        """Prefill from historical bars BEFORE live ticks arrive so a (re)start
        does not blind the engine for ~100 minutes. Accepts [t,o,h,l,c,v] lists
        or {t,o,h,l,c,v} dicts; t is epoch (s/ms) or ISO-8601. No-op once a live
        tick has been seen (last_ts set) — a late seed never rewrites history.
        Returns the number of source bars replayed."""
        if self.last_ts is not None:
            return 0
        n = 0
        for r in rows or []:
            if isinstance(r, dict):
                t = r.get("t", r.get("time"))
                o, h, l, c = r.get("o"), r.get("h"), r.get("l"), r.get("c")
                v = r.get("v", r.get("volume", 0.0))
            else:
                try:
                    t, o, h, l, c = r[0], r[1], r[2], r[3], r[4]
                    v = r[5] if len(r) > 5 else 0.0
                except (IndexError, TypeError):
                    continue
            ep = _epoch(t)
            try:
                o, h, l, c = float(o), float(h), float(l), float(c)
                v = float(v or 0.0)
            except (TypeError, ValueError):
                continue
            if ep is None or min(o, h, l, c) <= 0:
                continue
            # replay each bar as O -> low -> high -> C inside its own minute so
            # every timeframe bucket gets a faithful open/high/low/close
            self.add_tick(ep + 1.0, o)
            self.add_tick(ep + 20.0, min(o, h, l, c))
            self.add_tick(ep + 40.0, max(o, h, l, c))
            self.add_tick(ep + 58.0, c, v)
            n += 1
        return n

    def bars(self, tf: str, *, closed_only: bool = True, now_epoch: float | None = None) -> list[dict]:
        """Bars for `tf`. With closed_only, the still-forming last bar is dropped
        (its bucket end is > now) so the engines never see a partial bar."""
        dq = list(self._bars.get(tf, ()))
        if not closed_only or not dq:
            return dq
        now = now_epoch if now_epoch is not None else (self.last_ts or 0)
        end = datetime.fromisoformat(dq[-1]["t"]).replace(tzinfo=_IST).timestamp() + _TF_MIN[tf] * 60
        return dq[:-1] if end > now else dq

    def snapshot(self, *, now_epoch: float | None = None) -> dict[str, list[dict]]:
        return {tf: self.bars(tf, now_epoch=now_epoch) for tf in self.tfs}
