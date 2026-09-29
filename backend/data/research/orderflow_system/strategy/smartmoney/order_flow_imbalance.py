"""
SM-1 -- Order Flow Imbalance (OFI). True mode: net signed market-order
pressure from real bid/ask change + aggressor side (Dhan WS depth, Mode A
-- see data/source_tick.py, only usable once enough real ticks accumulate).

PROXY MODE (Mode C, what actually runs today): wraps the SAME delta/cvd
columns data/source_ohlcv_proxy.py already computes (body/volume/range-
based estimate) -- this is NOT a second, different estimate, it's the one
proxy this codebase has, exposed through the SM-x contract so it can be
scored/gated alongside the real detectors. is_proxy=True always in this
mode; never silently reported as equivalent to a real OFI reading.
"""
from __future__ import annotations

from .base import SMResult


def detect(df, i: int, cfg: dict) -> SMResult:
    if "delta" not in df.columns or i < 1:
        return SMResult.absent(is_proxy=True)
    lb = cfg["ofi_proxy_lookback"]
    window = df["delta"].abs().iloc[max(0, i - lb):i]
    baseline = window.mean() if len(window) else None
    delta_i = df["delta"].iloc[i]
    if not baseline or baseline == 0:
        return SMResult.absent(is_proxy=True)
    magnitude = abs(delta_i) / baseline
    if magnitude < 1.0:
        return SMResult.absent(is_proxy=True)
    direction = "bull" if delta_i > 0 else "bear"
    return SMResult(present=True, direction=direction, strength=min(magnitude / 3.0, 1.0),
                    confidence=min(magnitude / 3.0, 1.0), is_proxy=True,
                    detail={"delta": delta_i, "baseline": baseline})
