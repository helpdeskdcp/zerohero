"""
READ-ONLY 2-minute index-bar loaders for the Inside-Bar engine.

Sources (surveyed 2026-09-08):
  NIFTY      Kaggle 1m 2015-2025 (mavrick136) + 2008-2020 (nishanthsalian)  -> 2m
  BANKNIFTY  Kaggle 1m 2015-2024 (sandeepkapri, "Date"+"Time" split columns) -> 2m
  SENSEX     market_history.db market_candles INDEX 1m, ~4 sessions only     -> 2m
             (no usable Kaggle intraday: prathamsatani is DAILY)

coverage() states exactly what each symbol has. Missing data is reported,
never invented.
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
_CACHE = os.path.join(_ROOT, "data", "research", "inside_bar", "_cache")
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


def _epoch(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s if s < 1e12 else s / 1000.0)
    t = str(s).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(t)
        # Normalise to an IST wall-clock instant stored as a UTC epoch, so every
        # source lands on the same session clock (downstream reads with tz=utc).
        if dt.tzinfo is not None:
            dt = dt.astimezone(_IST).replace(tzinfo=timezone.utc)
        else:
            dt = dt.replace(tzinfo=timezone.utc)   # naive files are already IST wall-clock
        return dt.timestamp()
    except ValueError:
        pass
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
              "%m/%d/%Y %H:%M:%S", "%d-%B-%Y", "%d-%b-%Y", "%Y%m%d %H:%M", "%Y%m%d %H:%M:%S"):
        try:
            return datetime.strptime(t, f).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def _parse_1m_csv(path: str, kind: str) -> list[tuple]:
    """-> sorted [(t, o, h, l, c)] 1-minute tuples. `t` is epoch of the bar (naive
    IST treated as UTC -- consistent across the whole file, fine for intraday
    session logic which only uses hour:minute)."""
    rows: list[tuple] = []
    with open(path, newline="") as fh:
        rd = csv.reader(fh)
        header = [h.lower().strip() for h in next(rd, [])]
        idx = {h: i for i, h in enumerate(header)}
        for r in rd:
            try:
                if kind == "date_time":
                    t = _epoch(f"{r[idx['date']]} {r[idx['time']]}")
                else:
                    dc = idx.get("datetime", idx.get("date", idx.get("timestamp", 0)))
                    t = _epoch(r[dc])
                if t is None:
                    continue
                rows.append((t, float(r[idx["open"]]), float(r[idx["high"]]),
                             float(r[idx["low"]]), float(r[idx["close"]])))
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


def _to_2m(ones: list[tuple]) -> list[dict]:
    """1m tuples -> closed 2-minute bars, aligned to even minutes of the day."""
    out: list[dict] = []
    cur = None
    b = None
    for (t, o, h, l, c) in ones:
        bk = int(t // 120) * 120
        if bk != cur:
            if b is not None:
                out.append(b)
            cur = bk
            b = {"t": bk, "o": o, "h": h, "l": l, "c": c, "n": 0}
        if h > b["h"]:
            b["h"] = h
        if l < b["l"]:
            b["l"] = l
        b["c"] = c
        b["n"] += 1
    if b is not None:
        out.append(b)
    for bar in out:
        d = datetime.fromtimestamp(bar["t"], tz=timezone.utc)
        bar["session_date"] = d.strftime("%Y-%m-%d")
        bar["hhmm"] = d.strftime("%H:%M")
        bar["minute_of_day"] = d.hour * 60 + d.minute
    return out


def _ro(path):
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.OperationalError:
        return None


_MEM: dict = {}


def load_2m(symbol: str, *, start: str | None = None, end: str | None = None) -> list[dict]:
    sym = symbol.upper()
    key = (sym, start, end)
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
                    "SELECT bar_start,o,h,l,c FROM market_candles "
                    "WHERE symbol=? AND kind='INDEX' AND tf='1m' ORDER BY bar_start", (sym,)):
                    t = _epoch(r["bar_start"])
                    if t is not None and r["c"] is not None and s_ep <= t <= e_ep:
                        ones.append((t, r["o"], r["h"], r["l"], r["c"]))
                src = "market_history.db"
            finally:
                con.close()
    ones.sort()
    bars = _to_2m(ones)
    for b in bars:
        b["source"] = src
    _MEM[key] = bars
    return bars


def coverage() -> dict:
    rep = {"generated_at": datetime.now(timezone.utc).isoformat(), "by_symbol": {}, "notes": []}
    for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
        bars = load_2m(sym)
        if bars:
            rep["by_symbol"][sym] = {
                "source": bars[0]["source"], "n_2m_bars": len(bars),
                "range": [bars[0]["session_date"], bars[-1]["session_date"]],
                "sessions": len({b["session_date"] for b in bars}),
                "verdict": "OOS_CAPABLE" if len({b["session_date"] for b in bars}) >= 40
                           else "INSUFFICIENT_SAMPLE",
            }
        else:
            rep["by_symbol"][sym] = {"source": None, "n_2m_bars": 0, "verdict": "NO_DATA"}
    rep["notes"] = [
        "NIFTY / BANKNIFTY: 2m resampled from Kaggle 1m history -> real walk-forward OOS.",
        "SENSEX: only market_history.db INDEX 1m (~4 sessions) -- INSUFFICIENT_SAMPLE; "
        "the Kaggle SENSEX file (prathamsatani) is DAILY, not intraday.",
        "Cash-index volume is 0 everywhere -- this strategy uses price geometry + EMA only.",
        "1m timestamps are treated as naive session-clock; intraday session rules "
        "(no-entry-after / hard-exit) use hour:minute, so this is exact.",
    ]
    return rep
