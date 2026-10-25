"""Signal path of Winners Scalper Pro v4.5 IN (Pine v6): ATR trailing stops + ADX/MA strength filter + dir flips. Bar-by-bar, strictly causal.
Pine quirks reproduced on purpose: ta.barssince(dir != dir[1]) is na before the first flip (so isTrendStable is false until then); longStopPrev = nz(longStop[1], longStop) uses the previous bar's
FINAL (adjusted) stop; hasStrongFired is reset first, then read, then updated."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..zone_breakout.strategy import rma


@dataclass
class Params:
    length: int = 6
    mult: float = 2.1
    ma_len: int = 34
    adx_len: int = 16
    adx_thresh: float = 14.0
    slope_pct: float = 0.05
    min_bars: int = 0
    tp_mult: tuple = (1.5, 3.0, 4.0)


def dmi(h, l, c, n: int):
    up = np.r_[np.nan, np.diff(h)]; dn = -np.r_[np.nan, np.diff(l)]
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0); minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0); plus_dm[0] = minus_dm[0] = np.nan
    pc = np.r_[np.nan, c[:-1]]; tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    trr = rma(tr, n); p = 100 * rma(plus_dm, n) / trr; m = 100 * rma(minus_dm, n) / trr; s = p + m
    adx = 100 * rma(np.abs(p - m) / np.where(s == 0, 1.0, s), n); return p, m, adx


def atr_series(h, l, c, n: int):
    pc = np.r_[np.nan, c[:-1]]; tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0); return rma(tr, n)


def signals(df: pd.DataFrame, p: Params | None = None) -> pd.DataFrame:
    """df: timestamp, open, high, low, close. Returns df with atr, adx, ma, long_stop, short_stop, dir, initial_buy/sell, late_buy/sell, buy, sell, strong, tp1..tp3."""
    p = p or Params(); d = df.reset_index(drop=True).copy(); h, l, c = (d[k].to_numpy(float) for k in ("high", "low", "close")); n = len(d)
    atr = atr_series(h, l, c, p.length); _, _, adx = dmi(h, l, c, p.adx_len); ma = pd.Series(c).rolling(p.ma_len, min_periods=p.ma_len).mean().values
    hi = pd.Series(c).rolling(p.length, min_periods=p.length).max().values; lo = pd.Series(c).rolling(p.length, min_periods=p.length).min().values
    ls = np.full(n, np.nan); ss = np.full(n, np.nan); dr = np.ones(n, int); ib = np.zeros(n, bool); isl = np.zeros(n, bool); lb = np.zeros(n, bool); lsl = np.zeros(n, bool); strong = np.zeros(n, bool)
    fired = False; last_change = None; cur_dir = 1; tp = np.full((n, 3), np.nan)
    for i in range(n):
        raw_l = hi[i] - p.mult * atr[i]; raw_s = lo[i] + p.mult * atr[i]
        prev_l = ls[i - 1] if i > 0 and not np.isnan(ls[i - 1]) else raw_l; prev_s = ss[i - 1] if i > 0 and not np.isnan(ss[i - 1]) else raw_s
        cl = raw_l
        if i > 0 and c[i - 1] > prev_l: cl = max(raw_l, prev_l)                                   # NaN-safe: comparisons with NaN are False and max(nan, x) -> keep the Pine na behaviour below
        if np.isnan(raw_l): cl = np.nan
        cs = raw_s
        if i > 0 and c[i - 1] < prev_s: cs = min(raw_s, prev_s)
        if np.isnan(raw_s): cs = np.nan
        ls[i], ss[i] = cl, cs
        nd = cur_dir
        if c[i] > prev_s: nd = 1
        elif c[i] < prev_l: nd = -1
        flip_buy = nd == 1 and cur_dir == -1; flip_sell = nd == -1 and cur_dir == 1; changed = nd != cur_dir
        if changed: last_change = i
        cur_dir = nd; dr[i] = nd
        stable = last_change is not None and (i - last_change) >= p.min_bars
        if flip_buy or flip_sell: fired = False
        cs_buy = (adx[i] > p.adx_thresh) and (c[i] > ma[i] or c[i] > ls[i] * (1 + p.slope_pct / 100)) if not (np.isnan(adx[i]) or np.isnan(ma[i]) or np.isnan(ls[i])) else False
        cs_sell = (adx[i] > p.adx_thresh) and (c[i] < ma[i] or c[i] < ss[i] * (1 - p.slope_pct / 100)) if not (np.isnan(adx[i]) or np.isnan(ma[i]) or np.isnan(ss[i])) else False
        ib[i] = flip_buy and stable; isl[i] = flip_sell and stable
        lb[i] = nd == 1 and not fired and cs_buy and not flip_buy and stable; lsl[i] = nd == -1 and not fired and cs_sell and not flip_sell and stable
        if (ib[i] and cs_buy) or (isl[i] and cs_sell) or lb[i] or lsl[i]: fired = True
        strong[i] = (ib[i] and cs_buy) or lb[i] or (isl[i] and cs_sell) or lsl[i]
        if ib[i] or lb[i]: tp[i] = [c[i] + atr[i] * m for m in p.tp_mult]
        if isl[i] or lsl[i]: tp[i] = [c[i] - atr[i] * m for m in p.tp_mult]
    d["atr"], d["adx"], d["ma"], d["long_stop"], d["short_stop"], d["dir"] = atr, adx, ma, ls, ss, dr
    d["initial_buy"], d["initial_sell"], d["late_buy"], d["late_sell"] = ib, isl, lb, lsl; d["buy"] = ib | lb; d["sell"] = isl | lsl; d["strong"] = strong
    d["tp1"], d["tp2"], d["tp3"] = tp[:, 0], tp[:, 1], tp[:, 2]; return d
