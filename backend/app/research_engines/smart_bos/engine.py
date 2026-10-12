"""Faithful, causal Python port of the Pine v6 indicator "Smart NSE + MCX | BoS CHoCH Safety".

Research only: produces SIGNALS (entry-bar, side, planned SL/T1-T3). Execution
assumptions live in sim.py. No broker/order path; nothing here is imported by the
live autoscalp.

Port notes (Pine behaviours that are ambiguous or differ from naive expectations):
* ta.atr(14): the Pine code hard-codes 14 and ignores its own `atrLen` input; the
  port exposes atr_len (default 14 = identical to Pine).
* ta.pivothigh/pivotlow(len, len): a pivot is reported `len` bars after it forms.
  Tie handling is not specified in the Pine reference we have, so it is a parameter
  (`pivot_ties`: "allow" = equal neighbours do not disqualify, "strict" = must be
  strictly higher/lower). Robustness is checked by running both.
* Swing/trend state is NOT reset per session (matches Pine `var`), BoS/CHoCH state
  therefore carries overnight.
* "Previous completed daily bar" is the previous IST trading date's regular-session
  H/L/C. A previous day with < 90% of its expected bars is treated as not ready
  (partial capture would give wrong levels). Only yesterday's completeness is used
  (known at decision time); today's bar count is never inspected.
* Signal = bar close of a confirmed 1-minute bar (Pine alert semantics). Fills are
  modelled in sim.py (next-bar open), never at the signal close.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .data import ist_minutes


@dataclass
class Params:
    pivot_len: int = 3
    atr_len: int = 14
    atr_mult: float = 0.25
    min_buffer: float = 0.5
    mintick: float = 0.05
    rr: tuple = (2.0, 3.0, 4.0)
    pd_buffer: float = 0.0
    max_candle_atr: float = 2.0
    opening_minutes: int = 5
    session: tuple = (555, 930)          # 09:15-15:30 IST, minute-of-day [start, end)
    entry: tuple = (560, 925)            # 09:20-15:25
    pivot_ties: str = "allow"
    min_day_bars_frac: float = 0.9
    weekdays_only: bool = True


@dataclass
class Signal:
    i: int                  # index of the signal (confirmed) bar
    side: int               # +1 buy / -1 sell
    kind: str               # "BoS" | "CHoCH"
    close: float
    sl: float
    targets: tuple
    risk: float
    atr: float
    day: int
    renko_dir: int = 0
    renko_run: int = 0
    brick: float = 0.0
    ctx: dict = field(default_factory=dict)


def _atr(h, l, c, n):
    tr = np.empty_like(h)
    tr[0] = h[0] - l[0]
    tr[1:] = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])])
    out = np.full_like(h, np.nan)
    if len(h) >= n:
        out[n - 1] = tr[:n].mean()
        for i in range(n, len(h)):
            out[i] = (tr[i] + (n - 1) * out[i - 1]) / n
    return out


def _pivots(a, n, high: bool, ties: str):
    """pivot[i] = value of the pivot bar i-n, known at bar i (nan otherwise)."""
    out = np.full_like(a, np.nan)
    for i in range(2 * n, len(a)):
        c = i - n
        v = a[c]
        left, right = a[c - n:c], a[c + 1:i + 1]
        if high:
            ok = (v > left.max() and v > right.max()) if ties == "strict" else (v >= left.max() and v >= right.max())
        else:
            ok = (v < left.min() and v < right.min()) if ties == "strict" else (v <= left.min() and v <= right.min())
        if ok:
            out[i] = v
    return out


def previous_day_levels(t, h, l, c, p: Params):
    """dict day -> (pdh, pdl, pdc, complete) built from regular-session bars only."""
    days, mod = ist_minutes(t)
    exp = p.session[1] - p.session[0]
    ins = (mod >= p.session[0]) & (mod < p.session[1])
    agg = {}
    for i in np.nonzero(ins)[0]:
        d = int(days[i])
        a = agg.get(d)
        if a is None:
            agg[d] = [h[i], l[i], c[i], 1]
        else:
            a[0] = max(a[0], h[i]); a[1] = min(a[1], l[i]); a[2] = c[i]; a[3] += 1
    return {d: (a[0], a[1], a[2], a[3] >= p.min_day_bars_frac * exp) for d, a in agg.items()}, days, mod


def generate_signals(bars: dict, p: Params | None = None, renko=None) -> tuple[list[Signal], dict]:
    """Run the Pine state machine over 1-minute bars. `renko`, if given, is an
    object with .state_at(i) -> (dir, run, brick) used only to annotate signals."""
    p = p or Params()
    t, o, h, l, c = bars["t"], bars["o"], bars["h"], bars["l"], bars["c"]
    n = len(t)
    pd, days, mod = previous_day_levels(t, h, l, c, p)
    day_list = sorted(pd)
    prev_of = {d: (day_list[k - 1] if k else None) for k, d in enumerate(day_list)}
    atr = _atr(h, l, c, p.atr_len)
    ph = _pivots(h, p.pivot_len, True, p.pivot_ties)
    pl = _pivots(l, p.pivot_len, False, p.pivot_ties)
    wd = ((t + 19800) // 86400 + 4) % 7          # 0=Mon .. 6=Sun (epoch day 0 = Thursday)
    sig, stats = [], {"bars": n, "breaks_up": 0, "breaks_dn": 0, "days_skipped_incomplete": 0}

    swing_hi = swing_lo = np.nan
    swing_hi_bar = swing_lo_bar = -1
    hi_used = lo_used = False
    trend = 0
    s_open = or_hi = or_lo = np.nan
    s_start_t = None
    open_ready = open_locked = False
    prev_in = False
    open_s = p.opening_minutes * 60
    skipped_days = set()

    for i in range(n):
        in_mkt = p.session[0] <= mod[i] < p.session[1] and (not p.weekdays_only or wd[i] < 5)
        in_ent = p.entry[0] <= mod[i] < p.entry[1] and in_mkt
        if in_mkt and not prev_in:
            s_open, or_hi, or_lo, s_start_t = o[i], h[i], l[i], t[i]
            open_ready = open_locked = False
        prev_in = in_mkt
        if not np.isnan(ph[i]):
            swing_hi, swing_hi_bar, hi_used = ph[i], i - p.pivot_len, False
        if not np.isnan(pl[i]):
            swing_lo, swing_lo_bar, lo_used = pl[i], i - p.pivot_len, False
        if in_mkt and s_start_t is not None and t[i] < s_start_t + open_s and not open_locked:
            or_hi, or_lo = max(or_hi, h[i]), min(or_lo, l[i])
        if in_mkt and s_start_t is not None and t[i] + 60 >= s_start_t + open_s and not open_locked:
            open_ready = open_locked = True

        bull = in_mkt and not np.isnan(swing_hi) and not hi_used and c[i] > swing_hi and i > swing_hi_bar
        bear = in_mkt and not np.isnan(swing_lo) and not lo_used and c[i] < swing_lo and i > swing_lo_bar
        bull_choch, bear_choch = bull and trend == -1, bear and trend == 1
        if bull:
            hi_used, trend = True, 1
            stats["breaks_up"] += 1
        if bear:
            lo_used, trend = True, -1
            stats["breaks_dn"] += 1
        if not (bull or bear) or not in_ent:
            continue
        d = int(days[i])
        pdk = prev_of.get(d)
        if pdk is None or not pd[pdk][3]:             # previous day must exist and be complete (known at this time)
            if d not in skipped_days:
                skipped_days.add(d)
            continue
        pdh, pdl, pdc, _ = pd[pdk]
        if not (open_ready and not np.isnan(or_hi) and not np.isnan(atr[i])):
            continue
        near_pd = p.pd_buffer > 0 and (abs(c[i] - pdh) <= p.pd_buffer or abs(c[i] - pdl) <= p.pd_buffer
                                       or abs(c[i] - pdc) <= p.pd_buffer)
        if near_pd or (h[i] - l[i]) > atr[i] * p.max_candle_atr:
            continue
        buf = max(p.min_buffer, max(p.mintick, atr[i] * p.atr_mult))
        if bull and c[i] > pdc and c[i] > s_open and c[i] > or_hi:
            sl = l[i] - buf
            risk = c[i] - sl
            if risk > p.mintick:
                rd = renko.state_at(i) if renko is not None else (0, 0, 0.0)
                sig.append(Signal(i, 1, "CHoCH" if bull_choch else "BoS", c[i], sl,
                                  tuple(c[i] + risk * r for r in p.rr), risk, atr[i], d, *rd))
        elif bear and c[i] < pdc and c[i] < s_open and c[i] < or_lo:
            sl = h[i] + buf
            risk = sl - c[i]
            if risk > p.mintick:
                rd = renko.state_at(i) if renko is not None else (0, 0, 0.0)
                sig.append(Signal(i, -1, "CHoCH" if bear_choch else "BoS", c[i], sl,
                                  tuple(c[i] - risk * r for r in p.rr), risk, atr[i], d, *rd))
    stats["days_skipped_incomplete"] = len(skipped_days)
    stats["signals"] = len(sig)
    return sig, stats
