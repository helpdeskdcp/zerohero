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
                  side: str, *, trail_dist: float = 0.0) -> dict:
    """Walk the post-breakout bars; return status + which bar resolved it +
    the REALIZED points (signed, in the trade's favourable direction) and the
    exit price.

    `trail_dist` > 0 enables a trailing stop: after entry the stop ratchets to
    stay `trail_dist` behind the best price seen (never loosens). A trailed
    exit can therefore land in profit -- points is `exit - entry` (BUY) /
    `entry - exit` (SELL), which _trade_from_leg turns into WIN/LOSS/FLAT by
    its sign, not by the status label.
    """
    cur_stop = stop
    best = entry
    for b in bars_after:
        hi, lo = b["h"], b["l"]
        if trail_dist > 0:
            if side == "BUY":
                best = max(best, hi)
                cur_stop = max(cur_stop, best - trail_dist)
            else:
                best = min(best, lo)
                cur_stop = min(cur_stop, best + trail_dist)
        if side == "BUY":
            hit_stop = lo <= cur_stop
            hit_tgt = hi >= target
        else:
            hit_stop = hi >= cur_stop
            hit_tgt = lo <= target
        if hit_stop and hit_tgt:
            pts = (cur_stop - entry) if side == "BUY" else (entry - cur_stop)
            return {"status": "STOP_HIT", "resolved_bar": b["bar_start"],
                    "exit_price": round(cur_stop, 4), "points": round(pts, 4),
                    "note": "bar spanned both target and stop; STOP assumed (pessimistic)"}
        if hit_stop:
            pts = (cur_stop - entry) if side == "BUY" else (entry - cur_stop)
            return {"status": "STOP_HIT", "resolved_bar": b["bar_start"],
                    "exit_price": round(cur_stop, 4), "points": round(pts, 4)}
        if hit_tgt:
            pts = (target - entry) if side == "BUY" else (entry - target)
            return {"status": "TARGET_HIT", "resolved_bar": b["bar_start"],
                    "exit_price": round(target, 4), "points": round(pts, 4)}
    return {"status": "TRIGGERED", "resolved_bar": None, "exit_price": None, "points": 0.0}


def _first_breakout_idx(bars_after: list, level: float, side: str) -> Optional[int]:
    for i, b in enumerate(bars_after):
        if side == "BUY" and b["h"] > level:
            return i
        if side == "SELL" and b["l"] < level:
            return i
    return None


def _setup(spike: dict, bars_after: list, side: str, rr: float,
           stop_frac: float = 1.0, trail: bool = False) -> dict:
    """`stop_frac`: the stop distance as a fraction of the spike candle's
    high-low range. 1.0 (default) = stop at the opposite extreme (the original
    spec). 0.5 = stop halfway between entry and that extreme -- a TIGHTER stop.
    `target` is always rr x the ACTUAL (possibly tighter) stop distance, so a
    tighter stop also pulls the target in. `trail=True` trails the stop the
    same distance behind the best price after entry."""
    h, l = spike["h"], spike["l"]
    rng = h - l
    sd = max(0.0, stop_frac) * rng            # stop distance in points
    if side == "BUY":
        entry = h
        stop = entry - sd
        target = entry + rr * sd
    else:
        entry = l
        stop = entry + sd
        target = entry - rr * sd
    risk = abs(entry - stop)
    reward = abs(target - entry)
    d = {"side": side, "entry": round(entry, 4), "stop_loss": round(stop, 4),
         "target": round(target, 4), "risk_points": round(risk, 4),
         "reward_points": round(reward, 4), "trail": bool(trail),
         "rr": round(reward / risk, 2) if risk else None}
    bidx = _first_breakout_idx(bars_after, entry, side)
    if bidx is None:
        d["outcome"] = {"status": "PENDING", "resolved_bar": None, "exit_price": None, "points": 0.0}
        d["breakout_bar"] = None
    else:
        d["breakout_bar"] = bars_after[bidx]["bar_start"]
        d["outcome"] = _walk_outcome(bars_after[bidx:], entry, stop, target, side,
                                     trail_dist=sd if trail else 0.0)
    return d


_FILTERS = ("none", "candle_dir", "strong_body")
_PATTERNS = ("spike", "sideways_spike", "hammer")


def _is_hammer(b: dict, *, wick_body_mult: float = 2.0,
               opp_wick_frac: float = 0.30, min_body_frac: float = 0.05) -> str:
    """Classify a candle as a hammer / shooting-star reversal.

    "BUY"  -> bullish hammer: long LOWER wick (>= wick_body_mult x body),
              little/no upper wick, body near the top.
    "SELL" -> shooting star: long UPPER wick, little/no lower wick.
    ""     -> neither / ambiguous / doji.
    """
    o, c, h, l = b.get("o"), b.get("c"), b["h"], b["l"]
    rng = h - l
    if o is None or c is None or rng <= 0:
        return ""
    body = abs(c - o)
    if body < min_body_frac * rng:            # a doji is not a hammer
        return ""
    upper = h - max(o, c)
    lower = min(o, c) - l
    if lower >= wick_body_mult * body and upper <= opp_wick_frac * rng:
        return "BUY"
    if upper >= wick_body_mult * body and lower <= opp_wick_frac * rng:
        return "SELL"
    return ""


def _is_post_consolidation(clean: list, idx: int, avg_range: float, *,
                           lookback: int = 5, span_x: float = 1.5) -> bool:
    """True when the `lookback` bars immediately before `idx` were coiled in a
    tight range -- total high-low span <= span_x x the session's average bar
    range -- i.e. the trigger candle is breaking OUT of a sideways stretch."""
    if idx < lookback or avg_range <= 0:
        return False
    window = clean[idx - lookback:idx]
    span = max(x["h"] for x in window) - min(x["l"] for x in window)
    return span <= span_x * avg_range


def _sides_for(b: dict, sig_filter: str) -> tuple:
    """Which of BUY / SELL to build for this spike candle under `sig_filter`.
    none -> both (original). candle_dir -> only the side matching the spike
    candle's own direction (bullish close>open -> BUY, bearish -> SELL,
    doji -> neither). strong_body -> candle_dir AND |close-open| >= 0.5*range."""
    if sig_filter not in _FILTERS or sig_filter == "none":
        return ("BUY", "SELL")
    o, c = b["o"], b["c"]
    rng = b["h"] - b["l"]
    if o is None or c is None or c == o:
        return ()
    body = abs(c - o)
    if sig_filter == "strong_body" and (rng <= 0 or body < 0.5 * rng):
        return ()
    return ("BUY",) if c > o else ("SELL",)


def smart_money_setups(bars: list, *, volume_mult: float = 2.0, rr: float = 3.0,
                       stop_frac: float = 1.0, trail: bool = False,
                       sig_filter: str = "none", pattern: str = "spike",
                       consol_lookback: int = 5, consol_span_x: float = 1.5) -> dict:
    """Detect trigger candles and build breakout setups for each, with a
    same-session forward-walked outcome.

    `pattern`:
      "spike"          -- bar volume >= volume_mult x session avg (original).
      "sideways_spike" -- a volume spike whose prior `consol_lookback` bars were
                          coiled within `consol_span_x` x the avg bar range
                          (breakout OUT of a sideways stretch).
      "hammer"         -- a hammer / shooting-star reversal candle; the wick
                          sets the side (long lower wick -> BUY above its high,
                          long upper wick -> SELL below its low). No volume
                          filter -- the candle shape IS the trigger.

    `stop_frac` < 1.0 = a tighter stop; `trail=True` trails the stop that same
    distance behind the best price; `sig_filter` in {none, candle_dir,
    strong_body} further restricts the side (ignored for "hammer", whose side
    is already fixed by the wick)."""
    if pattern not in _PATTERNS:
        pattern = "spike"
    clean = _clean(bars)
    vols = [b["v"] for b in clean if b["v"] > 0]
    if len(clean) < 3 or (pattern != "hammer" and len(vols) < 3):
        return {"status": "NO_DATA", "reason": "need >=3 bars with volume",
                "setups": []}
    avg_v = mean(vols) if vols else 0.0
    if pattern != "hammer" and avg_v <= 0:
        return {"status": "NO_DATA", "reason": "zero average volume", "setups": []}
    avg_range = mean(b["h"] - b["l"] for b in clean) if clean else 0.0

    setups = []
    for idx, b in enumerate(clean):
        if pattern == "hammer":
            hs = _is_hammer(b)
            if not hs:
                continue
            sides = (hs,)
        else:
            if b["v"] < volume_mult * avg_v:
                continue
            if pattern == "sideways_spike" and not _is_post_consolidation(
                    clean, idx, avg_range, lookback=consol_lookback, span_x=consol_span_x):
                continue
            sides = _sides_for(b, sig_filter)
            if not sides:
                continue
        after = clean[idx + 1:]
        row = {
            "candle": {"bar_start": b["bar_start"], "o": b["o"], "h": b["h"],
                       "l": b["l"], "c": b["c"], "v": b["v"]},
            "volume_x_avg": round(b["v"] / avg_v, 2) if avg_v > 0 else None,
            "range_points": round(b["h"] - b["l"], 4),
            "pattern": pattern,
        }
        if "BUY" in sides:
            row["buy"] = _setup(b, after, "BUY", rr, stop_frac, trail)
        if "SELL" in sides:
            row["sell"] = _setup(b, after, "SELL", rr, stop_frac, trail)
        setups.append(row)
    return {
        "status": "OK",
        "method": "OHLCV_BARS",
        "note": ("trigger candle per `pattern`; breakout / target / stop "
                 "evaluated at ~5m bar granularity, not tick -- a bar spanning "
                 "both target and stop is scored STOP_HIT"),
        "session_avg_volume": round(avg_v, 2),
        "session_avg_range": round(avg_range, 4),
        "volume_mult": volume_mult, "rr": rr, "stop_frac": stop_frac,
        "trail": bool(trail), "sig_filter": sig_filter, "pattern": pattern,
        "spike_count": len(setups),
        "setups": setups,
    }
