"""Closed-candle indicators. Candle = dict(t=epoch seconds of bar START, o,h,l,c,v). Every function uses only the bars it is given, so feeding it
completed bars guarantees no look-ahead; closed_only() drops a still-forming bar. Values that cannot be computed are None, never guessed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def closed_only(candles: list[dict], now: float, interval_sec: int) -> list[dict]:
    """Keep bars whose end (t + interval) <= now."""
    return [c for c in candles if c["t"] + interval_sec <= now]


def validate_candles(candles: list[dict], interval_sec: int) -> str | None:
    """None if usable, else reason: EMPTY / BAD_OHLC / NOT_SORTED / DUPLICATE."""
    if not candles: return "EMPTY"
    prev = None
    for c in candles:
        if any(c.get(k) is None for k in "ohlc") or c["h"] < max(c["o"], c["c"], c["l"]) - 1e-9 or c["l"] > min(c["o"], c["c"], c["h"]) + 1e-9 or min(c["o"], c["h"], c["l"], c["c"]) <= 0: return "BAD_OHLC"
        if prev is not None:
            if c["t"] == prev: return "DUPLICATE"
            if c["t"] < prev: return "NOT_SORTED"
        prev = c["t"]
    return None


def sma(vals: list[float], n: int) -> float | None:
    return sum(vals[-n:]) / n if n > 0 and len(vals) >= n else None


def ema(vals: list[float], n: int) -> float | None:
    """Seeded with the SMA of the first n values (standard), then recursive."""
    if n <= 0 or len(vals) < n: return None
    k = 2 / (n + 1); e = sum(vals[:n]) / n
    for v in vals[n:]: e = v * k + e * (1 - k)
    return e


def atr(candles: list[dict], n: int = 14) -> float | None:
    """Wilder ATR."""
    if len(candles) < n + 1: return None
    trs = [max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"])) for p, c in zip(candles, candles[1:])]
    a = sum(trs[:n]) / n
    for tr in trs[n:]: a = (a * (n - 1) + tr) / n
    return a


def rel_volume(candles: list[dict], n: int = 20) -> float | None:
    """Last bar volume / mean of the n bars before it. None when the volume is unavailable (index candles carry none)."""
    if len(candles) < n + 1: return None
    base = [c.get("v") or 0 for c in candles[-n - 1:-1]]; m = sum(base) / n
    return None if m <= 0 else (candles[-1].get("v") or 0) / m


def session_day(t: float) -> str: return datetime.fromtimestamp(t, IST).strftime("%Y-%m-%d")


def vwap(candles: list[dict]) -> float | None:
    """Session VWAP of the LAST bar's IST day (typical price). None if the day has no traded volume (e.g. an index)."""
    if not candles: return None
    d = session_day(candles[-1]["t"]); pv = vol = 0.0
    for c in candles:
        if session_day(c["t"]) == d: v = c.get("v") or 0; pv += (c["h"] + c["l"] + c["c"]) / 3 * v; vol += v
    return pv / vol if vol > 0 else None


def opening_range(candles: list[dict], minutes: int = 15, open_hhmm: tuple[int, int] = (9, 15)) -> dict | None:
    """High/low of the first `minutes` of the last bar's session; None until that window has fully closed."""
    if not candles: return None
    d = session_day(candles[-1]["t"]); rows = [c for c in candles if session_day(c["t"]) == d]
    t0 = datetime.fromtimestamp(rows[0]["t"], IST).replace(hour=open_hhmm[0], minute=open_hhmm[1], second=0, microsecond=0).timestamp()
    win = [c for c in rows if t0 <= c["t"] < t0 + minutes * 60]
    if not win or rows[-1]["t"] < t0 + minutes * 60: return None          # window not fully closed yet
    return {"high": max(c["h"] for c in win), "low": min(c["l"] for c in win)}


def swings(candles: list[dict], k: int = 2) -> dict:
    """Confirmed swing highs/lows (a bar is a swing only after k lower/higher bars on BOTH sides have closed -> confirmed k bars late, no repaint)."""
    hi, lo = [], []
    for i in range(k, len(candles) - k):
        w = candles[i - k:i + k + 1]
        if candles[i]["h"] == max(c["h"] for c in w) and sum(c["h"] == candles[i]["h"] for c in w) == 1: hi.append((candles[i]["t"], candles[i]["h"]))
        if candles[i]["l"] == min(c["l"] for c in w) and sum(c["l"] == candles[i]["l"] for c in w) == 1: lo.append((candles[i]["t"], candles[i]["l"]))
    return {"highs": hi, "lows": lo}


def structure(candles: list[dict], k: int = 2) -> str:
    """HH_HL / LH_LL / MIXED / UNKNOWN from the last two confirmed swings each side."""
    s = swings(candles, k)
    if len(s["highs"]) < 2 or len(s["lows"]) < 2: return "UNKNOWN"
    up_h = s["highs"][-1][1] > s["highs"][-2][1]; up_l = s["lows"][-1][1] > s["lows"][-2][1]
    return "HH_HL" if up_h and up_l else "LH_LL" if not up_h and not up_l else "MIXED"


def support_resistance(candles: list[dict], k: int = 2, lookback: int = 60) -> dict:
    """Nearest confirmed swing levels around the last close within `lookback` bars."""
    w = candles[-lookback:]; s = swings(w, k); px = candles[-1]["c"] if candles else None
    res = sorted(v for _, v in s["highs"] if px is not None and v > px); sup = sorted((v for _, v in s["lows"] if px is not None and v < px), reverse=True)
    return {"resistance": res[0] if res else None, "support": sup[0] if sup else None}


def break_events(candles: list[dict], level: float, direction: str, retest_tol: float = 0.0015, max_retest_bars: int = 6) -> dict:
    """Event vs a level, on closed bars. direction UP = level is resistance, DOWN = level is support.
    BREAKOUT/BREAKDOWN: last close beyond the level and the bar before was not. RETEST: after a break within max_retest_bars price came back to within
    retest_tol of the level and the last close is back on the break side. REJECTION: last bar pierced the level intraday but closed back on the original side."""
    if len(candles) < 2 or level is None: return {"event": None}
    c, p = candles[-1], candles[-2]
    if direction == "UP":
        if c["c"] > level >= p["c"]: return {"event": "BREAKOUT"}
        if c["h"] > level >= c["c"]: return {"event": "REJECTION"}
        for j in range(2, min(max_retest_bars, len(candles) - 1) + 1):          # break at bar -j, retest now
            b, a = candles[-j], candles[-j - 1] if len(candles) > j else None
            if a is not None and b["c"] > level >= a["c"] and c["l"] <= level * (1 + retest_tol) and c["c"] > level: return {"event": "RETEST", "bars_since_break": j - 1}
    else:
        if c["c"] < level <= p["c"]: return {"event": "BREAKDOWN"}
        if c["l"] < level <= c["c"]: return {"event": "REJECTION"}
        for j in range(2, min(max_retest_bars, len(candles) - 1) + 1):
            b, a = candles[-j], candles[-j - 1] if len(candles) > j else None
            if a is not None and b["c"] < level <= a["c"] and c["h"] >= level * (1 - retest_tol) and c["c"] < level: return {"event": "RETEST", "bars_since_break": j - 1}
    return {"event": None}
