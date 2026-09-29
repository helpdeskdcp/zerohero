"""
SM-2 -- Absorption. High volume + small range at a level, with the side
that fails to move despite the volume (sell_absorbed / buy_absorbed).

PROXY MODE (Mode C): wraps the SAME is_absorption column
data/source_ohlcv_proxy.py already computes (volume-vs-rolling-mean +
range-vs-ATR test -- see that module's docstring for the exact formula).
Direction is inferred from which side of the bar's own range the close
sits: closing in the upper half of a high-volume/narrow-range bar reads as
sell_absorbed (bullish -- sellers' volume was absorbed without pushing
price down); closing in the lower half reads as buy_absorbed (bearish).
is_proxy=True always in this mode.
"""
from __future__ import annotations

from .base import SMResult


def detect(df, i: int, cfg: dict) -> SMResult:
    if "is_absorption" not in df.columns or not bool(df["is_absorption"].iloc[i]):
        return SMResult.absent(is_proxy=True)
    high_i, low_i, close_i = df["high"].iloc[i], df["low"].iloc[i], df["close"].iloc[i]
    rng = high_i - low_i
    close_pos = (close_i - low_i) / rng if rng > 0 else 0.5
    direction = "bull" if close_pos >= 0.5 else "bear"
    event = "sell_absorbed" if direction == "bull" else "buy_absorbed"
    strength = abs(close_pos - 0.5) * 2  # 0 at midpoint, 1 at either extreme
    return SMResult(present=True, direction=direction, strength=strength,
                    confidence=0.5 + strength / 2, is_proxy=True, detail={"event": event})
