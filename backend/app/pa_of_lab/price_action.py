"""Price-action engine: trade WITH the trend, at a KEY LEVEL, on a rejection candle, 1:RR. Pure/causal: signal at bar i uses bars <= i only.
bars: list of dicts {t,o,h,l,c}; t = bar start epoch (any monotone key). `day` key (str) optional, used for prior-day levels."""
from __future__ import annotations
from dataclasses import dataclass


WIN = 150   # swing look-back window in bars: bounds cost; the signal stays a pure function of the last WIN bars


@dataclass
class Signal:
    i: int
    side: int          # +1 long, -1 short
    entry: float       # trigger price (beyond signal candle extreme); fill only if next bars trade through it
    stop: float
    target: float
    reason: str


def swings(bars, i, k=3):
    """confirmed swing highs/lows up to bar i (a swing at j needs k bars on each side, so j <= i-k)."""
    sh, sl = [], []
    for j in range(max(k, i - WIN), i - k + 1):
        h, l = bars[j]["h"], bars[j]["l"]
        if all(h > bars[j - m]["h"] and h >= bars[j + m]["h"] for m in range(1, k + 1)): sh.append((j, h))
        if all(l < bars[j - m]["l"] and l <= bars[j + m]["l"] for m in range(1, k + 1)): sl.append((j, l))
    return sh, sl


def trend(bars, i, k=3):
    sh, sl = swings(bars, i, k)
    if len(sh) < 2 or len(sl) < 2: return 0
    if sh[-1][1] > sh[-2][1] and sl[-1][1] > sl[-2][1]: return 1
    if sh[-1][1] < sh[-2][1] and sl[-1][1] < sl[-2][1]: return -1
    return 0


def rejection(b, prev):
    """+1 bullish rejection (hammer / bullish engulfing), -1 bearish, 0 none."""
    rng = b["h"] - b["l"]
    if rng <= 0: return 0
    body = abs(b["c"] - b["o"]); up = b["h"] - max(b["o"], b["c"]); lo = min(b["o"], b["c"]) - b["l"]
    if lo >= 2 * body and lo >= 0.55 * rng and b["c"] >= b["o"]: return 1
    if up >= 2 * body and up >= 0.55 * rng and b["c"] <= b["o"]: return -1
    if prev and b["c"] > b["o"] and prev["c"] < prev["o"] and b["c"] >= prev["o"] and b["o"] <= prev["c"]: return 1
    if prev and b["c"] < b["o"] and prev["c"] > prev["o"] and b["c"] <= prev["o"] and b["o"] >= prev["c"]: return -1
    return 0


def key_levels(bars, i):
    """prior-day high/low (needs 'day' key) + last confirmed swing high/low."""
    lv = []
    d = bars[i].get("day")
    if d is not None:
        j = i
        while j >= 0 and bars[j].get("day") == d: j -= 1
        if j >= 0:
            pd = bars[j]["day"]; pb = []
            while j >= 0 and bars[j].get("day") == pd: pb.append(bars[j]); j -= 1
            lv += [max(b["h"] for b in pb), min(b["l"] for b in pb)]
    sh, sl = swings(bars, i)
    lv += [h for _, h in sh[-2:]] + [l for _, l in sl[-2:]]
    return lv


def signal_at(bars, i, rr=2.0, near_bps=8.0, min_stop_bps=10.0, k=3) -> Signal | None:
    if i < 4 * k + 2: return None
    tr = trend(bars, i, k)
    if tr == 0: return None
    b = bars[i]; rj = rejection(b, bars[i - 1])
    if rj != tr: return None
    tol = b["c"] * near_bps / 1e4
    probe = b["l"] if tr > 0 else b["h"]
    if not any(abs(probe - L) <= tol for L in key_levels(bars, i)): return None
    if tr > 0: entry, stop = b["h"], b["l"]
    else: entry, stop = b["l"], b["h"]
    risk = abs(entry - stop)
    if risk / entry * 1e4 < min_stop_bps: return None
    return Signal(i, tr, entry, stop, entry + tr * rr * risk, f"trend{tr:+d}+level+rejection")
