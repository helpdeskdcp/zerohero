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
