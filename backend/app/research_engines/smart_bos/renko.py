"""Causal Renko brick state built from 1-minute closes.

Brick size is fixed per trading day from PRIOR sessions only (k x mean prior-N
session range), so no same-day information leaks into the size. Bricks reset at
each session start (a new day starts with no trend). Standard Renko rules: a brick
continues when price moves one brick beyond the last brick's far end, and reverses
only after price retraces one brick beyond the last brick's origin (i.e. 2 bricks
from its close).
"""
from __future__ import annotations

import numpy as np

from .data import ist_minutes


class RenkoState:
    """state_at(i) -> (dir, run, brick). dir in {-1,0,1}; run = consecutive bricks
    in the current direction (>=1 once the first brick prints, else 0)."""

    def __init__(self, dirs, runs, bricks, brick_count):
        self.dirs, self.runs, self.bricks, self.brick_count = dirs, runs, bricks, brick_count

    def state_at(self, i: int):
        return int(self.dirs[i]), int(self.runs[i]), float(self.bricks[i])


def daily_brick_sizes(t, h, l, session=(555, 930), k=0.05, lookback=5, mintick=0.05):
    """dict day -> brick size from the mean regular-session range of the prior `lookback` days."""
    days, mod = ist_minutes(t)
    ins = (mod >= session[0]) & (mod < session[1])
    rng = {}
    for i in np.nonzero(ins)[0]:
        d = int(days[i])
        a = rng.get(d)
        rng[d] = [h[i], l[i]] if a is None else [max(a[0], h[i]), min(a[1], l[i])]
    ds = sorted(rng)
    out = {}
    for k_i, d in enumerate(ds):
        prior = [rng[x][0] - rng[x][1] for x in ds[max(0, k_i - lookback):k_i]]
        if len(prior) >= 2:
            out[d] = max(mintick, k * float(np.mean(prior)))
    return out


def build(bars: dict, *, k: float = 0.05, lookback: int = 5, mintick: float = 0.05,
          session=(555, 930)) -> RenkoState:
    t, c, h, l = bars["t"], bars["c"], bars["h"], bars["l"]
    n = len(t)
    days, mod = ist_minutes(t)
    sizes = daily_brick_sizes(t, h, l, session, k, lookback, mintick)
    dirs, runs, bricks = np.zeros(n, np.int8), np.zeros(n, np.int32), np.zeros(n)
    nb = np.zeros(n, np.int32)
    cur_day, B = None, 0.0
    top = bot = np.nan
    d_, run_, cnt = 0, 0, 0
    for i in range(n):
        d = int(days[i])
        if d != cur_day:
            cur_day, B = d, sizes.get(d, 0.0)
            top = bot = np.nan
            d_, run_, cnt = 0, 0, 0
        if B > 0 and session[0] <= mod[i] < session[1]:
            px = c[i]
            if np.isnan(top):
                top = bot = px                      # anchor: first close of the session
            else:
                while True:
                    if d_ >= 0 and px >= top + B:   # up brick (continue or first)
                        bot, top = top, top + B
                        run_ = run_ + 1 if d_ == 1 else 1
                        d_, cnt = 1, cnt + 1
                    elif d_ <= 0 and px <= bot - B:  # down brick
                        top, bot = bot, bot - B
                        run_ = run_ + 1 if d_ == -1 else 1
                        d_, cnt = -1, cnt + 1
                    elif d_ == 1 and px <= bot - B:  # reversal down after an up brick
                        top, bot = bot, bot - B
                        d_, run_, cnt = -1, 1, cnt + 1
                    elif d_ == -1 and px >= top + B:  # reversal up after a down brick
                        bot, top = top, top + B
                        d_, run_, cnt = 1, 1, cnt + 1
                    else:
                        break
        dirs[i], runs[i], bricks[i], nb[i] = d_, run_, B, cnt
    return RenkoState(dirs, runs, bricks, nb)


def annotate(signals, rs: "RenkoState"):
    """Copies of signals tagged with the Renko state at the signal bar (causal: state_at(i) uses bars <= i)."""
    import copy
    out = []
    for s in signals:
        c = copy.copy(s)
        c.renko_dir, c.renko_run, c.brick = rs.state_at(s.i)
        out.append(c)
    return out
