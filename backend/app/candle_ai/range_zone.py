"""Range & zone signals (rule: data/research/range_zone/SPEC_2026-10-24.md). Pure function on 5-minute bars; every value at bar t uses bars <= t only."""
from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from . import patterns as P

N_BOX, BOX_ATR, EDGE_ATR, FADE_ATR, MIN_TOUCH = 24, 3.0, 0.25, 0.10, 2
VARIANTS = ("BRK_UP", "BRK_DN", "FADE_LONG", "FADE_SHORT")
SIDE = {"BRK_UP": 1, "BRK_DN": -1, "FADE_LONG": 1, "FADE_SHORT": -1}


def box(df: pd.DataFrame, require_touches: bool = True) -> pd.DataFrame:
    """For every bar t: the box of the N bars BEFORE t. Columns rh, rl, mid, atr (ATR at t-1), valid."""
    o, h, l, c = (df[k].values.astype(float) for k in ("o", "h", "l", "c")); n = len(df); a = np.roll(P.atr(h, l, c), 1); a[0] = np.nan
    ist = df["t"].dt.tz_convert("Asia/Kolkata"); tod = ((ist.dt.hour * 60 + ist.dt.minute - (9 * 60 + 15)) // 5).values
    rh = np.full(n, np.nan); rl = np.full(n, np.nan); ok = np.zeros(n, bool)
    if n > N_BOX:
        wh = sliding_window_view(h, N_BOX)[:-1]; wl = sliding_window_view(l, N_BOX)[:-1]          # window k covers bars k..k+N-1 -> belongs to t = k+N
        rhk, rlk = wh.max(axis=1), wl.min(axis=1); ak = a[N_BOX:]
        okk = ((rhk - rlk) <= BOX_ATR * ak)
        if require_touches: okk &= ((wh >= (rhk - EDGE_ATR * ak)[:, None]).sum(axis=1) >= MIN_TOUCH) & ((wl <= (rlk + EDGE_ATR * ak)[:, None]).sum(axis=1) >= MIN_TOUCH)
        rh[N_BOX:], rl[N_BOX:], ok[N_BOX:] = rhk, rlk, okk
    ok &= (tod >= N_BOX) & np.isfinite(a)
    return pd.DataFrame({"rh": rh, "rl": rl, "mid": (rh + rl) / 2, "atr": a, "valid": ok}, index=df.index)


def signals(df: pd.DataFrame, require_touches: bool = True) -> pd.DataFrame:
    o, h, l, c = (df[k].values.astype(float) for k in ("o", "h", "l", "c")); b = box(df, require_touches); v = b["valid"].values; a = b["atr"].values; rh = b["rh"].values; rl = b["rl"].values
    out = b.copy()
    out["BRK_UP"] = v & (c > rh); out["BRK_DN"] = v & (c < rl)
    out["FADE_LONG"] = v & (l <= rl + FADE_ATR * a) & (c > rl) & (c > o); out["FADE_SHORT"] = v & (h >= rh - FADE_ATR * a) & (c < rh) & (c < o)
    return out
