"""
SM-7 -- Liquidity Sweep. Real price-structure pattern (is_proxy=False):
a wick pokes beyond a recent swing high/low (where stop-losses/breakout
orders cluster -- "liquidity") then closes back inside it, tagged by which
side was swept.

swept_highs (bearish signal -- upside liquidity grabbed, then rejected):
  high[i] > prior_swing_high AND close[i] retraces back down by
  >= sweep_close_back_pct of the wick (close[i] <= high[i] - pct*(high[i]-prior_swing_high))
swept_lows (bullish signal): mirror on the low side.

prior_swing_high/low = rolling max/min of high/low over
sweep_swing_lookback bars ENDING AT i-1 (never including bar i itself --
the swing being swept must be a level that already existed before this bar).
"""
from __future__ import annotations

from .base import SMResult


def detect(df, i: int, cfg: dict) -> SMResult:
    lb = cfg["sweep_swing_lookback"]
    if i < lb + 1:
        return SMResult.absent(is_proxy=False)
    prior_swing_high = df["high"].iloc[i - lb:i].max()
    prior_swing_low = df["low"].iloc[i - lb:i].min()
    high_i, low_i, close_i = df["high"].iloc[i], df["low"].iloc[i], df["close"].iloc[i]
    back_pct = cfg["sweep_close_back_pct"]

    if high_i > prior_swing_high:
        wick = high_i - prior_swing_high
        if wick > 0 and close_i <= high_i - back_pct * wick:
            strength = min(wick / (df["atr"].iloc[i] or 1e-9), 1.0) if "atr" in df.columns else 0.5
            return SMResult(present=True, direction="bear", strength=strength, confidence=back_pct,
                            is_proxy=False, detail={"event": "swept_highs", "level": prior_swing_high})
    if low_i < prior_swing_low:
        wick = prior_swing_low - low_i
        if wick > 0 and close_i >= low_i + back_pct * wick:
            strength = min(wick / (df["atr"].iloc[i] or 1e-9), 1.0) if "atr" in df.columns else 0.5
            return SMResult(present=True, direction="bull", strength=strength, confidence=back_pct,
                            is_proxy=False, detail={"event": "swept_lows", "level": prior_swing_low})
    return SMResult.absent(is_proxy=False)
