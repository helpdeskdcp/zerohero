"""
Causal, vectorized technical features -- every column at row i is computed
ONLY from bars <= i (pandas `.rolling()`/`.ewm()` end at the current row by
construction; any comparison to "N bars ago" uses `.shift(N)`, never a
negative/future shift). HTF bias additionally uses `pd.merge_asof(...,
direction="backward")`, which by construction only matches a higher-
timeframe bar whose own timestamp is <= the 5m bar's timestamp -- no
look-ahead.

Volume-derived features (real VWAP, volume expansion/percentile) are
DELIBERATELY NOT computed here: `data.py`'s own audit confirms Kaggle
NIFTY/BANKNIFTY 5m volume is 100% NULL/0, so any such feature would be a
column of zeros/NaNs -- explicitly UNAVAILABLE, not a fabricated proxy.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..liquidity_sweep import resample as resample_mod

EMA_FAST, EMA_SLOW, EMA_TREND = 10, 30, 20
RSI_PERIOD = 14
ATR_PERIOD = 14
STRUCT_BLOCK = 10
BREAKOUT_LOOKBACK = 20
VOL_BASELINE_WINDOW = 20
COMPRESSION_SHORT, COMPRESSION_LONG = 10, 50


def _wilder_atr(h: pd.Series, l: pd.Series, c: pd.Series, period: int) -> pd.Series:
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def _wilder_rsi(c: pd.Series, period: int) -> pd.Series:
    delta = c.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.where(avg_loss != 0, 100.0)


def _consecutive_direction(sign: pd.Series) -> pd.Series:
    """Count of consecutive same-sign bars ending at each row (positive for
    up-streaks, negative for down-streaks, 0 for a flat/zero-return bar)."""
    grp = (sign != sign.shift(1)).cumsum()
    counts = sign.groupby(grp).cumcount() + 1
    return counts * sign


def add_price_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    o, h, l, c = df["o"], df["h"], df["l"], df["c"]

    df["ret_1"] = c.pct_change()
    df["body_ratio"] = ((c - o) / (h - l).replace(0, np.nan)).fillna(0.0)
    df["range_pct"] = (h - l) / c

    df["ema_fast"] = c.ewm(span=EMA_FAST, min_periods=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = c.ewm(span=EMA_SLOW, min_periods=EMA_SLOW, adjust=False).mean()
    df["ema20"] = c.ewm(span=EMA_TREND, min_periods=EMA_TREND, adjust=False).mean()
    df["ema_sep"] = (df["ema_fast"] - df["ema_slow"]) / c
    df["ema_fast_slope"] = df["ema_fast"].diff(5) / c
    df["dist_from_ema20_atr"] = np.nan   # filled after ATR below

    df["rsi14"] = _wilder_rsi(c, RSI_PERIOD)
    df["roc_10"] = c.pct_change(10)

    df["atr14"] = _wilder_atr(h, l, c, ATR_PERIOD)
    df["atr_norm"] = df["atr14"] / c
    df["dist_from_ema20_atr"] = (c - df["ema20"]) / df["atr14"].replace(0, np.nan)

    df["vol_baseline"] = df["atr14"].rolling(VOL_BASELINE_WINDOW, min_periods=VOL_BASELINE_WINDOW).mean()
    df["vol_expansion_ratio"] = df["atr14"] / df["vol_baseline"].replace(0, np.nan)

    df["range_short"] = (h - l).rolling(COMPRESSION_SHORT, min_periods=COMPRESSION_SHORT).mean()
    df["range_long"] = (h - l).rolling(COMPRESSION_LONG, min_periods=COMPRESSION_LONG).mean()
    df["range_compression"] = df["range_short"] / df["range_long"].replace(0, np.nan)

    roll_high_20 = h.rolling(BREAKOUT_LOOKBACK, min_periods=BREAKOUT_LOOKBACK).max()
    roll_low_20 = l.rolling(BREAKOUT_LOOKBACK, min_periods=BREAKOUT_LOOKBACK).min()
    df["prior_high_20"] = roll_high_20.shift(1)     # excludes current bar -- breakout measured vs PRIOR 20
    df["prior_low_20"] = roll_low_20.shift(1)
    df["breakout_up_atr"] = (c - df["prior_high_20"]) / df["atr14"].replace(0, np.nan)
    df["breakout_down_atr"] = (df["prior_low_20"] - c) / df["atr14"].replace(0, np.nan)

    block_high = h.rolling(STRUCT_BLOCK, min_periods=STRUCT_BLOCK).max()
    block_low = l.rolling(STRUCT_BLOCK, min_periods=STRUCT_BLOCK).min()
    df["struct_uptrend"] = (block_high > block_high.shift(STRUCT_BLOCK)) & (block_low > block_low.shift(STRUCT_BLOCK))
    df["struct_downtrend"] = (block_high < block_high.shift(STRUCT_BLOCK)) & (block_low < block_low.shift(STRUCT_BLOCK))

    sign = np.sign(df["ret_1"]).fillna(0)
    df["consecutive_dir"] = _consecutive_direction(sign)

    df["expansion_move_atr"] = (c - c.shift(6)) / df["atr14"].replace(0, np.nan)

    return df


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    ist = df["t"].dt.tz_convert("Asia/Kolkata")
    df["session_date"] = ist.dt.strftime("%Y-%m-%d")
    df["minutes_since_open"] = (ist.dt.hour * 60 + ist.dt.minute) - (9 * 60 + 15)
    df["time_bucket"] = np.select(
        [df["minutes_since_open"] < 45, df["minutes_since_open"] >= (14 * 60 + 30) - (9 * 60 + 15)],
        ["OPENING", "CLOSING"], default="MID")
    df["day_of_week"] = ist.dt.day_name()
    df["year"] = ist.dt.year
    return df


def add_htf_bias(df: pd.DataFrame) -> pd.DataFrame:
    """Real HTF trend bias, backward-joined so every 5m bar only ever sees
    higher-timeframe bars that had already CLOSED at or before it (merge_asof
    direction="backward" -- see module docstring). Reuses
    `app.liquidity_sweep.resample` (already tested for exact aggregation),
    run ONCE globally here (not per-bar/per-signal -- there is no expensive
    per-step re-resampling in this vectorized design, unlike the event-
    triggered liquidity_sweep walk)."""
    df = df.copy()
    bars = df[["t", "o", "h", "l", "c", "v"]].rename(columns={}).to_dict("records")
    for b in bars:
        b["t"] = b["t"].isoformat()

    htf_frames = {}
    for tf, minutes in (("15m", 15), ("30m", 30), ("1h", 60), ("4h", 240)):
        coarse = resample_mod.resample_bars(bars, minutes)
        htf_frames[tf] = _htf_ema_bias_frame(coarse)
    htf_frames["1d"] = _htf_ema_bias_frame(resample_mod.resample_daily(bars))

    weights = {"15m": 0.10, "30m": 0.15, "1h": 0.20, "4h": 0.25, "1d": 0.30}
    signed = pd.Series(0.0, index=df.index)
    wsum = pd.Series(0.0, index=df.index)
    for tf, frame in htf_frames.items():
        if frame.empty:
            continue
        joined = pd.merge_asof(df[["t"]].sort_values("t"), frame.sort_values("t"),
                               on="t", direction="backward")
        bias = joined["bias"].to_numpy()
        has_val = ~np.isnan(bias)
        signed += pd.Series(np.where(has_val, bias, 0.0), index=df.index) * weights[tf]
        wsum += pd.Series(np.where(has_val, weights[tf], 0.0), index=df.index)
        df[f"htf_bias_{tf}"] = bias

    htf_score = np.where(wsum > 0, signed / wsum.replace(0, np.nan), 0.0)
    df["htf_score"] = htf_score
    df["htf_regime"] = np.select(
        [df["htf_score"] > 0.15, df["htf_score"] < -0.15, df["htf_score"].abs() < 0.05],
        ["BULLISH", "BEARISH", "RANGE"], default="TRANSITION")
    return df


def _htf_ema_bias_frame(coarse_bars: list) -> pd.DataFrame:
    if len(coarse_bars) < EMA_TREND + 1:
        return pd.DataFrame(columns=["t", "bias"])
    closes = pd.Series([b["c"] for b in coarse_bars])
    ts = pd.to_datetime([b["t"] for b in coarse_bars], utc=True)
    ema = closes.ewm(span=EMA_TREND, min_periods=EMA_TREND, adjust=False).mean()
    bias = ((closes - ema) / ema.replace(0, np.nan) * 20).clip(-1.0, 1.0)
    return pd.DataFrame({"t": ts, "bias": bias.to_numpy()})


def build_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = add_price_features(df)
    df = add_time_features(df)
    df = add_htf_bias(df)
    return df
