"""Option-market flow features for the NIFTY index (rule: data/research/option_flow/SPEC_2026-10-24.md). All features at 5-minute bar t use data <= t only."""
from __future__ import annotations

import numpy as np
import pandas as pd

LOOK = 15            # minutes
NEAR = 250           # points around ATM
STEP = 50
TARGET_MIN = 30


def _series(df: pd.DataFrame, field: str) -> dict:
    """(otype, strike) -> minute-indexed Series (forward-filled within the pulled day range)."""
    out = {}
    for (ot, k), g in df.groupby(["otype", "strike"]):
        s = g.set_index("ts")[field].sort_index(); out[(ot, float(k))] = s[~s.index.duplicated(keep="last")]
    return out


def flow_features(bars: pd.DataFrame, spot: pd.Series, t_list: list[int], step: int = STEP, near_pts: int = NEAR) -> pd.DataFrame:
    """bars: columns ts (epoch s, minute), strike, otype ('CE'/'PE'), c, v, oi for ONE expiry. spot: index close by minute epoch. t_list: epoch seconds of the minute that closes each 5m bar.
    Returns one row per t with F1..F5 (NaN when the needed contracts/minutes are missing)."""
    C, V, OI = _series(bars, "c"), _series(bars, "v"), _series(bars, "oi"); rows = []
    for t in t_list:
        s_t = spot.get(t); s_p = spot.get(t - LOOK * 60)
        if s_t is None or s_p is None or not np.isfinite(s_t) or not np.isfinite(s_p): rows.append((t,) + (np.nan,) * 5); continue
        K = round(s_t / step) * step; Kp = round(s_p / step) * step
        near = [K + step * j for j in range(-near_pts // step, near_pts // step + 1)]
        def win_sum(side, d, lo, hi):
            tot = 0.0; ok = False
            for k in near:
                sr = d.get((side, float(k)))
                if sr is None: continue
                w = sr[(sr.index > lo) & (sr.index <= hi)]
                if len(w): tot += float(w.sum()); ok = True
            return tot if ok else np.nan
        pv, cv = win_sum("PE", V, t - LOOK * 60, t), win_sum("CE", V, t - LOOK * 60, t)
        f1 = (pv - cv) / (pv + cv) if np.isfinite(pv) and np.isfinite(cv) and (pv + cv) > 0 else np.nan
        def oi_at(side, tt):
            tot = 0.0; ok = False
            for k in near:
                sr = OI.get((side, float(k)))
                if sr is None: continue
                x = sr[sr.index <= tt]
                if len(x) and np.isfinite(x.iloc[-1]): tot += float(x.iloc[-1]); ok = True
            return tot if ok else np.nan
        dp, dc = oi_at("PE", t) - oi_at("PE", t - LOOK * 60), oi_at("CE", t) - oi_at("CE", t - LOOK * 60)
        f2 = (dp - dc) / (abs(dp) + abs(dc)) if np.isfinite(dp) and np.isfinite(dc) and (abs(dp) + abs(dc)) > 0 else np.nan
        def px(side, k, tt):
            sr = C.get((side, float(k)))
            if sr is None: return np.nan
            x = sr[sr.index <= tt]
            return float(x.iloc[-1]) if len(x) and tt - x.index[-1] <= 300 else np.nan
        c_now, p_now, c_old, p_old = px("CE", K, t), px("PE", K, t), px("CE", K, t - LOOK * 60), px("PE", K, t - LOOK * 60)
        d_now = (K + c_now - p_now) - s_t; d_old = (K + px("CE", Kp, t - LOOK * 60) - px("PE", Kp, t - LOOK * 60)) - s_p
        f3 = (d_now - d_old) / s_t * 1e4
        st_now, st_old = c_now + p_now, c_old + p_old; f4 = st_now / st_old - 1 if np.isfinite(st_now) and np.isfinite(st_old) and st_old > 0 else np.nan
        def skew(tt):
            st = px("CE", K, tt) + px("PE", K, tt); return (px("PE", K - 2 * step, tt) - px("CE", K + 2 * step, tt)) / st if st and np.isfinite(st) and st > 0 else np.nan
        rows.append((t, f1, f2, f3, f4, skew(t) - skew(t - LOOK * 60)))
    return pd.DataFrame(rows, columns=["t", "F1", "F2", "F3", "F4", "F5"])
