"""Feature frame for the next-candle model. Row t uses bars <= t only; the target (row t -> bar t+1) is built separately in `target`."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import levels as L
from . import patterns as P
from .volume import volume_features

FAMS = ["PD", "PW", "PM", "FD", "FW", "FM"]
DIST_BINS = [0.25, 0.75, 1.5]                      # ATR units: touch / near / mid / far
DIST_LAB = ["touch", "near", "mid", "far"]


def ema(x, n): return pd.Series(x).ewm(span=n, adjust=False).mean().values


def build(df: pd.DataFrame) -> pd.DataFrame:
    """df: t (tz-aware UTC), o, h, l, c, v sorted ascending, ONE instrument. Returns feature frame indexed like df with 'date', 'tod' and every feature."""
    o, h, l, c = (df[k].values.astype(float) for k in ("o", "h", "l", "c"))
    a = P.atr(h, l, c); pat = P.detect(o, h, l, c); dom = P.dominant(pat)
    ist = df["t"].dt.tz_convert("Asia/Kolkata"); date = L.session_date(df["t"])
    tod = (ist.dt.hour * 60 + ist.dt.minute - (9 * 60 + 15)).values
    lev = L.previous_period_levels(df); names = L.level_names(lev.columns)
    rng = np.where(h - l > 0, h - l, np.nan)
    f = pd.DataFrame(index=df.index)
    f["date"] = date.values; f["tod"] = tod; f["dow"] = ist.dt.dayofweek.values
    f["atr"] = a; f["range_atr"] = (h - l) / a; f["body_atr"] = (c - o) / a
    f["upper_wick"] = (h - np.maximum(o, c)) / rng; f["lower_wick"] = (np.minimum(o, c) - l) / rng; f["close_loc"] = (c - l) / rng
    for k in (1, 2, 3, 5):
        f[f"ret{k}_atr"] = (c - np.roll(c, k)) / a; f.loc[f.index[:k], f"ret{k}_atr"] = np.nan
    e20, e50 = ema(c, 20), ema(c, 50)
    slope = (e20 - np.roll(e20, 5)) / a; slope[:5] = np.nan
    f["ema_slope"] = slope; f["dist_ema50"] = (c - e50) / a
    trend = np.where((slope > 0.5) & (c > e50), 1, np.where((slope < -0.5) & (c < e50), -1, 0)); trend[np.isnan(slope)] = 0
    f["trend"] = trend
    pc = lev["PDC"].values; f["gap_atr"] = (o - pc) / a                                                      # open vs previous day close, only meaningful on the first bar of the day
    f.loc[tod != 0, "gap_atr"] = 0.0
    f["pivot_pos"] = (c - lev["FD_P"].values) / a
    for nme in P.NAMES: f[f"p_{nme}"] = pat[nme].values
    f["dom"] = dom
    # --- level context
    lv = lev[names].values.astype(float); dist = (lv - c[:, None]) / a[:, None]                               # >0 level above close, <0 below
    touched = (l[:, None] <= lv) & (lv <= h[:, None]); rej_top = touched & (c[:, None] < lv) & (h[:, None] >= lv); rej_bot = touched & (c[:, None] > lv) & (l[:, None] <= lv)
    absd = np.where(np.isnan(dist), np.inf, np.abs(dist)); j = np.argmin(absd, axis=1); has = np.isfinite(absd[np.arange(len(c)), j])
    nd = dist[np.arange(len(c)), j]; f["near_dist"] = np.where(has, nd, np.nan)
    fam_idx = np.array([L.family_of(n) for n in names])
    f["near_fam"] = np.where(has, fam_idx[j], "NONE")
    f["near_side"] = np.where(has, np.where(nd > 0, 1, -1), 0)                                                # +1 nearest level is above price (resistance), -1 below (support)
    f["near_zone"] = np.where(has, pd.cut(np.abs(nd), [-1] + DIST_BINS + [np.inf], labels=DIST_LAB).astype(object), "none")
    f["near_name"] = np.where(has, np.array(names)[j], "NONE")
    ab = np.where(dist > 0, dist, np.inf).min(axis=1); be = np.where(dist < 0, -dist, np.inf).min(axis=1)
    f["dist_above"] = np.where(np.isfinite(ab), ab, np.nan); f["dist_below"] = np.where(np.isfinite(be), be, np.nan)
    f["n_levels_touched"] = np.nansum(touched, axis=1)
    f["rej_resistance"] = np.nansum(rej_top, axis=1) > 0; f["rej_support"] = np.nansum(rej_bot, axis=1) > 0
    f["rej_resistance"] = f["rej_resistance"].astype(int); f["rej_support"] = f["rej_support"].astype(int)
    for fam in FAMS:
        idx = [i for i, n in enumerate(names) if L.family_of(n) == fam]
        sub = absd[:, idx]; f[f"d_{fam}"] = np.where(np.isfinite(sub.min(axis=1)), sub.min(axis=1), np.nan)
    # --- volume candles (NaN on index data)
    vf = volume_features(df["t"], o, h, l, c, df["v"].values, a)
    for k in vf.columns: f[k] = vf[k].values
    return f


def target(df: pd.DataFrame) -> pd.DataFrame:
    """Row t -> next candle: ret_bps = (c[t+1]/c[t]-1)*1e4, y = 1 if up. Last bar of each session has no valid next candle in the same session -> NaN (never crosses the overnight gap)."""
    c = df["c"].values.astype(float); date = L.session_date(df["t"]).values
    nxt = np.roll(c, -1); same = np.roll(date, -1) == date; same[-1] = False
    r = np.where(same, (nxt / c - 1.0) * 1e4, np.nan)
    y = np.where(np.isnan(r) | (r == 0), np.nan, (r > 0).astype(float))
    return pd.DataFrame({"ret_bps": r, "y": y}, index=df.index)


NUMERIC = None


def model_columns(f: pd.DataFrame, with_volume: bool) -> list[str]:
    drop = {"date", "near_fam", "near_zone", "near_name", "atr"}
    cols = [c for c in f.columns if c not in drop]
    if not with_volume: cols = [c for c in cols if c not in ("relvol", "clv_x_vol", "effort_result", "climax", "dry_up", "vol_trend")]
    return cols
