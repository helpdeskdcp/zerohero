"""
Per-timeframe 5-vs-20 SMA directional read -- the cascade's basic unit.
Uses ONLY CONFIRMED HTF candles (never `developing`) unless a caller
explicitly opts into the informational live-only path (see
`compute_bias(..., allow_developing=True)`, off by default).

Every result carries the audit fields the anti-repaint correction requires:
the HTF candle timestamp actually used, whether it was confirmed, and the
as_of (data-available) timestamp the computation was run against.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ..strategy.base_strategy import (
    IndicatorSnapshot,
    MarketFeatures,
    build_indicator_snapshot,
)
from ..strategy.config import StrategyConfig
from .htf_resample import HTFSeries, resample_confirmed

BULLISH, BEARISH, NEUTRAL = "BULLISH", "BEARISH", "NEUTRAL"


@dataclass
class TimeframeBias:
    timeframe: str
    direction: str
    score: float                    # 0-100
    price: float | None
    sma_fast: float | None
    sma_slow: float | None
    sma_fast_slope: float | None
    htf_candle_ts: str | None       # timestamp of the last bar actually used
    htf_confirmed: bool             # False only when a caller explicitly used the developing bucket
    data_available_ts: str | None   # the as_of_ts this computation was run against
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _htf_bars(series: HTFSeries) -> list[dict]:
    return [{"t": b.t, "o": b.o, "h": b.h, "l": b.l, "c": b.c, "v": b.v} for b in series.confirmed]


def _score_from_snapshot(ind: IndicatorSnapshot) -> tuple[str, float]:
    if ind.sma_fast is None or ind.sma_slow is None:
        return NEUTRAL, 0.0
    price = ind.close
    slope = ind.sma_fast_slope
    bull_hits = sum([price > ind.sma_fast, ind.sma_fast >= ind.sma_slow, (slope or 0) >= 0])
    bear_hits = sum([price < ind.sma_fast, ind.sma_fast <= ind.sma_slow, (slope or 0) <= 0])
    if bull_hits >= 2 and bull_hits > bear_hits:
        return BULLISH, round(bull_hits / 3 * 100.0, 1)
    if bear_hits >= 2 and bear_hits > bull_hits:
        return BEARISH, round(bear_hits / 3 * 100.0, 1)
    return NEUTRAL, round(max(bull_hits, bear_hits) / 3 * 100.0, 1)


def compute_bias(bars_5m: list[dict], timeframe: str, *, as_of_ts: str, cfg: StrategyConfig | None = None,
                 allow_developing: bool = False) -> TimeframeBias:
    """`bars_5m`: real 5m bars, already causal (every bar's own timestamp
    <= as_of_ts -- the caller's contract, same as everywhere else). For
    timeframe == "5m" this IS the base data, so no resampling/confirmation
    step applies -- the input bars are already the confirmed 5m series by
    the caller's own contract."""
    cfg = cfg or StrategyConfig()
    if timeframe == "5m":
        htf_bars = bars_5m
        candle_ts = bars_5m[-1]["t"] if bars_5m else None
        confirmed = True
    else:
        series = resample_confirmed(bars_5m, timeframe, as_of_ts=as_of_ts)
        bucket = series.confirmed[-1] if series.confirmed else None
        if bucket is None and allow_developing and series.developing is not None:
            bucket = series.developing
            confirmed = False
        else:
            confirmed = bucket is not None
        htf_bars = _htf_bars(series)
        candle_ts = bucket.t if bucket else None

    ind = build_indicator_snapshot(MarketFeatures(bars=htf_bars), cfg)
    if ind is None:
        return TimeframeBias(timeframe=timeframe, direction=NEUTRAL, score=0.0, price=None,
                             sma_fast=None, sma_slow=None, sma_fast_slope=None,
                             htf_candle_ts=candle_ts, htf_confirmed=confirmed, data_available_ts=as_of_ts,
                             reason=f"insufficient confirmed {timeframe} history")

    direction, score = _score_from_snapshot(ind)
    return TimeframeBias(
        timeframe=timeframe, direction=direction, score=score, price=ind.close,
        sma_fast=ind.sma_fast, sma_slow=ind.sma_slow, sma_fast_slope=ind.sma_fast_slope,
        htf_candle_ts=candle_ts, htf_confirmed=confirmed, data_available_ts=as_of_ts,
        reason=f"{timeframe}: price={'above' if ind.close > (ind.sma_fast or 0) else 'below'} 5sma, "
              f"5sma {'>=' if (ind.sma_fast or 0) >= (ind.sma_slow or 0) else '<'} 20sma, "
              f"slope={'up' if (ind.sma_fast_slope or 0) >= 0 else 'down'}",
    )
