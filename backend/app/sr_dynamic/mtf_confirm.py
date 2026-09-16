"""
Requirement 9: multi-timeframe confirmation. A zone found on one timeframe
is "confirmed" on another timeframe only if that OTHER timeframe's own,
independently-clustered zone list also has a level nearby -- never inferred
by copying the same swings across timeframes.
"""
from __future__ import annotations

TF_ORDER = ["5m", "15m", "30m", "1h"]


def confirmed_timeframes(level: float, zones_by_tf: dict, *, atr: float,
                         tolerance_atr_mult: float = 0.3) -> list[str]:
    """zones_by_tf: {tf: [SRZone, ...]}, each list already independently
    clustered on that timeframe's own bars. Returns the subset of TF_ORDER
    present in zones_by_tf whose own zones contain a level within
    tolerance_atr_mult*atr of `level`."""
    if not atr or atr <= 0:
        return []
    tol = tolerance_atr_mult * atr
    out = []
    for tf in TF_ORDER:
        zones = zones_by_tf.get(tf)
        if not zones:
            continue
        if any(abs(z.level - level) <= tol for z in zones):
            out.append(tf)
    return out
