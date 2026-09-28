"""No-look-ahead structural levels. Consolidates 3 independently-written
copies of the resample+shift(1)+merge_asof pattern (reversal_sweep,
false_breakout_reversal, cpr_orb) for a prior COMPLETED period's high/low,
plus opening-range high/low."""
from __future__ import annotations

import numpy as np
import pandas as pd


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """label='left'/closed='left' EXPLICITLY -- pandas' own defaults
    disagree between rules (e.g. 'W' defaults to label='right'), which
    would silently break the uniform shift(1)-for-"prior completed period"
    logic in prior_period_value() below if left on defaults."""
    r = df.resample(rule, label="left", closed="left")
    out = pd.concat([r["open"].first(), r["high"].max(), r["low"].min(), r["close"].last()],
                    axis=1, keys=["open", "high", "low", "close"]).dropna()
    return out


def prior_period_value(target_index: pd.DatetimeIndex, period_df: pd.DataFrame, col: str) -> np.ndarray:
    """For each timestamp in `target_index`, the PRIOR COMPLETED period's
    value of `col` -- never the period that timestamp itself falls inside
    (that would be look-ahead). `period_df` must come from resample_ohlc()
    above (label='left') so shifting by one row is a valid "prior period"
    operation regardless of the resample rule used to build it."""
    shifted = period_df[[col]].shift(1)
    s = shifted.reset_index()
    merged = pd.merge_asof(pd.DataFrame({"datetime": target_index}), s, on="datetime", direction="backward")
    return merged[col].values


def opening_range(df: pd.DataFrame, start: str = "09:15", end: str = "09:30") -> pd.DataFrame:
    """Per-session opening-range high/low, visible only from `end` onward
    the SAME session (NaN before the range itself has closed each day)."""
    dates = df.index.date
    start_t, end_t = pd.Timestamp(start).time(), pd.Timestamp(end).time()
    or_high, or_low = {}, {}
    for date in pd.unique(dates):
        day_df = df[dates == date]
        rng = day_df[(day_df.index.time >= start_t) & (day_df.index.time < end_t)]
        if not rng.empty:
            or_high[date] = rng["high"].max()
            or_low[date] = rng["low"].min()
    date_series = pd.Series(dates, index=df.index)
    out = pd.DataFrame(index=df.index)
    out["or_high"] = date_series.map(or_high)
    out["or_low"] = date_series.map(or_low)
    before_end = pd.Series([t < end_t for t in df.index.time], index=df.index)
    out.loc[before_end, ["or_high", "or_low"]] = np.nan
    return out


def daily_pivot_cpr(daily: pd.DataFrame) -> pd.DataFrame:
    """Classic CPR (Pivot/BC/TC) from a daily OHLC frame's OWN high/low/
    close -- caller is responsible for passing PRIOR-day values (e.g. via
    prior_period_value on each of these 3 columns) to stay non-look-ahead;
    this function only does the arithmetic."""
    out = daily.copy()
    out["pivot"] = (out["high"] + out["low"] + out["close"]) / 3.0
    out["bc"] = (out["high"] + out["low"]) / 2.0
    out["tc"] = 2 * out["pivot"] - out["bc"]
    lo = out[["bc", "tc"]].min(axis=1)
    hi = out[["bc", "tc"]].max(axis=1)
    out["bc"], out["tc"] = lo, hi
    return out
