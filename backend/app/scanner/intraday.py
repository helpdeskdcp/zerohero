"""Owner's 5-minute BULLISH / BEARISH 'balanced' scans (rule: data/research/scanner/SPEC_5M_BULLISH_2026-10-24.md). Pure functions on a 5-minute OHLCV frame (t UTC tz-aware, o, h, l, c, v);
the value at bar t uses bars <= t only. Chartink conventions: Wilder RSI(14), EMA(20), SMA(volume, 20) includes the latest bar, '1 candle ago' = the previous 5-minute bar, series continuous across sessions."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

import numpy as np
import pandas as pd

VOL_MULT = 1.5
HERE = os.path.dirname(os.path.abspath(__file__)); MH = os.path.abspath(os.path.join(HERE, "..", "..", "data", "market_history.db"))
INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX", "NATURALGAS", "CRUDEOIL", "GOLDM"]
VARIANTS = ("FULL_BULL", "A_BULL", "B_BULL", "FULL_BEAR", "A_BEAR", "B_BEAR")


def rsi(x: pd.Series, n: int = 14) -> pd.Series:
    d = x.diff(); up = d.clip(lower=0).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean(); dn = (-d.clip(upper=0)).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def scan_flags(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c, v = (df[k].astype(float).reset_index(drop=True) for k in ("o", "h", "l", "c", "v"))
    r = rsi(c, 14); e20 = c.ewm(span=20, adjust=False, min_periods=20).mean(); vol_ok = v > v.rolling(20, min_periods=20).mean() * VOL_MULT
    ph, pl = h.shift(1), l.shift(1)
    bull_core = (c > o) & vol_ok & (r > 50) & (c > e20); bull_a = (l < pl) & (c > pl) & (c > o); bull_b = (c > ph) & vol_ok & (r > 55)
    bear_core = (c < o) & vol_ok & (r < 50) & (c < e20); bear_a = (h > ph) & (c < ph) & (c < o); bear_b = (c < pl) & vol_ok & (r < 45)
    out = pd.DataFrame({"A_BULL": bull_core & bull_a, "B_BULL": bull_core & bull_b, "FULL_BULL": bull_core & (bull_a | bull_b),
                        "A_BEAR": bear_core & bear_a, "B_BEAR": bear_core & bear_b, "FULL_BEAR": bear_core & (bear_a | bear_b)}).fillna(False)
    out["rsi"] = r.values; out["vol_ratio"] = (v / v.rolling(20, min_periods=20).mean()).values; out.index = df.index; return out


def futures_5m(sym: str, since_epoch: float | None = None, until_epoch: float | None = None, db: str | None = None, days_back: int | None = None) -> pd.DataFrame:
    """Complete 5-minute bars of the front-month future (the expiry with the largest volume on each session day), volume summed. 1-minute bars are binned on 5-minute boundaries of the exchange clock."""
    c = sqlite3.connect(f"file:{db or MH}?mode=ro", uri=True, timeout=30)
    try:
        q = "SELECT bar_start, session_date_ist, expiry, o, h, l, c, v FROM market_candles WHERE kind='FUTURE' AND symbol=? AND tf='1m'"; a: list = [sym]
        if since_epoch is not None: q += " AND bar_start>=?"; a.append(datetime.fromtimestamp(since_epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
        if until_epoch is not None: q += " AND bar_start<?"; a.append(datetime.fromtimestamp(until_epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
        d = pd.read_sql_query(q, c, params=a)
    finally:
        c.close()
    if d.empty: return pd.DataFrame(columns=["t", "o", "h", "l", "c", "v"])
    d["t1"] = pd.to_datetime(d["bar_start"], utc=True); d["v"] = d["v"].fillna(0.0)
    front = d.groupby(["session_date_ist", "expiry"])["v"].sum().reset_index().sort_values(["v", "expiry"]).groupby("session_date_ist").tail(1)[["session_date_ist", "expiry"]]
    d = d.merge(front, on=["session_date_ist", "expiry"]).drop_duplicates("t1").sort_values("t1"); d["bin"] = d["t1"].dt.floor("5min")
    g = d.groupby("bin").agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), v=("v", "sum"), n=("c", "size")).reset_index().rename(columns={"bin": "t"})
    now_cut = None if until_epoch is None else until_epoch
    g = g[g["n"] >= 4]                                                                         # a bin with fewer than 4 of its 5 minutes is not a complete bar
    if now_cut is not None: g = g[((g["t"] + pd.Timedelta(minutes=5)) - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds() <= now_cut]
    return g[["t", "o", "h", "l", "c", "v"]].reset_index(drop=True)
