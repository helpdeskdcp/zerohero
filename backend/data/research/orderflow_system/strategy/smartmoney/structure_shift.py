"""
SM-8 -- Structure Shift (BOS / CHoCH). Real price-structure pattern
(is_proxy=False). A swing high/low is only CONFIRMED once
structure_swing_lookback bars have passed with nothing higher/lower --
standard lagged pivot confirmation, deliberately NOT a symmetric
(look-ahead) pivot definition. precompute_swings() adds the confirmed
swing-level columns ONCE (same pattern as strategy/_common.py's shared
context columns); detect() then just reads them at bar i.

BOS (Break of Structure, continuation): close breaks past the most
recently confirmed swing level in the SAME direction as the prior trend.
CHoCH (Change of Character, reversal warning): close breaks past the most
recently confirmed swing level AGAINST the prior trend.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import SMResult


def precompute_swings(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    lb = cfg["structure_swing_lookback"]
    out = df.copy()
    n = len(out)
    highs, lows = out["high"].values, out["low"].values
    confirmed_high = np.full(n, np.nan)
    confirmed_low = np.full(n, np.nan)
    last_high, last_low = np.nan, np.nan
    for i in range(2 * lb, n):
        j = i - lb  # candidate pivot bar, confirmed now that lb bars have passed after it
        window_lo, window_hi = j - lb, j + lb + 1
        if highs[j] == highs[window_lo:window_hi].max():
            last_high = highs[j]
        if lows[j] == lows[window_lo:window_hi].min():
            last_low = lows[j]
        confirmed_high[i] = last_high
        confirmed_low[i] = last_low
    out["confirmed_swing_high"] = confirmed_high
    out["confirmed_swing_low"] = confirmed_low
    return out


def detect(df, i: int, cfg: dict) -> SMResult:
    """df must already have confirmed_swing_high/low columns (call
    precompute_swings() once on the full frame before scanning)."""
    if "confirmed_swing_high" not in df.columns or i < 1:
        return SMResult.absent(is_proxy=False)
    swing_high = df["confirmed_swing_high"].iloc[i - 1]  # the level as of the PRIOR bar's close
    swing_low = df["confirmed_swing_low"].iloc[i - 1]
    close_i = df["close"].iloc[i]
    trend = df["htf_trend"].iloc[i] if "htf_trend" in df.columns else "flat"

    if swing_high == swing_high and close_i > swing_high:  # NaN-safe
        event = "BOS" if trend == "bullish" else "CHoCH"
        return SMResult(present=True, direction="bull", strength=1.0,
                        confidence=1.0 if event == "BOS" else 0.6, is_proxy=False,
                        detail={"event": event, "level": swing_high})
    if swing_low == swing_low and close_i < swing_low:
        event = "BOS" if trend == "bearish" else "CHoCH"
        return SMResult(present=True, direction="bear", strength=1.0,
                        confidence=1.0 if event == "BOS" else 0.6, is_proxy=False,
                        detail={"event": event, "level": swing_low})
    return SMResult.absent(is_proxy=False)
