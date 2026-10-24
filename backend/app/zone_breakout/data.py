"""5- and 15-minute index bars for the Zone Breakout strategy. NIFTY / BANKNIFTY: Kaggle 5m 2015-2026 extended with the primary Angel capture (market_candles 1m); SENSEX: market_candles 1m;
FINNIFTY / MIDCPNIFTY / BANKEX: Upstox 1m pull (data/historical/zone_breakout/index_1m.db) extended with the capture. Regular session only (09:15-15:30 IST). Returns the bot's frame:
timestamp (IST tz-aware, bar START), open, high, low, close, volume (=1, cash indices have no volume)."""
from __future__ import annotations

import os
import sqlite3

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
MH = os.path.join(ROOT, "data", "market_history.db"); PULL = os.path.join(ROOT, "data", "historical", "zone_breakout", "index_1m.db")
INDICES = ["NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY", "BANKEX"]
COST_BP = {"NIFTY": 1.0, "BANKNIFTY": 1.0, "SENSEX": 1.5, "BANKEX": 1.5, "FINNIFTY": 2.0, "MIDCPNIFTY": 2.0}                  # assumed index-future round trip (spec)


def _session_only(d: pd.DataFrame) -> pd.DataFrame:
    ist = d["timestamp"].dt.tz_convert("Asia/Kolkata"); m = ist.dt.hour * 60 + ist.dt.minute
    return d[(m >= 9 * 60 + 15) & (m < 15 * 60 + 30)].reset_index(drop=True)


def _resample_1m(d1: pd.DataFrame, minutes: int = 5, min_n: int = 4) -> pd.DataFrame:
    """d1: timestamp (UTC or IST aware), open, high, low, close. Bins are aligned to the exchange clock (09:15 + k x minutes); a bin needs >= min_n 1-minute bars."""
    d = d1.copy(); ist = d["timestamp"].dt.tz_convert("Asia/Kolkata"); d["bin"] = ist.dt.floor(f"{minutes}min")
    g = d.groupby("bin").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), n=("close", "size")).reset_index().rename(columns={"bin": "timestamp"})
    g = g[g["n"] >= min_n].drop(columns="n"); g["volume"] = 1.0; return g


def _capture_1m(sym: str, since_utc: str | None = None, db: str | None = None) -> pd.DataFrame:
    c = sqlite3.connect(f"file:{db or MH}?mode=ro", uri=True, timeout=60)
    try:
        q = "SELECT bar_start, o, h, l, c FROM market_candles WHERE symbol=? AND kind='INDEX' AND tf='1m' AND c IS NOT NULL"; a: list = [sym]
        if since_utc: q += " AND bar_start>=?"; a.append(since_utc)
        d = pd.read_sql_query(q + " ORDER BY bar_start", c, params=a)
    finally:
        c.close()
    if d.empty: return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])
    d["timestamp"] = pd.to_datetime(d["bar_start"], utc=True); return d.drop_duplicates("timestamp").rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})[["timestamp", "open", "high", "low", "close"]]


def _pulled_1m(sym: str, path: str | None = None) -> pd.DataFrame:
    p = path or PULL
    if not os.path.exists(p): return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30)
    try: d = pd.read_sql_query("SELECT ts, o, h, l, c FROM bars WHERE symbol=? ORDER BY ts", c, params=(sym,))
    finally: c.close()
    d["timestamp"] = pd.to_datetime(d["ts"], unit="s", utc=True); return d.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})[["timestamp", "open", "high", "low", "close"]]


def load_5m(sym: str) -> pd.DataFrame:
    sym = sym.upper()
    if sym in ("NIFTY", "BANKNIFTY"):
        from ..index_signal_research.data import load_index_5m
        k = load_index_5m(sym).rename(columns={"t": "timestamp", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}); k["timestamp"] = k["timestamp"].dt.tz_convert("Asia/Kolkata"); k["volume"] = 1.0
        last = k["timestamp"].iloc[-1]; cap = _capture_1m(sym, since_utc=(last.tz_convert("UTC") + pd.Timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S"))
        ext = _resample_1m(cap, 5) if len(cap) else pd.DataFrame(columns=k.columns)
        d = pd.concat([k[["timestamp", "open", "high", "low", "close", "volume"]], ext], ignore_index=True)
    elif sym == "SENSEX":
        d = _resample_1m(_capture_1m(sym), 5)
    else:
        a = _pulled_1m(sym); b = _capture_1m(sym); one = pd.concat([a, b], ignore_index=True).drop_duplicates("timestamp", keep="last").sort_values("timestamp"); d = _resample_1m(one, 5)
    d["timestamp"] = d["timestamp"].dt.tz_convert("Asia/Kolkata"); d = d.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    return _session_only(d)


def to_15m(d5: pd.DataFrame) -> pd.DataFrame:
    d = d5.copy(); d["bin"] = d["timestamp"].dt.floor("15min")
    g = d.groupby("bin").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), n=("close", "size")).reset_index().rename(columns={"bin": "timestamp"})
    g = g[g["n"] == 3].drop(columns="n"); g["volume"] = 1.0; return g


def angel_history(sym: str, days: int = 30, interval: str = "FIVE_MINUTE", fetch=None, chunk_days: int = 20) -> pd.DataFrame:
    """Historical bars straight from ZeroHero's PRIMARY Angel One adapter (app/connectors/angelone.fetch_candles: shared session, cached, rate-limit aware) -> the bot's frame.
    Not used by the live loop (that reads the capture to avoid REST load); used to seed / back-fill and to cross-check the capture. `fetch` is injectable for tests."""
    from datetime import datetime, timedelta
    from .. import instruments
    meta = instruments.resolve(sym) or {}
    if fetch is None:
        from ..connectors import angelone
        fetch = lambda f, t: angelone.fetch_candles(meta.get("market"), sym, meta.get("exchange"), meta.get("symboltoken"), interval, f, t)
    end = datetime.now(); rows = []; cur = end - timedelta(days=days)
    while cur < end:
        nxt = min(cur + timedelta(days=chunk_days), end); r = fetch(cur.strftime("%Y-%m-%d %H:%M"), nxt.strftime("%Y-%m-%d %H:%M"))
        if (r or {}).get("data_status") not in ("OK", "STALE"): raise RuntimeError(f"angel candles {sym}: {(r or {}).get('data_status')} {(r or {}).get('reason', '')}")
        rows += r.get("candles") or []; cur = nxt
    if not rows: return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    d = pd.DataFrame(rows).rename(columns={"t": "timestamp", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata"); d["volume"] = 1.0
    return _session_only(d[["timestamp", "open", "high", "low", "close", "volume"]].drop_duplicates("timestamp", keep="last").sort_values("timestamp"))
