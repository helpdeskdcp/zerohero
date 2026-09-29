"""
SM-9 -- Value Migration. Direction of POC/VAH/VAL shift over a rolling
window -- value area migrating up = bullish acceptance at higher prices;
migrating down = bearish. is_proxy=True: built on the same OHLCV-only
volume-profile approximation strategy/_common.py's compute_context_columns
already computes (each bar's volume concentrated at its own close -- see
that module's docstring), not a real intrabar volume profile.
"""
from __future__ import annotations

from .base import SMResult


def detect(df, i: int, cfg: dict) -> SMResult:
    lb = cfg["value_migration_lookback"]
    if i < lb or "poc" not in df.columns:
        return SMResult.absent(is_proxy=True)
    poc_now, poc_then = df["poc"].iloc[i], df["poc"].iloc[i - lb]
    if poc_now != poc_now or poc_then != poc_then:  # NaN-safe
        return SMResult.absent(is_proxy=True)
    shift = poc_now - poc_then
    atr = df["atr"].iloc[i] if "atr" in df.columns and df["atr"].iloc[i] == df["atr"].iloc[i] else None
    if not atr or atr <= 0:
        return SMResult.absent(is_proxy=True)
    magnitude = abs(shift) / atr
    if magnitude < 0.5:
        return SMResult.absent(is_proxy=True)
    direction = "bull" if shift > 0 else "bear"
    return SMResult(present=True, direction=direction, strength=min(magnitude / 3.0, 1.0),
                    confidence=min(magnitude / 3.0, 1.0), is_proxy=True,
                    detail={"poc_shift": shift})
