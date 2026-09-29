"""
SM-6 -- Fair Value Gap (FVG). A real, purely price-structure pattern (not a
proxy -- it's directly observable from OHLC, no order-flow inference
needed): a 3-candle imbalance where candle 1's high/low doesn't overlap
candle 3's low/high at all, leaving a gap candle 2 "raced through" without
two-way trade. is_proxy=False.

Bullish FVG at bar i (i is candle 3): high[i-2] < low[i] -- gap up.
Bearish FVG at bar i: low[i-2] > high[i] -- gap down.
strength = gap size / ATR (config atr_lookback) -- a gap smaller than
typical noise isn't meaningfully an imbalance.
"""
from __future__ import annotations

from .base import SMResult


def detect(df, i: int, cfg: dict) -> SMResult:
    """cfg: the smartmoney config block (cfg["smartmoney"] from the full
    resolved config) -- callers pass that sub-dict, not the whole config."""
    if i < 2:
        return SMResult.absent(is_proxy=False)
    min_gap_atr_mult = cfg["fvg_min_gap_atr_mult"]
    atr = df["atr"].iloc[i] if "atr" in df.columns else None
    if atr is None or atr != atr or atr <= 0:  # NaN-safe (early bars, before ATR warms up)
        return SMResult.absent(is_proxy=False)

    high_c1, low_c1 = df["high"].iloc[i - 2], df["low"].iloc[i - 2]
    high_c3, low_c3 = df["high"].iloc[i], df["low"].iloc[i]

    if high_c1 < low_c3:
        gap = low_c3 - high_c1
        if gap >= min_gap_atr_mult * atr:
            return SMResult(present=True, direction="bull", strength=min(gap / atr, 1.0),
                            confidence=min(gap / atr, 1.0), is_proxy=False,
                            detail={"gap_low": high_c1, "gap_high": low_c3, "formed_at": i})
    if low_c1 > high_c3:
        gap = low_c1 - high_c3
        if gap >= min_gap_atr_mult * atr:
            return SMResult(present=True, direction="bear", strength=min(gap / atr, 1.0),
                            confidence=min(gap / atr, 1.0), is_proxy=False,
                            detail={"gap_low": high_c3, "gap_high": low_c1, "formed_at": i})
    return SMResult.absent(is_proxy=False)


def is_filled(df, gap_detail: dict, direction: str, as_of_i: int) -> bool:
    """Has price since traded back INTO the gap zone (partial or full
    mitigation)? Only looks at bars strictly after the gap formed and at/
    before as_of_i -- no look-ahead."""
    formed_at = gap_detail["formed_at"]
    if as_of_i <= formed_at:
        return False
    window = df.iloc[formed_at + 1:as_of_i + 1]
    if direction == "bull":
        return bool((window["low"] <= gap_detail["gap_high"]).any())
    return bool((window["high"] >= gap_detail["gap_low"]).any())
