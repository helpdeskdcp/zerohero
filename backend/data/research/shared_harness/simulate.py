"""Standardized trade simulation. Consolidates 3 independently-written
copies of the SL/TP/EOD/max-bars walk-forward loop (reversal_sweep,
false_breakout_reversal, cpr_orb), the daily trade-management rules most of
today's strategy specs asked for verbatim, and the overlap-safe scan-index
rule -- the exact bug found and fixed in reversal_sweep_2026-09 (three
overlapping break-bars all confirming at the same bar were counted as 3
separate trades because the scan loop only ever did `i += 1`, never
skipping past a fired signal's own confirmation bar)."""
from __future__ import annotations

import pandas as pd

_LONG_DIRECTIONS = {"LONG", "CE", "BUY"}


def is_long(direction: str) -> bool:
    return direction.upper() in _LONG_DIRECTIONS


def simulate_trade(bars: pd.DataFrame, entry_idx: int, direction: str, entry: float,
                   stop: float, target: float, *, tag: str = "", max_bars: int = 60) -> dict:
    """direction in {"LONG","CE","BUY"} profits when price rises;
    {"SHORT","PE","SELL"} profits when price falls. Walks forward from
    entry_idx+1 bar by bar until stop, target, session-end (EOD), or
    max_bars, whichever comes first -- never looks further ahead than that."""
    long_ = is_long(direction)
    highs, lows, closes = bars["high"].values, bars["low"].values, bars["close"].values
    dates = bars.index.date
    entry_date = dates[entry_idx]
    n = len(bars)
    base = {"direction": direction, "entry": entry, "stop": stop, "target": target,
            "tag": tag, "entry_ts": bars.index[entry_idx]}
    for k in range(1, max_bars + 1):
        j = entry_idx + k
        if j >= n or dates[j] != entry_date:
            j_final = min(j - 1, n - 1)
            return {**base, "exit": closes[j_final], "exit_ts": bars.index[j_final], "reason": "EOD"}
        if long_:
            if lows[j] <= stop:
                return {**base, "exit": stop, "exit_ts": bars.index[j], "reason": "SL"}
            if highs[j] >= target:
                return {**base, "exit": target, "exit_ts": bars.index[j], "reason": "TP"}
        else:
            if highs[j] >= stop:
                return {**base, "exit": stop, "exit_ts": bars.index[j], "reason": "SL"}
            if lows[j] <= target:
                return {**base, "exit": target, "exit_ts": bars.index[j], "reason": "TP"}
    j_final = min(entry_idx + max_bars, n - 1)
    return {**base, "exit": closes[j_final], "exit_ts": bars.index[j_final], "reason": "MAXBARS"}


def pnl_points(trade: dict) -> float:
    return (trade["exit"] - trade["entry"]) if is_long(trade["direction"]) else (trade["entry"] - trade["exit"])


def next_scan_index(i: int, fired: bool, fired_at: int | None) -> int:
    """The exact index-advance rule missing in reversal_sweep_2026-09's
    original bug: on a fire, skip PAST the confirmation bar (fired_at + 1),
    not just i+1 -- otherwise the next 1-3 bars of the same continuing move
    can re-fire as "new" signals that resolve to the same real event.
    Usage: at the end of a scan while-loop, `i = next_scan_index(i, fired, fired_at)`."""
    return (fired_at + 1) if (fired and fired_at is not None) else (i + 1)


def apply_daily_limits(trades: list[dict], *, max_per_day: int = 3, max_consecutive_sl: int = 2) -> list[dict]:
    """Chronological, per-day: stop taking NEW trades once either cap is
    hit; a trade already past this point in the day is simply never
    included (never retroactively removes an already-resolved trade)."""
    by_date: dict = {}
    for t in sorted(trades, key=lambda x: x["entry_ts"]):
        by_date.setdefault(t["entry_ts"].date(), []).append(t)
    kept = []
    for day_trades in by_date.values():
        count, consec_sl = 0, 0
        for t in day_trades:
            if count >= max_per_day or consec_sl >= max_consecutive_sl:
                break
            kept.append(t)
            count += 1
            consec_sl = consec_sl + 1 if t["reason"] == "SL" else 0
    return kept
