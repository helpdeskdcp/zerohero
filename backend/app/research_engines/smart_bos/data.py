"""1-minute OHLC loader for the Smart BoS/CHoCH research engine.

Reads market_history.db read-only through the indexed (symbol, kind, tf, bar_start)
path, in bounded date slices, and caches a compact .npz so repeated research runs do
not rescan the 8.7 GB capture DB. Timestamps are normalised to UTC epoch seconds and
the IST trading date is derived from them; duplicate bars are dropped (first kept).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np

_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "market_history.db")
_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "research", "smart_bos", "cache")
IST = timezone(timedelta(hours=5, minutes=30))


def _epoch(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())


def load_1m(symbol: str, kind: str = "INDEX", start: str = "2017-01-01", end: str = "2027-01-01",
            use_cache: bool = True, db_path: str | None = None) -> dict:
    """{'t': epoch[], 'o','h','l','c','v' arrays} sorted by time, deduped."""
    key = f"{symbol}_{kind}_{start}_{end}.npz"
    path = os.path.join(_CACHE, key)
    if use_cache and os.path.exists(path):
        z = np.load(path)
        return {k: z[k] for k in z.files}
    conn = sqlite3.connect(f"file:{os.path.abspath(db_path or _DB)}?mode=ro", uri=True, timeout=60)
    rows = []
    y0, y1 = int(start[:4]), int(end[:4])
    for y in range(y0, y1 + 1):
        a = max(start, f"{y}-01-01")
        b = min(end, f"{y + 1}-01-01")
        if a >= b:
            continue
        rows += conn.execute(
            "SELECT bar_start, o, h, l, c, COALESCE(v,0) FROM market_candles "
            "WHERE symbol=? AND kind=? AND tf='1m' AND bar_start>=? AND bar_start<? ORDER BY bar_start",
            (symbol, kind, a, b)).fetchall()
    conn.close()
    seen, t, o, h, l, c, v = set(), [], [], [], [], [], []
    for r in rows:
        e = _epoch(r[0])
        if e in seen or None in r[1:5]:
            continue
        seen.add(e)
        t.append(e); o.append(r[1]); h.append(r[2]); l.append(r[3]); c.append(r[4]); v.append(r[5])
    order = np.argsort(t)
    out = {k: np.asarray(a, dtype=np.float64)[order] if k != "t" else np.asarray(a, dtype=np.int64)[order]
           for k, a in (("t", t), ("o", o), ("h", h), ("l", l), ("c", c), ("v", v))}
    if use_cache:
        os.makedirs(_CACHE, exist_ok=True)
        np.savez_compressed(path, **out)
    return out


def ist_minutes(t: np.ndarray):
    """(ist_date_ordinal[], minute_of_day[]) for epoch seconds."""
    ist = t + 19800
    days = ist // 86400
    return days, (ist % 86400) // 60
