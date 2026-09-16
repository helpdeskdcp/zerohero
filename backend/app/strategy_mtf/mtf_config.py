"""
All weights/thresholds/lookbacks for the Multi-Timeframe Cascade,
configurable, none hard-coded into the cascade logic itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# HTF weighted more than LTF, per the approved architecture -- Monthly is
# the heaviest single weight (the bias ceiling), 5m is the lightest (entry
# timing fine-tune only, never a direction driver on its own).
TIMEFRAME_WEIGHTS = {
    "1mo": 0.30, "1w": 0.20, "1d": 0.20, "1h": 0.15, "30m": 0.08, "15m": 0.05, "5m": 0.02,
}

# How many real 5m bars to feed the resampler for each timeframe so its own
# 20-period SMA (the slowest indicator any timeframe needs) has enough
# CONFIRMED history -- sized with real margin, not the bare minimum.
TIMEFRAME_LOOKBACK_5M_BARS = {
    "1mo": 30 * 21 * 75,     # ~30 months
    "1w": 30 * 5 * 75,       # ~30 weeks
    "1d": 30 * 75,           # ~30 sessions
    "1h": 30 * 12,           # ~30 hours worth of 5m bars
    "30m": 30 * 6,
    "15m": 30 * 3,
    "5m": 60,
}


@dataclass
class MTFConfig:
    sma_fast: int = 5
    sma_slow: int = 20
    slope_lookback: int = 2

    timeframe_weights: dict = field(default_factory=lambda: dict(TIMEFRAME_WEIGHTS))
    minimum_threshold: float = 65.0
    monthly_override_threshold: float = 80.0   # LTF evidence must clear THIS to trade against monthly bias

    # -- previous day H/L levels --
    level_touch_buffer_atr_mult: float = 0.15
    breakout_confirm_atr_mult: float = 0.10     # close must clear the level by this much, not just wick it
    retest_window_bars: int = 12

    # -- entry quality --
    wrong_side_penalty: float = 0.5
    late_entry_atr_mult: float = 3.0            # move already run > this many ATRs -> LATE_ENTRY
    min_target_points: float = 5.0              # a 1R target below this is not worth trading (spec: "avoid 5 point signals")

    # -- pullback --
    pullback_healthy_max_atr_mult: float = 1.5  # pullback depth beyond this (in ATR) reads as a structural reversal risk

    # -- targets / stop --
    sl_noise_grid_atr_mults: tuple = (0.25, 0.4, 0.6, 0.8, 1.0, 1.25, 1.5)
    sl_noise_lookback_bars: int = 100
    sl_max_noise_touch_rate: float = 0.05       # pick the SMALLEST buffer that random noise touched <= 5% of the time
    r_multiple_stages: tuple = (1.0, 2.0, 3.0, 4.0)

    # -- sideways / ZTH gate --
    sideways_adx_max: float = 18.0
    zth_expansion_atr_mult: float = 2.5

    def normalized_weights(self) -> dict:
        total = sum(self.timeframe_weights.values()) or 1.0
        return {k: v / total for k, v in self.timeframe_weights.items()}
