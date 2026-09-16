"""
Requirement 4: touch count, rejection count, rejection strength, and volume
confirmation for a clustered SR zone.

Extends (does not duplicate) app.engines.sr_engine._touches: that function
already establishes the touch/rejection band convention (+/-0.15*ATR around
the zone, a rejection = a close within 2 bars moving >= 0.3*ATR away). This
module reuses that exact convention and adds the two fields the existing
engine's _touches doesn't compute: rejection MAGNITUDE (not just a count)
and volume confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean


@dataclass
class TouchAnalysis:
    touches: int
    rejections: int
    rejection_strength: float    # mean rejection magnitude, in ATR units (0 if no rejections)
    volume_ratio: float | None   # mean volume on touch bars / mean volume overall; None if no real volume data
    last_touch_index: int | None


def analyze_touches(zone_low: float, zone_high: float, H: list, L: list, C: list, V: list,
                    atr: float) -> TouchAnalysis:
    if not atr or atr <= 0 or not C:
        return TouchAnalysis(0, 0, 0.0, None, None)

    lo, hi = zone_low - 0.15 * atr, zone_high + 0.15 * atr
    level = (zone_low + zone_high) / 2.0
    n = len(C)
    touches = rejections = 0
    last_touch_index = None
    rej_mags = []
    touch_vols = []

    for i in range(n):
        if L[i] <= hi and H[i] >= lo:
            touches += 1
            last_touch_index = i
            touch_vols.append(V[i] if i < len(V) else 0.0)
            for j in (i + 1, i + 2):
                if j < n and abs(C[j] - level) >= 0.3 * atr:
                    rejections += 1
                    rej_mags.append(abs(C[j] - level) / atr)
                    break

    rejection_strength = round(mean(rej_mags), 3) if rej_mags else 0.0

    volume_ratio = None
    baseline = [v for v in (V or []) if v]
    if touch_vols and baseline and mean(baseline) > 0:
        touch_vols_real = [v for v in touch_vols if v]
        if touch_vols_real:
            volume_ratio = round(mean(touch_vols_real) / mean(baseline), 3)

    return TouchAnalysis(touches=touches, rejections=rejections, rejection_strength=rejection_strength,
                         volume_ratio=volume_ratio, last_touch_index=last_touch_index)
