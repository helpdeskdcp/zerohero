"""
Mode C -- OHLCV proxy DataSource (spec Section 2, Mode C).

WARNING: OHLCV proxy mode -- order-flow values are APPROXIMATIONS, not true
delta. There is no bid/ask depth in historical OHLCV data at all; every
order-flow column here is inferred from price/volume shape, using the exact
formulas below (also documented in config/base.yaml's `proxy:` section --
change the formula in exactly one place if you tune it, not both).

  delta_proxy_i    = volume_i * sign(close_i - open_i)           [0 if close==open]
  cvd_proxy        = cumulative(delta_proxy), reset every session
  absorption_proxy = volume_i >= absorption_vol_mult * rolling_mean(volume, absorption_vol_lookback)
                     AND (high_i - low_i) <= absorption_range_max_atr_mult * ATR(absorption_atr_lookback)
  imbalance_proxy  = |close_i - open_i| / (high_i - low_i) >= imbalance_body_ratio_min
                     AND volume_i >= imbalance_vol_mult * rolling_mean(volume, imbalance_vol_lookback)

Source: market_history.db::market_candles (source=DHAN_HISTORICAL), the
same real backfilled data used throughout this repo's other research --
see shared_harness.candles.load_market_candles. No ticks, no bid/ask, no
synthetic/fabricated bars.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # data/research/ -> shared_harness
import numpy as np
import pandas as pd

from shared_harness import candles

from .source_base import DataSource, pandas_freq

PROXY_WARNING = ("WARNING: OHLCV PROXY MODE -- order-flow values (delta/cvd/absorption/"
                 "imbalance) are APPROXIMATIONS derived from price+volume shape, NOT true "
                 "bid/ask aggressor data. See data/source_ohlcv_proxy.py's module docstring "
                 "for the exact formulas.")


def _resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    r = df.resample(pandas_freq(rule), label="left", closed="left")
    out = pd.concat([r["open"].first(), r["high"].max(), r["low"].min(), r["close"].last(),
                     r["volume"].sum()], axis=1,
                    keys=["open", "high", "low", "close", "volume"])
    return out.dropna(subset=["open", "high", "low", "close"])


def _atr(df: pd.DataFrame, lookback: int) -> pd.Series:
    prior_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prior_close).abs(),
                   (df["low"] - prior_close).abs()], axis=1).max(axis=1)
    return tr.rolling(lookback, min_periods=lookback).mean()


class OhlcvProxyDataSource(DataSource):
    mode = "ohlcv_proxy"
    is_proxy = True

    def __init__(self, db_path: str, proxy_cfg: dict):
        self.db_path = db_path
        self.cfg = proxy_cfg

    def warning_banner(self) -> str | None:
        return PROXY_WARNING

    def load(self, *, symbol: str, kind: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        raw = candles.load_market_candles(self.db_path, symbol, "1m", kind=kind, source="DHAN_HISTORICAL")
        raw = raw.between_time("09:15", "15:29")  # real NSE trading hours only
        raw = raw[raw["close"] > 0]
        raw = raw.loc[start:end]
        df = raw if timeframe == "1m" else _resample_ohlcv(raw, timeframe)
        df = df.between_time("09:15", "15:29")

        cfg = self.cfg
        sign = np.sign(df["close"] - df["open"])
        delta_proxy = df["volume"] * sign
        if cfg.get("cvd_session_reset", True):
            cvd_proxy = delta_proxy.groupby(df.index.date).cumsum()
        else:
            cvd_proxy = delta_proxy.cumsum()

        vol_ma_abs = df["volume"].rolling(cfg["absorption_vol_lookback"],
                                          min_periods=cfg["absorption_vol_lookback"]).mean()
        atr = _atr(df, cfg["absorption_atr_lookback"])
        is_absorption = (df["volume"] >= cfg["absorption_vol_mult"] * vol_ma_abs) & \
                        ((df["high"] - df["low"]) <= cfg["absorption_range_max_atr_mult"] * atr)

        body = (df["close"] - df["open"]).abs()
        rng = (df["high"] - df["low"]).replace(0, np.nan)
        vol_ma_imb = df["volume"].rolling(cfg["imbalance_vol_lookback"],
                                          min_periods=cfg["imbalance_vol_lookback"]).mean()
        is_imbalance = ((body / rng) >= cfg["imbalance_body_ratio_min"]) & \
                       (df["volume"] >= cfg["imbalance_vol_mult"] * vol_ma_imb)

        out = df.copy()
        out["delta"] = delta_proxy
        out["cvd"] = cvd_proxy
        out["is_absorption"] = is_absorption.fillna(False)
        out["is_imbalance"] = is_imbalance.fillna(False)
        self.validate(out)
        return out
