"""
SM-3 -- Delta/CVD Divergence. Price makes a new extreme (over
divergence_lookback bars); CVD does NOT also make a new extreme --
selling/buying pressure petering out even as price still pushed further,
the classic divergence read.

PROXY MODE (Mode C): CVD here is the SAME proxy cvd column
data/source_ohlcv_proxy.py computes (session-reset, tick-rule signed
volume) -- is_proxy=True always. This is a generalized, standalone version
of the same divergence check strategy/_reversal.py's ReversalSetup already
performs internally at a specific candidate bar; this module exposes it as
an independently-callable SM-x detector for the context engine.
"""
from __future__ import annotations

from .base import SMResult


def detect(df, i: int, cfg: dict) -> SMResult:
    lb = cfg["divergence_lookback"]
    if i < lb + 1 or "cvd" not in df.columns:
        return SMResult.absent(is_proxy=True)
    price_window = df["close"].iloc[i - lb:i]
    cvd_window = df["cvd"].iloc[i - lb:i]
    price_i, cvd_i = df["close"].iloc[i], df["cvd"].iloc[i]

    if price_i > price_window.max() and cvd_i <= cvd_window.max():
        gap = (cvd_window.max() - cvd_i) / (abs(cvd_window.max()) + 1e-9)
        return SMResult(present=True, direction="bear", strength=min(gap, 1.0),
                        confidence=min(gap, 1.0), is_proxy=True,
                        detail={"event": "bearish_divergence"})
    if price_i < price_window.min() and cvd_i >= cvd_window.min():
        gap = (cvd_i - cvd_window.min()) / (abs(cvd_window.min()) + 1e-9)
        return SMResult(present=True, direction="bull", strength=min(gap, 1.0),
                        confidence=min(gap, 1.0), is_proxy=True,
                        detail={"event": "bullish_divergence"})
    return SMResult.absent(is_proxy=True)
