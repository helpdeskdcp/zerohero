"""Pivot-line reversal: TA-Lib engulfing / harami near a classical or Fibonacci pivot line after a consolidation box. Rule fixed in
data/research/pivot_reversal/SPEC_2026-10-24.md. Pure function on 5-minute bars (t UTC tz-aware, o, h, l, c, v); every flag at bar t uses bars <= t only."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import levels as L, patterns as P

PROX_ATR, BOX_BARS, BOX_ATR = 0.30, 10, 2.5
VARIANTS = ("ENGULF_BUY", "ENGULF_SELL", "HARAMI_BUY", "HARAMI_SELL")


def pivot_lines(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """13 lines per bar from the previous completed session: classical P, R1-R3, S1-S3 and Fibonacci R1-R3, S1-S3."""
    lev = L.previous_period_levels(df).reset_index(drop=True); H, Lw, C = lev["PDH"], lev["PDL"], lev["PDC"]; p = (H + Lw + C) / 3.0; rng = H - Lw
    out = pd.DataFrame({"P": p, "R1": 2 * p - Lw, "S1": 2 * p - H, "R2": p + rng, "S2": p - rng, "R3": H + 2 * (p - Lw), "S3": Lw - 2 * (H - p),
                        "FR1": p + 0.382 * rng, "FR2": p + 0.618 * rng, "FR3": p + rng, "FS1": p - 0.382 * rng, "FS2": p - 0.618 * rng, "FS3": p - rng})
    return out, list(out.columns)


def signals(df: pd.DataFrame, need_talib: bool = True) -> pd.DataFrame:
    """Boolean columns ENGULF_BUY / ENGULF_SELL / HARAMI_BUY / HARAMI_SELL plus the filter pieces (pattern, near, box) so the study can drop one filter at a time."""
    import talib
    o, h, l, c = (df[k].values.astype(float) for k in ("o", "h", "l", "c")); n = len(df)
    a = P.atr(h, l, c); lines, _ = pivot_lines(df); lv = lines.values
    ist = df["t"].dt.tz_convert("Asia/Kolkata"); tod_bar = ((ist.dt.hour * 60 + ist.dt.minute - (9 * 60 + 15)) // 5).values
    near_low = (np.abs(l[:, None] - lv) <= PROX_ATR * a[:, None]); near_high = (np.abs(h[:, None] - lv) <= PROX_ATR * a[:, None])
    nl = np.nan_to_num(near_low, nan=0).any(axis=1); nh = np.nan_to_num(near_high, nan=0).any(axis=1)
    hh = pd.Series(h).shift(1).rolling(BOX_BARS, min_periods=BOX_BARS).max().values; ll = pd.Series(l).shift(1).rolling(BOX_BARS, min_periods=BOX_BARS).min().values
    box = ((hh - ll) <= BOX_ATR * np.roll(a, 1)) & (tod_bar >= BOX_BARS)                 # the 10 bars before t are all in today's session (t is at least the 11th bar)
    eng = talib.CDLENGULFING(o, h, l, c); har = talib.CDLHARAMI(o, h, l, c)
    out = pd.DataFrame({"eng_bull": eng == 100, "eng_bear": eng == -100, "har_bull": har == 100, "har_bear": har == -100, "near_low": nl, "near_high": nh, "box": box}, index=df.index)
    for nm, pat, near in (("ENGULF_BUY", "eng_bull", "near_low"), ("ENGULF_SELL", "eng_bear", "near_high"), ("HARAMI_BUY", "har_bull", "near_low"), ("HARAMI_SELL", "har_bear", "near_high")):
        out[nm] = out[pat] & out[near] & out["box"]
    return out
