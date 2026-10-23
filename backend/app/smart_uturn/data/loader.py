"""CSV / DataFrame ingestion. Output is RAW (unsorted, possibly duplicated): run quality.validate_one_minute next."""
from __future__ import annotations

import pandas as pd

IST = "Asia/Kolkata"
_ALIASES = {"datetime": "ts", "timestamp": "ts", "time": "time", "date": "date", "o": "open", "h": "high", "l": "low", "c": "close", "vol": "volume", "openinterest": "oi", "open_interest": "oi",
            "implied_volatility": "iv", "bid_price": "bid", "ask_price": "ask", "bidprice": "bid", "askprice": "ask", "bid_quantity": "bid_qty", "ask_quantity": "ask_qty"}


def load_dataframe(df: pd.DataFrame, *, ts_is: str = "open", tz: str = IST, source: str = "csv", interval_minutes: int = 1, dayfirst: bool = False) -> pd.DataFrame:
    if ts_is not in ("open", "close"): raise ValueError("ts_is must be 'open' or 'close'")
    d = df.rename(columns={c: _ALIASES.get(str(c).strip().lower(), str(c).strip().lower()) for c in df.columns}).copy()
    if "ts" not in d.columns:
        if "date" in d.columns and "time" in d.columns: d["ts"] = d["date"].astype(str) + " " + d["time"].astype(str)
        else: raise ValueError("no timestamp column (datetime/timestamp or date+time)")
    ts = pd.to_datetime(d["ts"], dayfirst=dayfirst)
    ts = ts.dt.tz_localize(tz) if ts.dt.tz is None else ts.dt.tz_convert(tz)
    missing = [c for c in ("open", "high", "low", "close") if c not in d.columns]
    if missing: raise ValueError(f"missing columns: {missing}")
    if ts_is == "close": ts = ts - pd.Timedelta(minutes=interval_minutes)
    out = d.drop(columns=[c for c in ("ts", "date", "time") if c in d.columns]).set_index(ts)
    out.index.name = "open_time"
    for c in out.columns:
        if c not in ("source", "source_ts"): out[c] = pd.to_numeric(out[c], errors="coerce")
    if "source" not in out.columns: out["source"] = source
    return out


def load_csv(path: str, **kw) -> pd.DataFrame:
    return load_dataframe(pd.read_csv(path), source=kw.pop("source", path), **kw)
