"""One-minute data validation. Nothing is invented: bad rows are dropped and COUNTED, gaps are reported, never filled."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .resampler import NSE, Session


def validate_one_minute(df: pd.DataFrame, session: Session = NSE, *, drop_outside_session: bool = True) -> tuple[pd.DataFrame, dict]:
    rep: dict = {"rows_in": int(len(df))}
    d = df.copy()
    rep["out_of_order_rows"] = int((d.index.to_series().diff().dt.total_seconds() < 0).sum())
    d = d.sort_index(kind="stable")
    dup = d.index.duplicated(keep="first"); rep["duplicate_timestamps_dropped"] = int(dup.sum()); d = d[~dup]
    bad_nan = d[["open", "high", "low", "close"]].isna().any(axis=1); rep["rows_with_missing_ohlc_dropped"] = int(bad_nan.sum()); d = d[~bad_nan]
    bad_px = (d[["open", "high", "low", "close"]] <= 0).any(axis=1); rep["non_positive_price_dropped"] = int(bad_px.sum()); d = d[~bad_px]
    bad_ohlc = (d.high < d[["open", "close", "low"]].max(axis=1)) | (d.low > d[["open", "close", "high"]].min(axis=1)); rep["inconsistent_ohlc_dropped"] = int(bad_ohlc.sum()); d = d[~bad_ohlc]
    loc = d.index.tz_convert("Asia/Kolkata"); mod = loc.hour * 60 + loc.minute
    inside = (mod >= session.open_min) & (mod < session.close_min) & (loc.dayofweek < 5 if session.weekdays_only else True)
    rep["outside_session_rows"] = int((~inside).sum())
    if drop_outside_session: d = d[inside]
    loc = d.index.tz_convert("Asia/Kolkata"); days = pd.Series(loc.date, index=d.index); per_day = d.groupby(days.values).size()
    full = session.n_minutes
    rep["sessions"] = int(len(per_day)); rep["sessions_complete"] = int((per_day == full).sum())
    rep["sessions_partial"] = {str(k): int(v) for k, v in per_day[per_day != full].items()} if len(per_day) <= 400 else {"count": int((per_day != full).sum())}
    rep["missing_minutes_total"] = int(full * len(per_day) - per_day.clip(upper=full).sum())
    if "volume" in d.columns:
        rep["volume_zero_share"] = round(float((d.volume.fillna(0) == 0).mean()), 4) if len(d) else None
        rep["volume_missing_share"] = round(float(d.volume.isna().mean()), 4) if len(d) else None
    rep["rows_out"] = int(len(d)); rep["first"] = str(d.index[0]) if len(d) else None; rep["last"] = str(d.index[-1]) if len(d) else None
    rep["timestamp_semantics"] = "candle OPEN time (Asia/Kolkata)"
    rep["fills"] = "none: missing minutes are left missing"
    return d, rep
