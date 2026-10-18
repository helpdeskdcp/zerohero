"""Candlestick pattern detectors, causal 'turning zones' and an honest bracket simulator. PURE numpy, READ-ONLY research.

Every detector looks at the CLOSED bar i (and earlier bars) only. Entries are executed at the NEXT bar open. Zones are causal definitions of 'the market may turn
here' (swing exhaustion, previous-day level, liquidity sweep reclaim). Nothing uses information after bar i. The bracket simulator is pessimistic: stop first when a bar
touches both, gap fills at the open, time stop, costs applied by the caller."""
from __future__ import annotations

import numpy as np

PATTERNS_LONG = ("bull_maru", "bull_engulf", "bull_harami", "hammer")
PATTERNS_SHORT = ("bear_maru", "bear_engulf", "bear_harami", "shooting")
ZONES = ("none", "exhaust", "prevday", "sweep")


def _prev(a, n=1, fill=np.nan):
    out = np.full_like(a, fill, dtype=float)
    out[n:] = a[:-n]
    return out


def patterns(o, h, l, c, atr, body_ratio=0.85, min_range_atr=0.6) -> dict[str, np.ndarray]:
    rng = h - l
    body = np.abs(c - o)
    ok = (rng >= min_range_atr * atr) & (rng > 0)                       # ignore tiny candles
    po, pc, ph, pl = _prev(o), _prev(c), _prev(h), _prev(l)
    prng = ph - pl
    pbody = np.abs(pc - po)
    pok = (prng >= min_range_atr * atr) & (prng > 0)
    bull, bear = c > o, c < o
    pbull, pbear = pc > po, pc < po
    up_w, lo_w = h - np.maximum(o, c), np.minimum(o, c) - l
    out = {
        "bull_maru": ok & bull & (body >= body_ratio * rng),
        "bear_maru": ok & bear & (body >= body_ratio * rng),
        # engulfing: current body fully engulfs the previous (opposite) body
        "bull_engulf": ok & pok & pbear & bull & (o <= pc) & (c >= po),
        "bear_engulf": ok & pok & pbull & bear & (o >= pc) & (c <= po),
        # harami: current body sits INSIDE the previous (large, opposite) body
        "bull_harami": pok & (pbody >= 0.5 * prng) & pbear & bull & (np.maximum(o, c) < po) & (np.minimum(o, c) > pc),
        "bear_harami": pok & (pbody >= 0.5 * prng) & pbull & bear & (np.maximum(o, c) < pc) & (np.minimum(o, c) > po),
        # hammer / shooting star: long rejection wick, small opposite wick
        "hammer": ok & (lo_w >= 2 * np.maximum(body, 0.1 * rng)) & (up_w <= 0.5 * np.maximum(body, 0.1 * rng)),
        "shooting": ok & (up_w >= 2 * np.maximum(body, 0.1 * rng)) & (lo_w <= 0.5 * np.maximum(body, 0.1 * rng)),
    }
    return {k: np.nan_to_num(v.astype(float)).astype(bool) for k, v in out.items()}


def zones(o, h, l, c, atr, day, nslot_pos) -> dict[str, np.ndarray]:
    """Per bar: {zone: (long_ok, short_ok)}. All causal.
    exhaust : price fell >= 2.5 ATR over the last 12 bars AND this bar's low is in the lowest 10% of the last 60 bars (mirror for shorts)
    prevday : this bar's low is within 0.25 ATR of the previous session's low or below it (high for shorts)
    sweep   : this bar pierced the lowest low of the previous 20 bars but CLOSED back above it (high / below for shorts)"""
    n = len(c)
    c12 = _prev(c, 12)
    lo60 = np.full(n, np.nan); hi60 = np.full(n, np.nan); lo20 = np.full(n, np.nan); hi20 = np.full(n, np.nan)
    for i in range(60, n):
        w = slice(i - 60, i)
        lo60[i], hi60[i] = l[w].min(), h[w].max()
        lo20[i], hi20[i] = l[i - 20:i].min(), h[i - 20:i].max()
    pos_lo = (l - lo60) / np.where(hi60 > lo60, hi60 - lo60, np.nan)                 # 0 = at the 60-bar low
    pos_hi = (hi60 - h) / np.where(hi60 > lo60, hi60 - lo60, np.nan)
    ex_l = ((c12 - c) >= 2.5 * atr) & (pos_lo <= 0.10)
    ex_s = ((c - c12) >= 2.5 * atr) & (pos_hi <= 0.10)
    # previous session high / low
    pdl = np.full(n, np.nan); pdh = np.full(n, np.nan)
    ud = np.unique(day)
    dh = {d: h[day == d].max() for d in ud}
    dl = {d: l[day == d].min() for d in ud}
    prev_of = {ud[k]: ud[k - 1] for k in range(1, len(ud))}
    for d, pd_ in prev_of.items():
        m = day == d
        pdl[m], pdh[m] = dl[pd_], dh[pd_]
    pv_l = l <= pdl + 0.25 * atr
    pv_s = h >= pdh - 0.25 * atr
    sw_l = (l < lo20) & (c > lo20)
    sw_s = (h > hi20) & (c < hi20)
    t = np.ones(n, bool)
    f = lambda a: np.nan_to_num(a.astype(float)).astype(bool)
    return {"none": (t, t), "exhaust": (f(ex_l), f(ex_s)), "prevday": (f(pv_l), f(pv_s)), "sweep": (f(sw_l), f(sw_s))}


def bracket(o, h, l, c, day, idx, side, stop_dist, rr, max_bars=24):
    """Enter at the open of bar idx+1; stop = entry -/+ stop_dist (an array), target = rr * stop_dist; max_bars time stop and exit at the session's last bar.
    Returns signed gross bps per event (NaN if the window leaves the session / data). Adverse-first inside a bar; gaps fill at the open."""
    n = len(c)
    e = idx + 1
    ok = (e + max_bars < n)
    e = np.where(ok, e, 0)
    ent = o[e]
    same = np.ones(len(idx), bool)
    out = np.full(len(idx), np.nan)
    H = np.stack([h[np.minimum(e + j, n - 1)] for j in range(max_bars)], 1)
    L = np.stack([l[np.minimum(e + j, n - 1)] for j in range(max_bars)], 1)
    Cc = np.stack([c[np.minimum(e + j, n - 1)] for j in range(max_bars)], 1)
    Op = np.stack([o[np.minimum(e + j, n - 1)] for j in range(max_bars)], 1)
    D = np.stack([day[np.minimum(e + j, n - 1)] for j in range(max_bars)], 1)
    sess = D == day[idx][:, None]                                                 # stay inside the signal's session
    last = np.where(sess.all(1), max_bars - 1, np.argmin(sess, 1) - 1)            # last in-session bar of the window
    stop = ent - side * stop_dist
    tgt = ent + side * rr * stop_dist
    res = np.zeros(len(idx))
    for k in range(len(idx)):
        if not ok[k] or last[k] < 0 or stop_dist[k] <= 0 or not np.isfinite(ent[k]):
            res[k] = np.nan
            continue
        s, t_ = side[k], tgt[k]
        px = None
        for j in range(last[k] + 1):
            op, hi, lo = Op[k, j], H[k, j], L[k, j]
            if s > 0:
                if op <= stop[k]:
                    px = op; break
                if lo <= stop[k]:
                    px = stop[k]; break
                if hi >= t_:
                    px = max(op, t_); break
            else:
                if op >= stop[k]:
                    px = op; break
                if hi >= stop[k]:
                    px = stop[k]; break
                if lo <= t_:
                    px = min(op, t_); break
        if px is None:
            px = Cc[k, last[k]]                                                   # time / end-of-session exit at the close
        res[k] = s * (px - ent[k]) / ent[k] * 1e4
    return res
