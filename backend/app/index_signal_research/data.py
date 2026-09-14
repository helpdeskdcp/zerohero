"""
Real-data loaders -- Phase 1/2 audit findings baked in as documented,
non-optional fixes (not silent workarounds):

DATASET INVENTORY (from this mission's own Phase 1 audit,
`data/historical/kaggle/research_historical.db`, table `normalized_bars`):
  - NIFTY   INDEX 5m : 2015-01-09 .. 2026-05-18, 209,888 real rows, clean
                       (no duplicate timestamps).
  - BANKNIFTY INDEX 5m : same date range, but EVERY row is stored TWICE
                       (exact duplicate timestamp+OHLC, confirmed by direct
                       query -- an ingest artifact, not real 2.5-minute
                       data). Deduplicated here via `DISTINCT`, not silently
                       used as-is.
  - NIFTY FUTURE 1d, GOLD/SENSEX/SILVER 1d : daily only -- not usable for
                       any intraday hypothesis in this mission; noted, not
                       loaded.
  - Volume: 100% NULL/0 for both NIFTY and BANKNIFTY 5m, confirmed by
                       direct query -- ANY volume-derived feature (real
                       VWAP, volume expansion, volume percentile) is
                       UNAVAILABLE on this dataset, not silently
                       approximated. See features.py.

market_history.db (real broker captures, ~2026-09-02..2026-09-11): kept
for option-chain / pipeline-validation use only (Phase 15/16), never for
long-term index-hypothesis statistics -- 10 real days is not enough sample
for any of Phase 6/7/8's chronological splits.
"""
from __future__ import annotations

import os
import sqlite3

import pandas as pd

KAGGLE_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "historical", "kaggle", "research_historical.db")


def load_index_5m(symbol: str, *, limit_years: float | None = None, db_path: str | None = None) -> pd.DataFrame:
    """Real Kaggle 5m OHLC for `symbol` in {"NIFTY", "BANKNIFTY"}. Always
    de-duplicates on timestamp (see module docstring -- BANKNIFTY's table
    has every row stored twice; DISTINCT is harmless and a no-op for NIFTY,
    which has no duplicates). Returns a DataFrame sorted chronologically,
    columns: t (tz-aware UTC), o, h, l, c, v (always 0.0 -- real data, not
    fabricated, see module docstring)."""
    path = db_path or KAGGLE_DB
    conn = sqlite3.connect(path)
    where = "symbol=? AND instrument_type='INDEX' AND timeframe='5m'"
    params = [symbol]
    if limit_years is not None:
        max_ts = conn.execute(f"SELECT max(timestamp) FROM normalized_bars WHERE {where}", params).fetchone()[0]
        cutoff_year = int(max_ts[:4]) - int(limit_years)
        where += " AND timestamp >= ?"
        params.append(f"{cutoff_year}-01-01")
    rows = conn.execute(
        f"SELECT DISTINCT timestamp, open, high, low, close, volume FROM normalized_bars "
        f"WHERE {where} ORDER BY timestamp", params).fetchall()
    conn.close()
    df = pd.DataFrame(rows, columns=["t", "o", "h", "l", "c", "v"])
    df["v"] = df["v"].fillna(0.0)
    df["t"] = pd.to_datetime(df["t"], utc=True)
    df = df.reset_index(drop=True)
    return df


def to_ist(ts: pd.Series) -> pd.Series:
    """UTC -> IST wall-clock (NSE trades 09:15-15:30 IST = 03:45-10:00 UTC),
    same conversion convention as `app.liquidity_sweep.resample`."""
    return ts.dt.tz_convert("Asia/Kolkata")
