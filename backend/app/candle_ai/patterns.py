"""Vectorised candle patterns. Every flag at index t uses bars <= t only. Returns int8 arrays (1 = present).
Shapes are neutral about trend (hammer vs hanging man differ only by the trend state added in features)."""
from __future__ import annotations

import numpy as np
import pandas as pd

NAMES = ["doji", "spinning_top", "marubozu_bull", "marubozu_bear", "hammer_shape", "shooting_shape", "pin_bull", "pin_bear", "engulf_bull", "engulf_bear",
         "harami_bull", "harami_bear", "piercing", "dark_cloud", "morning_star", "evening_star", "three_soldiers", "three_crows", "tweezer_top", "tweezer_bottom",
         "inside_bar", "outside_bar", "nr7", "big_bull", "big_bear"]
# dominance: lower index = stronger, used to give each bar ONE dominant pattern id for the conditional table
PRIORITY = ["morning_star", "evening_star", "three_soldiers", "three_crows", "engulf_bull", "engulf_bear", "piercing", "dark_cloud", "harami_bull", "harami_bear", "tweezer_bottom",
            "tweezer_top", "pin_bull", "pin_bear", "hammer_shape", "shooting_shape", "marubozu_bull", "marubozu_bear", "big_bull", "big_bear", "outside_bar", "inside_bar", "nr7", "doji", "spinning_top"]


def atr(h, l, c, n=14) -> np.ndarray:
    pc = np.roll(c, 1); pc[0] = np.nan
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    return pd.Series(tr).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean().values


def _prev(x, k=1):
    y = np.roll(x, k).astype(float); y[:k] = np.nan; return y


def detect(o, h, l, c) -> pd.DataFrame:
    o, h, l, c = (np.asarray(x, float) for x in (o, h, l, c))
    rng = np.where(h - l > 0, h - l, np.nan); body = np.abs(c - o); up = h - np.maximum(o, c); lo = np.minimum(o, c) - l
    a = atr(h, l, c); bull = c > o; bear = c < o
    o1, h1, l1, c1 = (_prev(x) for x in (o, h, l, c)); o2, h2, l2, c2 = (_prev(x, 2) for x in (o, h, l, c))
    body1 = np.abs(c1 - o1); bull1 = c1 > o1; bear1 = c1 < o1; body2 = np.abs(c2 - o2); mid1 = (o1 + c1) / 2
    big_prev = body1 >= 0.6 * np.where(np.isnan(a), np.inf, a)
    f = {}
    f["doji"] = body <= 0.1 * rng
    f["spinning_top"] = (body <= 0.3 * rng) & (up >= 0.25 * rng) & (lo >= 0.25 * rng) & ~f["doji"]
    f["marubozu_bull"] = bull & (body >= 0.9 * rng); f["marubozu_bear"] = bear & (body >= 0.9 * rng)
    f["hammer_shape"] = (lo >= 0.6 * rng) & (np.minimum(o, c) >= l + 0.6 * rng) & (up <= 0.15 * rng)
    f["shooting_shape"] = (up >= 0.6 * rng) & (np.maximum(o, c) <= h - 0.6 * rng) & (lo <= 0.15 * rng)
    f["pin_bull"] = (lo >= (2 / 3) * rng) & (c >= l + 0.5 * rng); f["pin_bear"] = (up >= (2 / 3) * rng) & (c <= h - 0.5 * rng)
    f["engulf_bull"] = bull & bear1 & (c >= o1) & (o <= c1) & (body > body1); f["engulf_bear"] = bear & bull1 & (c <= o1) & (o >= c1) & (body > body1)
    f["harami_bull"] = bull & bear1 & big_prev & (np.maximum(o, c) <= np.maximum(o1, c1)) & (np.minimum(o, c) >= np.minimum(o1, c1)) & (body < body1)
    f["harami_bear"] = bear & bull1 & big_prev & (np.maximum(o, c) <= np.maximum(o1, c1)) & (np.minimum(o, c) >= np.minimum(o1, c1)) & (body < body1)
    f["piercing"] = bull & bear1 & big_prev & (o < c1) & (c > mid1) & (c < o1); f["dark_cloud"] = bear & bull1 & big_prev & (o > c1) & (c < mid1) & (c > o1)
    small1 = body1 <= 0.35 * np.where(np.isnan(a), np.inf, a)
    f["morning_star"] = bull & (c2 < o2) & (body2 >= 0.6 * np.where(np.isnan(a), np.inf, a)) & small1 & (c > (o2 + c2) / 2)
    f["evening_star"] = bear & (c2 > o2) & (body2 >= 0.6 * np.where(np.isnan(a), np.inf, a)) & small1 & (c < (o2 + c2) / 2)
    f["three_soldiers"] = bull & bull1 & (c2 > o2) & (c > c1) & (c1 > c2) & (o > o1) & (o1 > o2); f["three_crows"] = bear & bear1 & (c2 < o2) & (c < c1) & (c1 < c2) & (o < o1) & (o1 < o2)
    tol = 0.05 * np.where(np.isnan(a), np.inf, a)
    f["tweezer_top"] = bear & bull1 & (np.abs(h - h1) <= tol); f["tweezer_bottom"] = bull & bear1 & (np.abs(l - l1) <= tol)
    f["inside_bar"] = (h <= h1) & (l >= l1); f["outside_bar"] = (h > h1) & (l < l1)
    r = pd.Series(h - l); f["nr7"] = (r <= r.rolling(7, min_periods=7).min()).values
    f["big_bull"] = bull & ((h - l) >= 1.5 * a) & (c >= h - 0.25 * (h - l)); f["big_bear"] = bear & ((h - l) >= 1.5 * a) & (c <= l + 0.25 * (h - l))
    out = pd.DataFrame({k: np.nan_to_num(np.asarray(v, float), nan=0.0).astype(np.int8) for k, v in f.items()})[NAMES]
    out.loc[np.isnan(a) | np.isnan(rng), :] = 0                                                                # no ATR yet / zero-range bar: no pattern
    return out


def dominant(flags: pd.DataFrame) -> np.ndarray:
    """One integer id per bar: index in PRIORITY of the strongest present pattern, -1 = none."""
    ids = np.full(len(flags), -1, int)
    for i in range(len(PRIORITY) - 1, -1, -1):
        ids[flags[PRIORITY[i]].values == 1] = i
    return ids
