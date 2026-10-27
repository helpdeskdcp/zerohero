"""Offline fake of the Upstox candle endpoints for Smart Chart tests: deterministic synthetic 1-minute NIFTY-like history
(weekdays, 09:15-15:29 IST) served through the REAL parse/merge/aggregate code paths."""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
LAST_DAY = date(2026, 10, 23)          # a Friday
DAYS = 260
_cache: dict = {}


def _weekdays(n: int, last: date) -> list[date]:
    out, d = [], last
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def minute_bars() -> list[tuple]:
    """ascending [(open_ms, o, h, l, c, v)] for DAYS weekdays; v=0 (index)."""
    if "m" in _cache:
        return _cache["m"]
    rnd = random.Random(7)
    p, out = 20000.0, []
    for di, d in enumerate(_weekdays(DAYS, LAST_DAY)):
        base = datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST)
        for m in range(375):
            o = p
            drift = math.sin((di * 375 + m) / 900.0) * 0.6
            c = max(100.0, o + drift + rnd.gauss(0, 2.2))
            h = max(o, c) + abs(rnd.gauss(0, 1.0))
            lo = min(o, c) - abs(rnd.gauss(0, 1.0))
            out.append((int((base + timedelta(minutes=m)).timestamp() * 1000), round(o, 2), round(h, 2), round(lo, 2), round(c, 2), 0.0))
            p = c
    _cache["m"] = out
    return out


def _agg(bars, native):
    if native == "1minute":
        return bars
    out, cur = [], None
    for ms, o, h, lo, c, v in bars:
        d = datetime.fromtimestamp(ms / 1000, IST)
        if native == "day":
            key = int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp() * 1000)
        else:
            mins = d.hour * 60 + d.minute - (9 * 60 + 15)
            key = ms - (mins % 30) * 60000
        if cur is None or cur[0] != key:
            cur = [key, o, h, lo, c, v]
            out.append(cur)
        else:
            cur[2], cur[3], cur[4], cur[5] = max(cur[2], h), min(cur[3], lo), c, cur[5] + v
    return [tuple(b) for b in out]


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, IST).isoformat()


def install(monkeypatch, *, today: date = date(2026, 10, 26), market_open: bool = False):
    """Patch upstox_ext + candles so no network/clock is used. `today` is a Monday after LAST_DAY (market closed => no intraday bars)."""
    from app.smart_chart import candles, upstox_ext
    calls = {"hist": 0, "intra": 0}

    def fetch_historical(key, interval, to_date, from_date):
        calls["hist"] += 1
        a = datetime.fromisoformat(from_date).replace(tzinfo=IST)
        b = datetime.fromisoformat(to_date).replace(tzinfo=IST) + timedelta(days=1)
        ams, bms = int(a.timestamp() * 1000), int(b.timestamp() * 1000)
        src = [x for x in minute_bars() if ams <= x[0] < bms]
        rows = [[_iso(x[0]), x[1], x[2], x[3], x[4], x[5], 0] for x in _agg(src, interval)]
        return upstox_ext.parse_candles(rows[::-1])        # Upstox returns newest-first

    def fetch_intraday(key, interval):
        calls["intra"] += 1
        return []

    monkeypatch.setattr(upstox_ext, "fetch_historical", fetch_historical)
    monkeypatch.setattr(upstox_ext, "fetch_intraday", fetch_intraday)
    monkeypatch.setattr(candles, "_today", lambda: today)
    monkeypatch.setattr(candles, "_market_open", lambda ex: market_open)
    candles.clear_cache()
    return calls


def candles_for_pine(n: int = 400, tf_min: int = 5) -> list[dict]:
    """n aggregated bars as the chart dict shape (for direct runner tests)."""
    from app.smart_chart import candles
    bars = candles.aggregate(minute_bars()[-n * tf_min - 400:], tf_min * 60)[-n:]
    return [{"time": b[0], "open": b[1], "high": b[2], "low": b[3], "close": b[4], "volume": b[5]} for b in bars]
