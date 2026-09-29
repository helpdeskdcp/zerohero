"""
Mode A -- real tick-derived DataSource (spec Section 2, Mode A).

Reads app.engines.orderflow_store.TickStore's classified_ticks table (Dhan
WS Full-packet quote-rule/tick-rule aggressor classification, persisted
starting 2026-09-29 -- see that module's docstring). Bars aggregated here
carry GENUINE buy/sell volume split (is_proxy = False): delta = real
buy_vol - real sell_vol, not a price-shape guess.

FAILS LOUDLY (raises DataSource.InsufficientHistory) rather than silently
returning a technically-valid but far-too-small sample -- persistence only
started 2026-09-29, so this mode is not usable for a real backtest for a
long while yet. min_days/min_ticks below are deliberately conservative;
this is meant to stay closed until there is genuinely enough data, not to
be tuned down just to make it "work".
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd

from .source_base import DataSource, pandas_freq

MIN_DAYS = 60          # calendar days of captured history required
MIN_TICKS = 50_000     # classified ticks required, across the requested symbol


class InsufficientHistory(RuntimeError):
    pass


class TickDataSource(DataSource):
    mode = "tick"
    is_proxy = False

    def __init__(self, tick_db_path: str, absorption_cfg: dict | None = None,
                imbalance_ratio_min: float = 3.0):
        self.tick_db_path = tick_db_path
        self.absorption_cfg = absorption_cfg or {}
        self.imbalance_ratio_min = imbalance_ratio_min

    def _load_ticks(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        conn = sqlite3.connect(self.tick_db_path)
        try:
            df = pd.read_sql_query(
                "SELECT received_ts, ltp, ltq, ltt, side FROM classified_ticks "
                "WHERE symbol=? ORDER BY ltt", conn, params=(symbol,))
        finally:
            conn.close()
        if df.empty:
            return df
        df["datetime"] = pd.to_datetime(df["ltt"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
        df = df.set_index("datetime").sort_index()
        return df.loc[start:end]

    def load(self, *, symbol: str, kind: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        ticks = self._load_ticks(symbol, start, end)
        n_days = ticks.index.normalize().nunique() if not ticks.empty else 0
        if len(ticks) < MIN_TICKS or n_days < MIN_DAYS:
            raise InsufficientHistory(
                f"tick mode: only {len(ticks)} classified ticks across {n_days} day(s) captured for "
                f"{symbol} -- need >= {MIN_TICKS} ticks and >= {MIN_DAYS} days before this mode is "
                f"usable for a real backtest. Persistence started 2026-09-29; check back later, or "
                f"use data.mode=ohlcv_proxy in the meantime.")

        freq = pandas_freq(timeframe)
        buy = ticks[ticks["side"] == "BUY_AGGRESSOR"]["ltq"].resample(freq, label="left", closed="left").sum()
        sell = ticks[ticks["side"] == "SELL_AGGRESSOR"]["ltq"].resample(freq, label="left", closed="left").sum()
        r = ticks["ltp"].resample(freq, label="left", closed="left")
        df = pd.concat([r.first(), r.max(), r.min(), r.last()],
                       axis=1, keys=["open", "high", "low", "close"])
        df["buy_vol"] = buy.reindex(df.index).fillna(0)
        df["sell_vol"] = sell.reindex(df.index).fillna(0)
        df["volume"] = df["buy_vol"] + df["sell_vol"]
        df = df.dropna(subset=["open", "high", "low", "close"])
        df = df[df["volume"] > 0]

        delta = df["buy_vol"] - df["sell_vol"]
        df["delta"] = delta
        df["cvd"] = delta.groupby(df.index.date).cumsum()

        vol_mult = self.absorption_cfg.get("absorption_vol_mult", 2.0)
        vol_lookback = self.absorption_cfg.get("absorption_vol_lookback", 20)
        vol_ma = df["volume"].rolling(vol_lookback, min_periods=vol_lookback).mean()
        rng = df["high"] - df["low"]
        rng_ma = rng.rolling(vol_lookback, min_periods=vol_lookback).mean()
        df["is_absorption"] = ((df["volume"] >= vol_mult * vol_ma) & (rng <= rng_ma)).fillna(False)

        ratio = np.where(df["sell_vol"] > 0, df["buy_vol"] / df["sell_vol"].replace(0, np.nan),
                        np.where(df["buy_vol"] > 0, np.inf, 1.0))
        df["is_imbalance"] = (np.maximum(ratio, 1 / np.where(ratio == 0, np.nan, ratio))
                              >= self.imbalance_ratio_min)
        df["is_imbalance"] = df["is_imbalance"].fillna(False)

        self.validate(df)
        return df
