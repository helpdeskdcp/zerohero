"""Volume-candle features (causal). All NaN when the instrument has no volume (index data)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def volume_features(t: pd.Series, o, h, l, c, v, atr) -> pd.DataFrame:
    n = len(c); nan = np.full(n, np.nan)
    v = np.asarray(v, float)
    if not np.nansum(v > 0):
        return pd.DataFrame({k: nan for k in ("relvol", "clv_x_vol", "effort_result", "climax", "dry_up", "vol_trend")})
    ist = t.dt.tz_convert("Asia/Kolkata"); tod = (ist.dt.hour * 60 + ist.dt.minute).values
    s = pd.Series(np.where(v > 0, v, np.nan)); base = s.groupby(tod).transform(lambda x: x.shift(1).rolling(20, min_periods=5).median())    # same time-of-day, PREVIOUS sessions only
    relvol = (s / base).values
    rng = np.where(np.asarray(h) - np.asarray(l) > 0, np.asarray(h) - np.asarray(l), np.nan)
    clv = ((np.asarray(c) - np.asarray(l)) - (np.asarray(h) - np.asarray(c))) / rng                          # -1 closes on the low, +1 on the high
    move = rng / np.where(np.asarray(atr) > 0, atr, np.nan)
    return pd.DataFrame({"relvol": relvol, "clv_x_vol": clv * relvol, "effort_result": relvol / np.where(move > 0, move, np.nan),
                         "climax": ((relvol >= 2.5) & (move >= 1.5)).astype(float) * np.where(np.isnan(relvol), np.nan, 1.0), "dry_up": (relvol <= 0.5).astype(float) * np.where(np.isnan(relvol), np.nan, 1.0),
                         "vol_trend": (pd.Series(relvol).rolling(5, min_periods=3).mean()).values})
