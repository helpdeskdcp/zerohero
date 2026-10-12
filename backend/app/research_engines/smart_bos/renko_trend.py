"""Renko trend-continuation entries (research). Independent of BoS/CHoCH.

An entry candidate is generated at the first bar where the Renko run in the current
direction reaches `min_run` bricks (a new brick printed on that bar). Fill/exit are
modelled by sim.simulate(mode="renko") exactly as for the Pine signals: next-bar open,
brick-sized SL, brick-multiple targets T1..T4. Causal: Renko state at bar i uses
bars <= i and a brick size fixed from PRIOR sessions only.
"""
from __future__ import annotations

import numpy as np

from .data import ist_minutes
from .engine import Signal


def generate(bars: dict, rs, *, min_run: int = 3, entry=(560, 925), first_only: bool = True, side_filter: int = 0):
    t, c = bars["t"], bars["c"]
    days, mod = ist_minutes(t)
    dirs, runs, bricks, nb = rs.dirs, rs.runs, rs.bricks, rs.brick_count
    out = []
    for i in range(1, len(t)):
        if nb[i] == nb[i - 1] or dirs[i] == 0 or bricks[i] <= 0 or days[i] != days[i - 1]:
            continue
        if not (entry[0] <= mod[i] < entry[1]) or runs[i] < min_run:
            continue
        if first_only and runs[i] != min_run:
            continue
        if side_filter and dirs[i] != side_filter:
            continue
        out.append(Signal(i, int(dirs[i]), "RENKO", c[i], 0.0, (), float(bricks[i]), 0.0, int(days[i]),
                          int(dirs[i]), int(runs[i]), float(bricks[i])))
    return out
