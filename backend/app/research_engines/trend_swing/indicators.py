"""Daily indicators for the trend-swing harness. Causal: value[i] uses only
bars <= i. Reuses orderflow.indicators for the shared primitives."""
from __future__ import annotations

from ..orderflow import indicators as OI

ema = OI.ema
atr = OI.atr
adx = OI.adx
efficiency_ratio = OI.efficiency_ratio
pctl = OI.pctl


def donchian(h: list[float], l: list[float], n: int):
    """Prior-N-bar high / low, EXCLUDING the current bar (so close>hi is a true
    breakout). Falls back to include-current for the first bar."""
    hi = [None] * len(h)
    lo = [None] * len(l)
    for i in range(len(h)):
        a = max(0, i - n)
        seg_h = h[a:i] or h[a:i + 1]
        seg_l = l[a:i] or l[a:i + 1]
        hi[i] = max(seg_h)
        lo[i] = min(seg_l)
    return hi, lo


def slope(x: list[float], k: int) -> list[float]:
    out = [0.0] * len(x)
    for i in range(k, len(x)):
        if x[i] is not None and x[i - k] is not None:
            out[i] = (x[i] - x[i - k]) / k
    return out
