"""Orderflow engine on bars that carry aggressor volume: {t,o,h,l,c,buy,sell}. Pure/causal. Needs tick-level aggressor data, which exists only
from the live Dhan feed (no historical aggressor data in India) -> this runs live/paper or on stored footprint bars, not on 5y history.
Signals: ABSORPTION (heavy one-sided aggression, price does not move, next bar confirms) and EXHAUSTION (new extreme with CVD divergence)."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class OFSignal:
    i: int
    side: int
    kind: str
    entry: float
    stop: float
    target: float


def delta(b): return b["buy"] - b["sell"]


def cvd(bars, i, n):
    return sum(delta(b) for b in bars[max(0, i - n + 1): i + 1])


def absorption_at(bars, i, lookback=20, vol_mult=1.5, move_frac=0.35, rr=2.0) -> OFSignal | None:
    """bar i-1 = absorption bar (volume >= vol_mult x avg, |delta| dominates, range small), bar i = confirmation in the absorbed side's favour."""
    if i < lookback + 2: return None
    a, c = bars[i - 1], bars[i]
    avg_v = sum(b["buy"] + b["sell"] for b in bars[i - 1 - lookback: i - 1]) / lookback
    avg_r = sum(b["h"] - b["l"] for b in bars[i - 1 - lookback: i - 1]) / lookback
    v = a["buy"] + a["sell"]
    if avg_v <= 0 or v < vol_mult * avg_v or (a["h"] - a["l"]) > move_frac * 3 * avg_r: return None
    d = delta(a)
    if abs(d) < 0.3 * v: return None
    side = -1 if d > 0 else 1                     # aggressive buyers absorbed by resting sellers -> short; and vice versa
    if side > 0 and not (c["c"] > a["h"] and delta(c) > 0): return None
    if side < 0 and not (c["c"] < a["l"] and delta(c) < 0): return None
    entry = c["c"]; stop = a["l"] if side > 0 else a["h"]
    risk = abs(entry - stop)
    return OFSignal(i, side, "ABSORPTION", entry, stop, entry + side * rr * risk) if risk > 0 else None


def exhaustion_at(bars, i, lookback=12, rr=2.0) -> OFSignal | None:
    """price makes a new lookback high/low but CVD over the same window does not confirm; fire on the reversal close."""
    if i < lookback + 2: return None
    b, w = bars[i], bars[i - lookback: i]
    hi, lo = max(x["h"] for x in w), min(x["l"] for x in w)
    prev_cvd = cvd(bars, i - 1, lookback)
    if b["h"] > hi and cvd(bars, i, lookback) < prev_cvd and b["c"] < b["o"]:
        entry, stop, side = b["c"], b["h"], -1
    elif b["l"] < lo and cvd(bars, i, lookback) > prev_cvd and b["c"] > b["o"]:
        entry, stop, side = b["c"], b["l"], 1
    else: return None
    risk = abs(entry - stop)
    return OFSignal(i, side, "EXHAUSTION", entry, stop, entry + side * rr * risk) if risk > 0 else None
