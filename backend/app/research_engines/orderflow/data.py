"""READ-ONLY N-minute NIFTY bar loader + capability flags. Kaggle 1m history
(volume = 0 for the cash index) resampled; market_history.db INDEX 1m fallback.
Missing data is reported, never invented."""
from __future__ import annotations

import csv
import os
import pickle
import sqlite3
from datetime import datetime, timezone, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_MH_DB = os.path.join(_ROOT, "data", "market_history.db")
_KAGGLE = os.path.join(_ROOT, "data", "historical", "kaggle")
_CACHE = os.path.join(_ROOT, "data", "research", "orderflow", "_cache")
_IST = timezone(timedelta(hours=5, minutes=30))

_KAGGLE_1M = {
    "NIFTY": [
        (os.path.join(_KAGGLE, "mavrick136__10-years-of-nifty-50-1-minute-data-20152025",
                      "NIFTY_1MIN_2015-2025.csv"), "single"),
        (os.path.join(_KAGGLE, "nishanthsalian__indian-stock-index-1minute-data-2008-2020",
                      "NIFTY_2008_2020.csv"), "date_time"),
    ],
}


def _epoch(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s if s < 1e12 else s / 1000.0)
    t = str(s).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(t)
        dt = dt.astimezone(_IST).replace(tzinfo=timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        pass
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
              "%Y%m%d %H:%M", "%Y%m%d %H:%M:%S"):
        try:
            return datetime.strptime(t, f).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def _parse_1m(path: str, kind: str) -> list[tuple]:
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
    rows = _parse_1m(path, kind)
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
    b = None
    cur = None
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


_MEM: dict = {}


def load(symbol: str = "NIFTY", *, tf_min: int = 5, start: str | None = None,
         end: str | None = None) -> tuple[list[dict], dict]:
    """-> (bars, capability). `capability` is the spec section-20 flag set."""
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
    if not ones and os.path.exists(_MH_DB):
        try:
            con = sqlite3.connect(f"file:{_MH_DB}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            for r in con.execute(
                "SELECT bar_start,o,h,l,c,COALESCE(v,0) v FROM market_candles "
                "WHERE symbol=? AND kind='INDEX' AND tf='1m' ORDER BY bar_start", (sym,)):
                t = _epoch(r["bar_start"])
                if t is not None and r["c"] is not None and s_ep <= t <= e_ep:
                    ones.append((t, r["o"], r["h"], r["l"], r["c"], r["v"] or 0.0))
            con.close()
            src = "market_history.db"
        except sqlite3.Error:
            pass
    ones.sort()
    bars = _to_tf(ones, tf_min)
    total_v = sum(b["v"] for b in bars)
    sessions = len({b["session_date"] for b in bars})
    cap = {
        "symbol": sym, "source": src, "tf_min": tf_min,
        "n_bars": len(bars), "n_sessions": sessions,
        "range": [bars[0]["session_date"], bars[-1]["session_date"]] if bars else None,
        "has_trades": False, "has_aggressor": False, "has_L1": False,
        "has_L2_snapshots": False, "has_L2_stream": False,
        "volume_available": total_v > 0,
        "cadence_sec": tf_min * 60,
        "true_orderflow": False,
        "note": ("TRUE ORDER FLOW NOT AVAILABLE -- NIFTY cash index: no aggressor "
                 "side, no per-trade size, no tick feed, volume=0. delta / CVD / "
                 "footprint / imbalance / absorption are PROXY or OMITTED."),
    }
    for b in bars:
        b["source"] = src
    _MEM[key] = (bars, cap)
    return bars, cap
