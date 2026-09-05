"""
Smart-money (volume-spike) breakout setups — pure, deterministic, read-only.

Definition (confirmed with the operator 2026-09-05):
  * A "smart-money candle" is a bar whose volume is >= `volume_mult` x the
    session's average bar volume -- large players were active.
  * BUY setup: a later bar's HIGH breaks above the spike candle's HIGH ->
    entry = spike HIGH, stop = spike LOW, risk = HIGH - LOW,
    target = entry + rr x risk  (rr = 3 by default -> 1:3).
  * SELL setup: a later bar's LOW breaks below the spike candle's LOW ->
    entry = spike LOW, stop = spike HIGH, risk = HIGH - LOW,
    target = entry - rr x risk.

Outcome is walked forward through the remaining bars of the SAME session:
  PENDING   -- breakout level never traded through
  TRIGGERED -- broke out, neither target nor stop hit yet by session end
  TARGET_HIT / STOP_HIT -- self-explanatory
When a single bar's range spans BOTH the target and the stop, we cannot know
the intrabar order from ~5m bars, so we CONSERVATIVELY call it STOP_HIT
(standard pessimistic bar-backtest convention). Every result says so.
"""
from __future__ import annotations

from statistics import mean
from typing import Optional


def _num(x) -> Optional[float]:
    try:
        f = float(x)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _clean(bars: list) -> list:
    out = []
    for b in bars:
        h, l, o, c, v = (_num(b.get("h")), _num(b.get("l")), _num(b.get("o")),
                         _num(b.get("c")), _num(b.get("v")))
        if h is None or l is None or h < l:
            continue
        out.append({"bar_start": b.get("bar_start"), "o": o, "h": h, "l": l,
                    "c": c if c is not None else (h + l) / 2,
                    "v": v if (v is not None and v > 0) else 0.0})
    return out


def _walk_outcome(bars_after: list, entry: float, stop: float, target: float,
                  side: str) -> dict:
    """Walk the post-breakout bars; return status + which bar resolved it."""
    for i, b in enumerate(bars_after):
        hi, lo = b["h"], b["l"]
        if side == "BUY":
            hit_stop = lo <= stop
            hit_tgt = hi >= target
        else:
            hit_stop = hi >= stop
            hit_tgt = lo <= target
        if hit_stop and hit_tgt:
            return {"status": "STOP_HIT", "resolved_bar": b["bar_start"],
                    "note": "bar spanned both target and stop; STOP assumed (pessimistic)"}
        if hit_stop:
            return {"status": "STOP_HIT", "resolved_bar": b["bar_start"]}
        if hit_tgt:
            return {"status": "TARGET_HIT", "resolved_bar": b["bar_start"]}
    return {"status": "TRIGGERED", "resolved_bar": None}


def _first_breakout_idx(bars_after: list, level: float, side: str) -> Optional[int]:
    for i, b in enumerate(bars_after):
        if side == "BUY" and b["h"] > level:
            return i
        if side == "SELL" and b["l"] < level:
            return i
    return None


def _setup(spike: dict, bars_after: list, side: str, rr: float) -> dict:
    h, l = spike["h"], spike["l"]
    rng = h - l
    if side == "BUY":
        entry, stop = h, l
        target = entry + rr * rng
    else:
        entry, stop = l, h
        target = entry - rr * rng
    risk = abs(entry - stop)
    reward = abs(target - entry)
    d = {"side": side, "entry": round(entry, 4), "stop_loss": round(stop, 4),
         "target": round(target, 4), "risk_points": round(risk, 4),
         "reward_points": round(reward, 4),
         "rr": round(reward / risk, 2) if risk else None}
    bidx = _first_breakout_idx(bars_after, entry, side)
    if bidx is None:
        d["outcome"] = {"status": "PENDING", "resolved_bar": None}
        d["breakout_bar"] = None
    else:
        d["breakout_bar"] = bars_after[bidx]["bar_start"]
        d["outcome"] = _walk_outcome(bars_after[bidx:], entry, stop, target, side)
    return d


def smart_money_setups(bars: list, *, volume_mult: float = 2.0, rr: float = 3.0) -> dict:
    """Detect volume-spike candles and build BUY+SELL breakout setups for each,
    with a same-session forward-walked outcome."""
    clean = _clean(bars)
    vols = [b["v"] for b in clean if b["v"] > 0]
    if len(clean) < 3 or len(vols) < 3:
        return {"status": "NO_DATA", "reason": "need >=3 bars with volume",
                "setups": []}
    avg_v = mean(vols)
    if avg_v <= 0:
        return {"status": "NO_DATA", "reason": "zero average volume", "setups": []}

    setups = []
    for idx, b in enumerate(clean):
        if b["v"] < volume_mult * avg_v:
            continue
        after = clean[idx + 1:]
        setups.append({
            "candle": {"bar_start": b["bar_start"], "o": b["o"], "h": b["h"],
                       "l": b["l"], "c": b["c"], "v": b["v"]},
            "volume_x_avg": round(b["v"] / avg_v, 2),
            "range_points": round(b["h"] - b["l"], 4),
            "buy": _setup(b, after, "BUY", rr),
            "sell": _setup(b, after, "SELL", rr),
        })
    return {
        "status": "OK",
        "method": "OHLCV_BARS",
        "note": ("spike = bar volume >= volume_mult x session average bar volume; "
                 "breakout / target / stop evaluated at ~5m bar granularity, not "
                 "tick -- a bar spanning both target and stop is scored STOP_HIT"),
        "session_avg_volume": round(avg_v, 2),
        "volume_mult": volume_mult, "rr": rr,
        "spike_count": len(setups),
        "setups": setups,
    }
