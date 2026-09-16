"""
All weights, thresholds, and margins the spec asks to be "configurable" --
none hard-coded into ce_strategy.py/pe_strategy.py/conflict.py's logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Bucket weights must sum to 1.0 -- CE and PE use the SAME bucket shape
# (bearish is the mirror of bullish, not a different set of buckets), per
# the spec's own CE/PE weight examples.
DEFAULT_WEIGHTS = {
    "trend": 0.25,
    "momentum": 0.20,
    "vwap": 0.15,
    "volume": 0.10,
    "oi": 0.15,
    "price_action": 0.10,
    "volatility": 0.05,
}


@dataclass
class StrategyConfig:
    # -- weighted scoring --
    ce_weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    pe_weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    # -- 5/20 SMA rule --
    sma_fast: int = 5
    sma_slow: int = 20
    slope_lookback: int = 3            # bars back to measure SMA slope over
    wrong_side_penalty: float = 0.5    # confidence multiplier when price is on the WRONG side of sma_fast

    # -- indicator thresholds --
    rsi_period: int = 14
    rsi_bull_min: float = 50.0         # RSI must be >= this AND rising for bullish momentum
    rsi_bear_max: float = 50.0         # RSI must be <= this AND falling for bearish momentum
    adx_period: int = 14
    adx_trend_min: float = 20.0
    bb_period: int = 20
    bb_std: float = 2.0
    atr_period: int = 14
    volatility_max_atr_pct: float = 3.0   # ATR/price %, above this = "too noisy", volatility bucket fails

    # -- CE/PE decision --
    minimum_threshold: float = 65.0
    minimum_margin: float = 10.0

    # -- raw-signal-vs-strategy conflict resolution --
    allow_conflict_override: bool = False   # False -> a SIGNAL_CONFLICT always resolves to NO_TRADE

    # -- anti-whipsaw / confirmation --
    confirmation_min_count: int = 2         # consecutive same-direction reads required before ENTRY_READY
    near_tie_margin: float = 5.0            # |ce_score - pe_score| below this near the threshold = avoid
    spike_atr_mult: float = 2.5             # a single-bar range > this many ATRs = "abnormal spike", skip
    sideways_adx_max: float = 15.0          # ADX below this = "sideways/noisy", require extra confirmation

    def normalized_weights(self, side: str) -> dict:
        w = self.ce_weights if side == "CE" else self.pe_weights
        total = sum(w.values()) or 1.0
        return {k: v / total for k, v in w.items()}
