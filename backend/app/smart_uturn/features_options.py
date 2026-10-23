"""Optional options-research layer. NOTHING here is used by the base strategy and no default filter is added. A feature raises Unavailable unless the REAL source field is present and fresh;
it never substitutes LTP for bid/ask, derives IV from OHLCV, or treats a missing field as zero."""
from __future__ import annotations

import numpy as np
import pandas as pd


class Unavailable(RuntimeError):
    pass


def _need(df, cols):
    for c in cols:
        if c not in df.columns or df[c].notna().sum() == 0: raise Unavailable(f"field {c!r} is not supplied by the source")


def _one_contract(df):
    for c in ("expiry", "strike", "option_type"):
        if c in df.columns and df[c].nunique(dropna=True) > 1: raise Unavailable(f"mixed {c}: one contract per frame (expiries/strikes are never mixed)")


def oi_change(df: pd.DataFrame, periods: int = 1) -> pd.Series:
    """Change of the OI SNAPSHOT series (not volume-like, never summed). First `periods` values are NaN."""
    _need(df, ["oi"]); _one_contract(df); return df["oi"].diff(periods)


def oi_buildup(df: pd.DataFrame, periods: int = 1) -> pd.Series:
    """LONG_BUILDUP / SHORT_BUILDUP / SHORT_COVERING / LONG_UNWINDING from price change vs OI change (classic quadrant), NaN when either is missing."""
    _need(df, ["oi", "close"]); _one_contract(df); dp, do = df["close"].diff(periods), df["oi"].diff(periods)
    out = pd.Series(np.select([(dp > 0) & (do > 0), (dp < 0) & (do > 0), (dp > 0) & (do < 0), (dp < 0) & (do < 0)], ["LONG_BUILDUP", "SHORT_BUILDUP", "SHORT_COVERING", "LONG_UNWINDING"], default=""), index=df.index)
    return out.where(dp.notna() & do.notna() & (out != ""))


def spread(df: pd.DataFrame) -> pd.Series:
    _need(df, ["bid", "ask"]); s = df["ask"] - df["bid"]; return s.where((df["bid"] > 0) & (df["ask"] > 0) & (s >= 0))


def bid_ask_imbalance(df: pd.DataFrame) -> pd.Series:
    _need(df, ["bid_qty", "ask_qty"]); t = df["bid_qty"] + df["ask_qty"]; return ((df["bid_qty"] - df["ask_qty"]) / t).where(t > 0)


def iv_change(df: pd.DataFrame, periods: int = 1) -> pd.Series:
    _need(df, ["iv"]); _one_contract(df); return df["iv"].diff(periods)


def stale_quote_mask(df: pd.DataFrame, max_age_minutes: float) -> pd.Series:
    """True where the bid/ask snapshot is older than max_age_minutes (needs `source_ts`)."""
    _need(df, ["source_ts"]); age = (df.index.to_series() - pd.to_datetime(df["source_ts"], utc=True).dt.tz_convert(df.index.tz)).dt.total_seconds() / 60.0
    return age > max_age_minutes
