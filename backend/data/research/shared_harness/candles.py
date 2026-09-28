"""Real-data candle loaders. Consolidates 3 independently-written copies of
the same CSV format-sniffing logic (reversal_sweep, false_breakout_reversal,
cpr_orb all had this verbatim). No synthetic/fabricated bars -- every loader
here reads real captured or real historical data only."""
from __future__ import annotations

import sqlite3

import pandas as pd


def load_ohlc_csv(csv_path: str) -> pd.DataFrame:
    """Handles both real formats seen in data/historical/kaggle/: a single
    'datetime' column (e.g. NIFTY 2015-2025), or separate 'Date'+'Time'
    columns with capitalized OHLC and no volume (e.g. BANKNIFTY)."""
    df = pd.read_csv(csv_path)
    cols = {c.lower(): c for c in df.columns}
    if "datetime" in cols:
        df["datetime"] = pd.to_datetime(df[cols["datetime"]])
    elif "date" in cols and "time" in cols:
        df["datetime"] = pd.to_datetime(df[cols["date"]] + " " + df[cols["time"]],
                                        format="%d-%m-%Y %H:%M:%S")
    else:
        raise ValueError(f"{csv_path}: no recognizable datetime column(s)")
    df = df.rename(columns={cols.get("open", "Open"): "open", cols.get("high", "High"): "high",
                            cols.get("low", "Low"): "low", cols.get("close", "Close"): "close"})
    df = df.set_index("datetime").sort_index()
    return df[~df.index.duplicated(keep="first")][["open", "high", "low", "close"]]


def load_captured_index_ticks(db_path: str, symbol: str, resample_rule: str = "1min") -> pd.DataFrame:
    """Real broker-captured index ticks (market_history.db::quote_snapshots),
    resampled to OHLC bars. Only as much history as this system has actually
    captured -- a hard, disclosed sample-size ceiling (a few weeks), never
    padded with synthetic bars to look bigger."""
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT received_ts, ltp FROM quote_snapshots WHERE symbol=? AND kind='INDEX' "
            "AND ltp IS NOT NULL ORDER BY received_ts", conn, params=(symbol,))
    finally:
        conn.close()
    df["datetime"] = pd.to_datetime(df["received_ts"], utc=True).dt.tz_convert("Asia/Kolkata")
    df = df.set_index("datetime")[["ltp"]]
    r = df["ltp"].resample(resample_rule, label="left", closed="left")
    return pd.concat([r.first(), r.max(), r.min(), r.last()],
                     axis=1, keys=["open", "high", "low", "close"]).dropna()


def load_captured_option_ticks(db_path: str, symbol: str) -> pd.DataFrame:
    """Real broker-captured option premium ticks for `symbol`, all
    expiries/strikes/types, sorted chronologically. Callers filter by
    expiry/strike/option_type themselves (see options.py)."""
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT received_ts, expiry, strike, option_type, ltp FROM quote_snapshots "
            "WHERE symbol=? AND kind='OPTION' AND ltp IS NOT NULL", conn, params=(symbol,))
    finally:
        conn.close()
    df["ts"] = pd.to_datetime(df["received_ts"], utc=True).dt.tz_convert("Asia/Kolkata")
    return df.sort_values("ts")
