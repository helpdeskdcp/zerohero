"""
Requirement 3: cluster nearby swing prices into zones instead of treating
every pivot as its own level.

Reuses app.engines.sr_engine._cluster directly (existing weighted-merge
clusterer) rather than re-implementing clustering. This module only adds the
swing-pivot -> candidate conversion and a typed Zone result shaped for the
Dynamic SR Engine's output contract (zone_low/zone_high, not the existing
engine's [_min,_max] pair naming).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..engines.sr_engine import _cluster
from .pivots import SwingPivot


@dataclass
class SRZone:
    level: float
    zone_low: float
    zone_high: float
    sources: list = field(default_factory=list)
    swing_count: int = 0


def swings_to_candidates(swings: list[SwingPivot], *, n_bars: int, base_weight: float = 0.9) -> list[tuple]:
    """(level, source, weight) tuples, weight decayed by recency exactly like
    the existing engine's swing candidates (sr_engine._candidates)."""
    cands = []
    for s in swings:
        age = (n_bars - 1 - s.index) / max(1, n_bars)
        src = "swing_high" if s.kind == "H" else "swing_low"
        cands.append((s.price, src, base_weight * (1 - 0.5 * age)))
    return cands


def cluster_swings(swings: list[SwingPivot], *, n_bars: int, merge: float) -> list[SRZone]:
    """Cluster confirmed swing pivots into SR zones. `merge` is the price
    distance below which two candidates are treated as the same zone --
    callers pass an ATR-relative value (e.g. 0.25*atr) so this scales
    correctly across instruments of very different price levels."""
    if not swings or merge <= 0:
        return []
    cands = swings_to_candidates(swings, n_bars=n_bars)
    raw = _cluster(cands, merge)
    zones = []
    for z in raw:
        zones.append(SRZone(
            level=round(z["level"], 4), zone_low=round(z["_min"], 4), zone_high=round(z["_max"], 4),
            sources=z["sources"], swing_count=len(z["members"]),
        ))
    return sorted(zones, key=lambda z: z.level)
