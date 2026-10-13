"""Offline analysis of the Renko tick shadow stream (read-only; never a gate).

Question (pre-declared, SMART_BOS_RESEARCH_REPORT.md): after the Renko run first reaches 3 bricks
(event), does price continue in that direction more than after short runs (baseline), over the next
H minutes? No trades are needed: forward returns come from market_history 1m bars. Entry = OPEN of
the bar following the observed bar (what a trader could actually get), exit = CLOSE of the bar H
minutes later, same IST session only. Returns are signed by Renko direction, in bps, gross of costs
(compare against ~2-3 bps index-futures round trip).
Sample rule: no conclusion below MIN_N events per group; baseline rows are thinned to one per H
minutes per symbol to limit overlapping horizons.
"""
from __future__ import annotations

import random
import sqlite3
import statistics
from datetime import datetime, timezone

MIN_N = 300
EVENT_RUN = 3


def _epoch(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())


def load_ticks(path: str) -> list[dict]:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute("SELECT * FROM renko_shadow_ticks ORDER BY symbol, last_bar_ts")]
    finally:
        c.close()


def load_bars(hist_path: str, symbol: str, kind: str, t0: int, t1: int) -> dict[int, tuple]:
    """{bar_start_epoch: (open, close)} for 1m bars in [t0, t1] (indexed range query)."""
    lo = datetime.fromtimestamp(t0 - 120, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    hi = datetime.fromtimestamp(t1 + 120, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    c = sqlite3.connect(f"file:{hist_path}?mode=ro", uri=True, timeout=5)
    try:
        rows = c.execute("SELECT bar_start, o, c FROM market_candles WHERE symbol=? AND kind=? AND tf='1m' "
                         "AND bar_start>=? AND bar_start<=?", (symbol, kind, lo, hi)).fetchall()
    finally:
        c.close()
    return {_epoch(r[0]): (r[1], r[2]) for r in rows if r[1] is not None and r[2] is not None}


def forward_bps(tick: dict, bars: dict, horizon_min: int, same_session_gap_min: int = 5):
    """Signed forward return in bps from the next bar's open to the close `horizon_min` bars later; None if unavailable."""
    d = tick["renko_dir"]
    if not d:
        return None
    t_obs = _epoch(tick["last_bar_ts"])
    entry = bars.get(t_obs + 60)
    exit_ = bars.get(t_obs + 60 * horizon_min)
    if not entry or not exit_:
        return None
    # reject horizons that straddle a session gap (a bar every minute is required between entry and exit)
    gaps = sum(1 for k in range(1, horizon_min) if (t_obs + 60 * k) not in bars)
    if gaps > same_session_gap_min:
        return None
    return d * (exit_[1] / entry[0] - 1.0) * 1e4


def boot_diff(a: list, b: list, n: int = 2000, seed: int = 5):
    rnd = random.Random(seed)
    diffs = sorted(statistics.fmean(rnd.choices(a, k=len(a))) - statistics.fmean(rnd.choices(b, k=len(b))) for _ in range(n))
    return diffs[int(.025 * n)], diffs[int(.975 * n) - 1]


def analyse(ticks: list[dict], bars_by_symbol: dict, horizons=(15, 30, 60)) -> dict:
    out = {}
    for H in horizons:
        ev, base = [], []
        last_base_t: dict = {}
        prev: dict = {}
        for t in ticks:                                    # ticks sorted by (symbol, last_bar_ts)
            s = t["symbol"]
            bars = bars_by_symbol.get(s) or {}
            was = prev.get(s)
            prev[s] = t
            f = forward_bps(t, bars, H)
            if f is None:
                continue
            is_event = t["renko_run"] == EVENT_RUN and (was is None or was["renko_run"] < EVENT_RUN
                                                         or was["renko_dir"] != t["renko_dir"])
            if is_event:
                ev.append(f)
            elif t["renko_run"] in (1, 2):
                tt = _epoch(t["last_bar_ts"])
                if tt - last_base_t.get(s, 0) >= 60 * H:
                    base.append(f); last_base_t[s] = tt
        row = {"events": len(ev), "baseline": len(base)}
        if ev:
            row["event_mean_bps"] = statistics.fmean(ev)
        if base:
            row["baseline_mean_bps"] = statistics.fmean(base)
        if min(len(ev), len(base)) < MIN_N:
            row["verdict"] = f"INSUFFICIENT SAMPLE (need >= {MIN_N} per group)"
        else:
            lo, hi = boot_diff(ev, base)
            row["diff_ci95_bps"] = (lo, hi)
            row["verdict"] = "events continue more" if lo > 0 else "events continue less" if hi < 0 else "no demonstrated difference"
        out[H] = row
    return out
