"""Index candle loader (read-only) and causal multi-timeframe preparation."""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

import numpy as np
import pandas as pd

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "market_history.db")
OPEN_MIN = 9 * 60 + 15; LAST_MIN = 15 * 60 + 29


def load_index_1m(symbol: str, start: str | None = None, end: str | None = None, db: str | None = None) -> pd.DataFrame:
    """1-minute INDEX candles of the regular session (09:15..15:29 IST), IST index, duplicates dropped, NO forward fill (a missing minute stays missing)."""
    c = sqlite3.connect(f"file:{db or DB}?mode=ro", uri=True, timeout=60)
    try:
        q = "SELECT bar_start, o, h, l, c FROM market_candles WHERE symbol=? AND kind='INDEX' AND tf='1m'"; p: list = [symbol]
        if start: q += " AND bar_start>=?"; p.append(start)
        if end: q += " AND bar_start<=?"; p.append(end)
        d = pd.read_sql_query(q + " ORDER BY bar_start", c, params=p)
    finally:
        c.close()
    d["ts"] = pd.to_datetime(d["bar_start"], utc=True).dt.tz_convert("Asia/Kolkata"); d = d.drop_duplicates("ts").set_index("ts").sort_index()
    m = d.index.hour * 60 + d.index.minute; d = d[(m >= OPEN_MIN) & (m <= LAST_MIN)]
    return d.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})[["open", "high", "low", "close"]].astype(float)


def _rma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n: return out
    out[n - 1] = np.nanmean(x[:n])
    for i in range(n, len(x)): out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def _ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.empty(len(x)); a = 2.0 / (n + 1); out[0] = x[0]
    for i in range(1, len(x)): out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def _atr(h, l, c, n=14):
    pc = np.r_[c[0], c[:-1]]; tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)]); return _rma(tr, n)


@dataclass
class TF:
    """One higher timeframe series. `end` = absolute minute at which each bar COMPLETES (a decision may use it only at/after that minute)."""
    k: int; start: np.ndarray; end: np.ndarray; o: np.ndarray; h: np.ndarray; l: np.ndarray; c: np.ndarray; n: np.ndarray; day: np.ndarray
    atr: np.ndarray; ema20: np.ndarray; ema50: np.ndarray; sh: np.ndarray; sl: np.ndarray        # sh/sl[i] = pivot high/low price CONFIRMED at bar i (known when bar i completes), else nan


def _fractals(h, l, w=2):
    n = len(h); sh = np.full(n, np.nan); sl = np.full(n, np.nan)
    for j in range(w, n - w):
        if h[j] > h[j - w:j].max() and h[j] > h[j + 1:j + w + 1].max(): sh[j + w] = h[j]          # confirmed w bars later: not before
        if l[j] < l[j - w:j].min() and l[j] < l[j + 1:j + w + 1].min(): sl[j + w] = l[j]
    return sh, sl


def build_tf(d1: pd.DataFrame, k: int) -> TF:
    idx = d1.index; day = (idx.year * 10000 + idx.month * 100 + idx.day).to_numpy(); mod = (idx.hour * 60 + idx.minute).to_numpy()
    bucket = (mod - OPEN_MIN) // k; key = day.astype(np.int64) * 1000 + bucket
    g = d1.assign(_k=key, _day=day, _mod=mod).groupby("_k", sort=True)
    o = g["open"].first().to_numpy(); h = g["high"].max().to_numpy(); l = g["low"].min().to_numpy(); c = g["close"].last().to_numpy(); n = g["close"].size().to_numpy()
    dd = g["_day"].first().to_numpy(); bk = (g["_mod"].first().to_numpy() - OPEN_MIN) // k
    dord = pd.to_datetime(dd.astype(str), format="%Y%m%d").to_numpy().astype("datetime64[D]").astype(np.int64)
    start = dord * 1440 + OPEN_MIN + bk * k; sh, sl = _fractals(h, l)
    return TF(k, start, start + k, o, h, l, c, n, dd, _atr(h, l, c, 14), _ema(c, 20), _ema(c, 50), sh, sl)


def minute_axis(d1: pd.DataFrame) -> np.ndarray:
    idx = d1.index; dd = (idx.year * 10000 + idx.month * 100 + idx.day).astype(str)
    dord = pd.to_datetime(dd, format="%Y%m%d").to_numpy().astype("datetime64[D]").astype(np.int64)
    return dord * 1440 + (idx.hour * 60 + idx.minute).to_numpy()


@dataclass
class Prep:
    sym: str; d1: pd.DataFrame; t1: np.ndarray; o: np.ndarray; h: np.ndarray; l: np.ndarray; c: np.ndarray; day: np.ndarray; tf5: TF; tf15: TF
    pdh: dict; pdl: dict; pdc: dict                     # previous session high / low / close by day key


def prepare(sym: str, d1: pd.DataFrame) -> Prep:
    d1 = d1[~d1.index.duplicated(keep="first")].sort_index()
    day = (d1.index.year * 10000 + d1.index.month * 100 + d1.index.day).to_numpy()
    dh = d1.groupby(day)["high"].max(); dl = d1.groupby(day)["low"].min(); dc = d1.groupby(day)["close"].last(); keys = list(dh.index)
    pdh = {keys[i]: float(dh.iloc[i - 1]) for i in range(1, len(keys))}; pdl = {keys[i]: float(dl.iloc[i - 1]) for i in range(1, len(keys))}; pdc = {keys[i]: float(dc.iloc[i - 1]) for i in range(1, len(keys))}
    return Prep(sym, d1, minute_axis(d1), d1["open"].to_numpy(), d1["high"].to_numpy(), d1["low"].to_numpy(), d1["close"].to_numpy(), day, build_tf(d1, 5), build_tf(d1, 15), pdh, pdl, pdc)
