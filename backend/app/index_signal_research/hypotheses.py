"""
Phase 3/5 -- seven distinct, independently falsifiable index-direction
hypotheses. Each function takes the full feature frame (from
`features.build_feature_frame`) and returns a pandas Series of
"UP" | "DOWN" | None per row -- None where the hypothesis does not fire.
Thresholds below are plain, round, documented choices, NOT fitted to this
dataset (the mission's own "do not tune parameters to chase a better
backtest" rule) -- if a hypothesis fails at these settings it is reported
as failing, not re-tuned until it passes.

Every condition here uses only columns from `features.py`, all of which are
causal by construction (rolling/ewm/shift, merge_asof backward) -- no
hypothesis here can see beyond the bar it fires on.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# -- documented, fixed thresholds (chosen before any hypothesis was run) --
EXPANSION_ATR_MULT = 1.0          # Hypothesis A: net 6-bar move size to call it "expansion"
BREAKOUT_ATR_MULT = 0.3           # Hypothesis B: minimum breakout distance beyond prior 20-bar range
REVERSION_ATR_MULT = 2.0          # Hypothesis C: minimum displacement from EMA20 to call it "extreme"
RSI_OVERBOUGHT, RSI_OVERSOLD = 75, 25
PULLBACK_TOUCH_ATR = 0.5          # Hypothesis D: how close to EMA20 counts as a real pullback touch
VOL_EXPANSION_RATIO = 1.5         # Hypothesis F: ATR / 20-bar-avg-ATR ratio to call it "just expanded"
OPENING_RANGE_BARS = 6            # Hypothesis E: first 30 min on 5m bars


def hyp_a_trend_continuation(df: pd.DataFrame) -> pd.Series:
    """After a real (ATR-normalized) directional expansion over the last 6
    bars, AND the 10-bar block structure agrees (HH+HL / LH+LL), bet
    CONTINUATION in that direction."""
    up = (df["expansion_move_atr"] >= EXPANSION_ATR_MULT) & df["struct_uptrend"]
    down = (df["expansion_move_atr"] <= -EXPANSION_ATR_MULT) & df["struct_downtrend"]
    return pd.Series(np.select([up, down], ["UP", "DOWN"], default=None), index=df.index)


def hyp_b_breakout(df: pd.DataFrame) -> pd.Series:
    """Close breaks beyond the prior 20-bar high/low by a real (ATR-
    normalized) margin -- a plain breakout bet, direction = breakout
    direction. (Range-compression-before-breakout is tested separately as
    an ablation variant in the driver script, not baked in here.)"""
    up = df["breakout_up_atr"] >= BREAKOUT_ATR_MULT
    down = df["breakout_down_atr"] >= BREAKOUT_ATR_MULT
    return pd.Series(np.select([up, down], ["UP", "DOWN"], default=None), index=df.index)


def hyp_c_mean_reversion(df: pd.DataFrame, *, bet: str = "reversion") -> pd.Series:
    """`bet="reversion"`: price far ABOVE ema20 + RSI overbought -> bet DOWN
    (fade); far below + oversold -> bet UP. `bet="continuation"`: the exact
    opposite mapping -- tested separately per the brief's own "test both,
    do not assume one" instruction."""
    extreme_high = (df["dist_from_ema20_atr"] >= REVERSION_ATR_MULT) & (df["rsi14"] >= RSI_OVERBOUGHT)
    extreme_low = (df["dist_from_ema20_atr"] <= -REVERSION_ATR_MULT) & (df["rsi14"] <= RSI_OVERSOLD)
    if bet == "reversion":
        return pd.Series(np.select([extreme_high, extreme_low], ["DOWN", "UP"], default=None), index=df.index)
    return pd.Series(np.select([extreme_high, extreme_low], ["UP", "DOWN"], default=None), index=df.index)


def hyp_d_pullback_in_trend(df: pd.DataFrame) -> pd.Series:
    """Established trend (same structure test as A) + price has pulled back
    close to EMA20 (a real retracement, not still extended) + the current
    candle closes back in the trend direction (a simple confirmation:
    bullish candle in an uptrend pullback, bearish candle in a downtrend
    pullback) -> bet CONTINUATION."""
    near_ema = df["dist_from_ema20_atr"].abs() <= PULLBACK_TOUCH_ATR
    bullish_candle = df["ret_1"] > 0
    bearish_candle = df["ret_1"] < 0
    up = df["struct_uptrend"] & near_ema & bullish_candle
    down = df["struct_downtrend"] & near_ema & bearish_candle
    return pd.Series(np.select([up, down], ["UP", "DOWN"], default=None), index=df.index)


def hyp_e_opening_range(df: pd.DataFrame) -> pd.Series:
    """After each session's first `OPENING_RANGE_BARS` bars complete, a
    breakout above/below that opening range (computed per-session, using
    only that session's own opening bars) bets continuation in the
    breakout direction. Bars inside the opening range itself never fire
    (nothing to break out of yet)."""
    grp = df.groupby("session_date")
    or_high = grp["h"].transform(lambda s: s.iloc[:OPENING_RANGE_BARS].max())
    or_low = grp["l"].transform(lambda s: s.iloc[:OPENING_RANGE_BARS].min())
    after_opening = df["minutes_since_open"] >= OPENING_RANGE_BARS * 5
    up = after_opening & (df["c"] > or_high)
    down = after_opening & (df["c"] < or_low)
    return pd.Series(np.select([up, down], ["UP", "DOWN"], default=None), index=df.index)


def hyp_f_volatility_expansion(df: pd.DataFrame) -> pd.Series:
    """Volatility has JUST expanded (current ATR well above its own 20-bar
    average) -- direction bet is the naive one, the sign of the bar's own
    return, explicitly to test whether direction is predictable GIVEN
    expansion (the brief's own "expansion alone does not imply direction --
    measure whether direction is predictable separately" instruction)."""
    expanded = df["vol_expansion_ratio"] >= VOL_EXPANSION_RATIO
    up = expanded & (df["ret_1"] > 0)
    down = expanded & (df["ret_1"] < 0)
    return pd.Series(np.select([up, down], ["UP", "DOWN"], default=None), index=df.index)


def hyp_g_mtf_structure(df: pd.DataFrame) -> pd.Series:
    """Real HTF bias (from features.add_htf_bias, backward-joined, never
    fabricated finer timeframes) agrees with a genuine 5m-level pullback/
    trend confirmation (reuses hypothesis D's own confirmation condition) --
    bet the HTF direction, only when the current timeframe structure
    confirms it."""
    d_signal = hyp_d_pullback_in_trend(df)
    up = (df["htf_regime"] == "BULLISH") & (d_signal == "UP")
    down = (df["htf_regime"] == "BEARISH") & (d_signal == "DOWN")
    return pd.Series(np.select([up, down], ["UP", "DOWN"], default=None), index=df.index)


HYPOTHESES = {
    "A_TREND_CONTINUATION": hyp_a_trend_continuation,
    "B_BREAKOUT": hyp_b_breakout,
    "C_MEAN_REVERSION": lambda df: hyp_c_mean_reversion(df, bet="reversion"),
    "C_MEAN_CONTINUATION": lambda df: hyp_c_mean_reversion(df, bet="continuation"),
    "D_PULLBACK_IN_TREND": hyp_d_pullback_in_trend,
    "E_OPENING_RANGE": hyp_e_opening_range,
    "F_VOLATILITY_EXPANSION": hyp_f_volatility_expansion,
    "G_MTF_STRUCTURE": hyp_g_mtf_structure,
}
