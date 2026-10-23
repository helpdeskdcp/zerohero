"""Normalised internal schema + feature availability report.
Bars: index = tz-aware (Asia/Kolkata) candle OPEN time. Columns open/high/low/close always; volume NaN when the source supplies none (a genuine 0 stays 0).
Optional source fields (never derived from OHLCV): oi, iv, bid, ask, bid_qty, ask_qty, ltp, ltq. Provenance columns: source, source_ts."""
from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED = ["open", "high", "low", "close"]
OPTIONAL = ["volume", "oi", "iv", "bid", "ask", "bid_qty", "ask_qty", "ltp", "ltq"]
TIMESTAMP_SEMANTICS = "candle OPEN time, Asia/Kolkata (a source that stamps the candle CLOSE is shifted by one source interval on load)"
OI_SEMANTICS = "OI is a SNAPSHOT series (open interest at the candle time). It is never summed like volume; a derived bar keeps open/high/low/close snapshots."


def feature_availability(df: pd.DataFrame, stale_after_minutes: float | None = None) -> dict:
    """Per field: SOURCE_PROVIDED | UNAVAILABLE | INVALID (present but entirely NaN/zero-only where zero is not credible) with coverage. Derived features are listed separately."""
    rep = {}
    for c in REQUIRED + OPTIONAL:
        if c not in df.columns:
            rep[c] = {"status": "UNAVAILABLE", "coverage": 0.0}; continue
        s = df[c]; cov = float(s.notna().mean())
        if cov == 0.0: rep[c] = {"status": "UNAVAILABLE", "coverage": 0.0}
        elif c == "volume" and float((s.fillna(0) == 0).mean()) > 0.999: rep[c] = {"status": "INVALID", "coverage": round(cov, 4), "note": "all zero: treated as not supplied (index data)"}
        elif c in ("iv", "bid", "ask") and float((s.fillna(0) <= 0).mean()) > 0.999: rep[c] = {"status": "INVALID", "coverage": round(cov, 4), "note": "no positive values"}
        else: rep[c] = {"status": "SOURCE_PROVIDED", "coverage": round(cov, 4)}
    rep["_derived"] = {"timeframes": "PROPERLY_DERIVED from 1-minute OHLCV (session-aware resampler)", "oi_aggregation": OI_SEMANTICS,
                       "iv": "NOT derived from OHLCV: needs provider IV or a separately calibrated pricing model", "bid_ask": "NOT reconstructed from OHLCV; LTP/close is never labelled bid or ask"}
    rep["_enabled"] = {"ohlcv_strategy": all(rep[c]["status"] == "SOURCE_PROVIDED" for c in REQUIRED),
                       "volume_filter": rep["volume"]["status"] == "SOURCE_PROVIDED", "oi_features": rep["oi"]["status"] == "SOURCE_PROVIDED",
                       "iv_features": rep["iv"]["status"] == "SOURCE_PROVIDED", "bid_ask_features": rep["bid"]["status"] == "SOURCE_PROVIDED" and rep["ask"]["status"] == "SOURCE_PROVIDED"}
    return rep
