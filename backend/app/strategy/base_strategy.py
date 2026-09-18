"""
The causal input contract (MarketFeatures) and the per-strategy output
contract (StrategyResult, matching the spec's exact JSON shape), plus the
one shared indicator snapshot both ce_strategy.py and pe_strategy.py read
from -- computed ONCE per verification call so CE and PE always see
IDENTICAL indicator values (no drift between the two sides).

NO LOOK-AHEAD: `MarketFeatures.bars` must already be truncated to <= the
signal timestamp by the CALLER (same contract as every other causal module
in this codebase -- app.liquidity_sweep, app.index_signal_research). This
package never reaches outside what it is handed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import pstdev

from ..engines.signal_engine import _adx, _atr, _macd, _rsi, _sma


def _num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


@dataclass
class MarketFeatures:
    """`bars`: chronological list of {"t","o","h","l","c","v"} dicts, already
    truncated to <= the decision timestamp. `htf_bars`: optional higher-
    timeframe bars for the higher-timeframe-trend condition, same causal
    contract. `oi`/`oi_chg`: optional current open-interest + its change
    (None when unavailable -- OI confirmation degrades, does not crash).
    `support`/`resistance`: optional known levels for the S/R condition."""
    bars: list[dict]
    htf_bars: list[dict] | None = None
    oi: float | None = None
    oi_chg: float | None = None
    support: float | None = None
    resistance: float | None = None


@dataclass
class StrategyResult:
    strategy_name: str
    direction: str                 # "CE" | "PE" | "NEUTRAL"
    valid: bool
    score: float                   # 0-100
    confidence: float               # 0-100
    conditions_passed: list = field(default_factory=list)
    conditions_failed: list = field(default_factory=list)
    reason: str = ""
    entry_allowed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class IndicatorSnapshot:
    close: float | None
    highs: list
    lows: list
    closes: list
    volumes: list
    sma_fast: float | None
    sma_fast_prev: float | None       # sma_fast measured `slope_lookback` bars earlier
    sma_slow: float | None
    vwap: float | None
    rsi: float | None
    rsi_prev: float | None
    macd: float | None
    macd_prev: float | None
    adx: float | None
    atr: float | None
    bb_upper: float | None
    bb_lower: float | None
    bb_mid: float | None
    avg_volume: float | None
    n: int

    @property
    def sma_fast_slope(self) -> float | None:
        if self.sma_fast is None or self.sma_fast_prev is None:
            return None
        return self.sma_fast - self.sma_fast_prev

    @property
    def atr_pct(self) -> float | None:
        if self.atr is None or not self.close:
            return None
        return self.atr / self.close * 100.0


def _bollinger(closes: list, period: int, k: float):
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid = sum(window) / period
    sd = pstdev(window)
    return mid + k * sd, mid - k * sd, mid


def build_indicator_snapshot(features: MarketFeatures, cfg) -> IndicatorSnapshot | None:
    """One pass over `features.bars`, causal throughout (every indicator is
    computed only from bars already present -- `_sma`/`_ema_series`/etc. all
    slice from the END of the array they're given, never look forward)."""
    clean = [(_num(b.get("h")), _num(b.get("l")), _num(b.get("c")), _num(b.get("v")))
            for b in (features.bars or [])]
    clean = [row for row in clean if row[0] is not None and row[1] is not None and row[2] is not None]
    if len(clean) < max(cfg.sma_slow, cfg.rsi_period + 1) :
        return None
    highs, lows, closes, vols = (list(col) for col in zip(*clean))
    vols = [v or 0.0 for v in vols]
    n = len(closes)

    sma_fast = _sma(closes, cfg.sma_fast)
    lb = min(cfg.slope_lookback, n - cfg.sma_fast) if n > cfg.sma_fast else 0
    sma_fast_prev = _sma(closes[:-lb], cfg.sma_fast) if lb > 0 else None
    sma_slow = _sma(closes, cfg.sma_slow)
    rsi = _rsi(closes, cfg.rsi_period)
    rsi_prev = _rsi(closes[:-1], cfg.rsi_period) if n > cfg.rsi_period + 1 else None
    macd = _macd(closes)
    macd_prev = _macd(closes[:-1]) if n > 27 else None
    adx_res = _adx(highs, lows, closes, n, cfg.adx_period)
    adx = adx_res["adx"] if isinstance(adx_res, dict) else adx_res
    atr = _atr(highs, lows, closes, n, cfg.atr_period)
    bb_upper, bb_lower, bb_mid = _bollinger(closes, cfg.bb_period, cfg.bb_std)
    avg_volume = (sum(vols[-20:]) / len(vols[-20:])) if vols else None
    vwap = None
    pv = vv = 0.0
    for h, l, c, v in zip(highs, lows, closes, vols):
        tp = (h + l + c) / 3
        pv += tp * v
        vv += v
    if vv > 0:
        vwap = pv / vv

    return IndicatorSnapshot(
        close=closes[-1], highs=highs, lows=lows, closes=closes, volumes=vols,
        sma_fast=sma_fast, sma_fast_prev=sma_fast_prev, sma_slow=sma_slow,
        vwap=vwap, rsi=rsi, rsi_prev=rsi_prev, macd=macd, macd_prev=macd_prev,
        adx=adx, atr=atr, bb_upper=bb_upper, bb_lower=bb_lower, bb_mid=bb_mid,
        avg_volume=avg_volume, n=n,
    )
