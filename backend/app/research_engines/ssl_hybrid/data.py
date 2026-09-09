"""READ-ONLY N-minute index-bar loaders for the SSL Hybrid engine.

Sources:
  NIFTY      Kaggle 1m 2015-2025 (mavrick136, has a `volume` col but it is 0.0)
             + 2008-2020 (nishanthsalian, no volume)
  BANKNIFTY  Kaggle 1m 2015-2024 (sandeepkapri, split Date/Time, no volume)
  SENSEX     market_history.db INDEX 1m, ~4 sessions only -> INSUFFICIENT

Volume is 0 / absent everywhere in the multi-year history, so downstream the
VWAP is computed as a disclosed price-proxy (see indicators.session_vwap).
Missing data is reported, never invented.
"""
from __future__ import annotations

import csv
import os
import pickle
import sqlite3
from datetime import datetime, timezone, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_MH_DB = os.path.join(_ROOT, "data", "market_history.db")
_KAGGLE = os.path.join(_ROOT, "data", "historical", "kaggle")
_CACHE = os.path.join(_ROOT, "data", "research", "ssl_hybrid", "_cache")
_IST = timezone(timedelta(hours=5, minutes=30))

_KAGGLE_1M = {
    "NIFTY": [
        (os.path.join(_KAGGLE, "mavrick136__10-years-of-nifty-50-1-minute-data-20152025",
                      "NIFTY_1MIN_2015-2025.csv"), "single"),
        (os.path.join(_KAGGLE, "nishanthsalian__indian-stock-index-1minute-data-2008-2020",
                      "NIFTY_2008_2020.csv"), "date_time"),
    ],
    "BANKNIFTY": [
        (os.path.join(_KAGGLE, "sandeepkapri__banknifty-data-upto-2024",
                      "bank-nifty-1m-data.csv"), "date_time"),
    ],
    "SENSEX": [],
}


def _epoch(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s if s < 1e12 else s / 1000.0)
    t = str(s).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is not None:
            dt = dt.astimezone(_IST).replace(tzinfo=timezone.utc)
        else:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        pass
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
              "%m/%d/%Y %H:%M:%S", "%Y%m%d %H:%M", "%Y%m%d %H:%M:%S"):
        try:
            return datetime.strptime(t, f).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def _parse_1m_csv(path: str, kind: str) -> list[tuple]:
    """-> sorted [(t, o, h, l, c, v)] 1-minute tuples (v = 0.0 when absent)."""
    rows: list[tuple] = []
    with open(path, newline="") as fh:
        rd = csv.reader(fh)
        header = [h.lower().strip() for h in next(rd, [])]
        idx = {h: i for i, h in enumerate(header)}
        vcol = idx.get("volume")
        for r in rd:
            try:
                if kind == "date_time":
                    t = _epoch(f"{r[idx['date']]} {r[idx['time']]}")
                else:
                    dc = idx.get("datetime", idx.get("date", idx.get("timestamp", 0)))
                    t = _epoch(r[dc])
                if t is None:
                    continue
                v = 0.0
                if vcol is not None:
                    try:
                        v = float(r[vcol])
                    except (ValueError, IndexError):
                        v = 0.0
                rows.append((t, float(r[idx["open"]]), float(r[idx["high"]]),
                             float(r[idx["low"]]), float(r[idx["close"]]), v))
            except (ValueError, IndexError, KeyError):
                continue
    rows.sort()
    return rows


def _cached_1m(path: str, kind: str) -> list[tuple]:
    try:
        st = os.stat(path)
        os.makedirs(_CACHE, exist_ok=True)
        ck = os.path.join(_CACHE, f"{os.path.basename(path)}.{int(st.st_mtime)}.{st.st_size}.pkl")
        if os.path.exists(ck):
            with open(ck, "rb") as fh:
                return pickle.load(fh)
    except OSError:
        ck = None
    rows = _parse_1m_csv(path, kind)
    if ck:
        try:
            with open(ck, "wb") as fh:
                pickle.dump(rows, fh, protocol=4)
        except OSError:
            pass
    return rows


def _to_tf(ones: list[tuple], tf_min: int) -> list[dict]:
    step = tf_min * 60
    out: list[dict] = []
    cur = None
    b = None
    for (t, o, h, l, c, v) in ones:
        bk = int(t // step) * step
        if bk != cur:
            if b is not None:
                out.append(b)
            cur = bk
            b = {"t": bk, "o": o, "h": h, "l": l, "c": c, "v": v, "n": 1}
        else:
            b["h"] = max(b["h"], h)
            b["l"] = min(b["l"], l)
            b["c"] = c
            b["v"] += v
            b["n"] += 1
    if b is not None:
        out.append(b)
    for bar in out:
        d = datetime.fromtimestamp(bar["t"], tz=timezone.utc)
        bar["session_date"] = d.strftime("%Y-%m-%d")
        bar["hhmm"] = d.strftime("%H:%M")
        bar["minute_of_day"] = d.hour * 60 + d.minute
        bar["hlc3"] = (bar["h"] + bar["l"] + bar["c"]) / 3.0
    return out


def _ro(path):
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.OperationalError:
        return None


_MEM: dict = {}


def load_bars(symbol: str, *, tf_min: int = 15, start: str | None = None,
              end: str | None = None) -> list[dict]:
    sym = symbol.upper()
    key = (sym, tf_min, start, end)
    if key in _MEM:
        return _MEM[key]
    s_ep = _epoch(start) if start else 0.0
    e_ep = _epoch(end) if end else 9e18
    ones: list[tuple] = []
    src = None
    for path, kind in _KAGGLE_1M.get(sym, []):
        if os.path.exists(path):
            ones += [r for r in _cached_1m(path, kind) if s_ep <= r[0] <= e_ep]
            src = "kaggle"
    if not ones:
        con = _ro(_MH_DB)
        if con:
            try:
                for r in con.execute(
                    "SELECT bar_start,o,h,l,c,COALESCE(v,0) v FROM market_candles "
                    "WHERE symbol=? AND kind='INDEX' AND tf='1m' ORDER BY bar_start", (sym,)):
                    t = _epoch(r["bar_start"])
                    if t is not None and r["c"] is not None and s_ep <= t <= e_ep:
                        ones.append((t, r["o"], r["h"], r["l"], r["c"], r["v"] or 0.0))
                src = "market_history.db"
            finally:
                con.close()
    ones.sort()
    bars = _to_tf(ones, tf_min)
    total_v = sum(b["v"] for b in bars)
    for b in bars:
        b["source"] = src
        b["has_volume"] = total_v > 0
    _MEM[key] = bars
    return bars


def coverage(tf_min: int = 15) -> dict:
    rep = {"generated_at": datetime.now(timezone.utc).isoformat(), "tf_min": tf_min,
           "by_symbol": {}, "notes": []}
    for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
        bars = load_bars(sym, tf_min=tf_min)
        if bars:
            sess = len({b["session_date"] for b in bars})
            rep["by_symbol"][sym] = {
                "source": bars[0]["source"], "n_bars": len(bars), "sessions": sess,
                "range": [bars[0]["session_date"], bars[-1]["session_date"]],
                "has_volume": bars[0]["has_volume"],
                "verdict": "OOS_CAPABLE" if sess >= 60 else "INSUFFICIENT_SAMPLE",
            }
        else:
            rep["by_symbol"][sym] = {"source": None, "n_bars": 0, "verdict": "NO_DATA"}
    rep["notes"] = [
        f"{tf_min}m bars resampled from Kaggle 1m history (NIFTY, BANKNIFTY).",
        "Volume is 0/absent in all multi-year history -> VWAP is a session-anchored "
        "cumulative HLC3 mean (price-VWAP proxy); the 5-pt volume score is inert.",
        "SENSEX: only market_history.db INDEX 1m (~4 sessions) -> INSUFFICIENT_SAMPLE.",
    ]
    return rep
