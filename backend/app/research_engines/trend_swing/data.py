"""READ-ONLY DAILY NIFTY bars: NIFTY 1m Kaggle history bucketed to one OHLC bar
per IST session date. Volume is 0 for the cash index (irrelevant to price-trend
signals). Missing data is reported, never invented."""
from __future__ import annotations

import csv
import os
import pickle
from datetime import datetime, timezone, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_KAGGLE = os.path.join(_ROOT, "data", "historical", "kaggle")
_CACHE = os.path.join(_ROOT, "data", "research", "trend_swing", "_cache")
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
}


def _epoch(s):
    if s is None:
        return None
    t = str(s).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(t)
        dt = dt.astimezone(_IST) if dt.tzinfo else dt.replace(tzinfo=_IST)
        return dt
    except ValueError:
        pass
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
              "%Y%m%d %H:%M", "%Y%m%d %H:%M:%S"):
        try:
            return datetime.strptime(t, f).replace(tzinfo=_IST)
        except ValueError:
            continue
    return None


def _parse_1m(path: str, kind: str):
    rows = []
    with open(path, newline="") as fh:
        rd = csv.reader(fh)
        header = [h.lower().strip() for h in next(rd, [])]
        idx = {h: i for i, h in enumerate(header)}
        for r in rd:
            try:
                if kind == "date_time":
                    dt = _epoch(f"{r[idx['date']]} {r[idx['time']]}")
                else:
                    dc = idx.get("datetime", idx.get("date", idx.get("timestamp", 0)))
                    dt = _epoch(r[dc])
                if dt is None:
                    continue
                rows.append((dt, float(r[idx["open"]]), float(r[idx["high"]]),
                             float(r[idx["low"]]), float(r[idx["close"]])))
            except (ValueError, IndexError, KeyError):
                continue
    rows.sort(key=lambda x: x[0])
    return rows


def _cached(path, kind):
    try:
        st = os.stat(path)
        os.makedirs(_CACHE, exist_ok=True)
        ck = os.path.join(_CACHE, f"{os.path.basename(path)}.{int(st.st_mtime)}.{st.st_size}.d.pkl")
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


_MEM: dict = {}


def load_daily(symbol: str = "NIFTY", *, start: str | None = None, end: str | None = None):
    """-> (bars, capability). bars: [{date, o,h,l,c, t (epoch of session date 00:00 IST)}]"""
    sym = symbol.upper()
    key = (sym, start, end)
    if key in _MEM:
        return _MEM[key]
    day: dict = {}
    for path, kind in _KAGGLE_1M.get(sym, []):
        if not os.path.exists(path):
            continue
        for (dt, o, h, l, c) in _cached(path, kind):
            d = dt.strftime("%Y-%m-%d")
            if start and d < start:
                continue
            if end and d > end:
                continue
            b = day.get(d)
            if b is None:
                day[d] = {"date": d, "o": o, "h": h, "l": l, "c": c,
                          "t": datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=_IST).timestamp(),
                          "n": 1}
            else:
                b["h"] = max(b["h"], h)
                b["l"] = min(b["l"], l)
                b["c"] = c
                b["n"] += 1
    bars = [day[d] for d in sorted(day)]
    # drop days with suspiciously few 1m rows (holiday half-data / feed gap)
    bars = [b for b in bars if b["n"] >= 30]
    cap = {
        "symbol": sym, "source": "kaggle_1m->daily", "n_days": len(bars),
        "range": [bars[0]["date"], bars[-1]["date"]] if bars else None,
        "volume_available": False,
        "note": "one OHLC bar per IST session date, resampled from 1m Kaggle. "
                "Cash index -> no volume; price-trend signals only.",
    }
    _MEM[key] = (bars, cap)
    return bars, cap
