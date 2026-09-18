"""
Order-Flow Stage-11 research script (see ORDERFLOW_STAGE11_TIME_EXIT_EDGE.md
for the full writeup and honest caveats).

Two things, both on real captured NATGAS 5m data via app.market_hub:

1. Forward-return significance test: does a volume-spike candle's OWN
   close>open direction carry real forward-predictive information,
   completely separate from any stop/target trade structure? (z-score,
   spike bars vs all other bars, several horizons, TRAIN/OOS split.)

2. A short time-exit backtest (hold N bars, market exit, modest tail stop)
   swept over hold_bars x stop_mult, reported on TRAIN and OOS separately,
   plus a BUY/SELL side breakdown (the aggregate is robust across splits;
   which SIDE carries it flips between TRAIN and OOS -- see the doc).

Read-only research. No production code touched, no signal, no orders, no
live wiring.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from statistics import mean, stdev

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import market_hub  # noqa: E402

TRAIN = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07",
        "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-14"]
OOS = ["2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]
HORIZONS = (1, 3, 5, 10, 20)
HOLD_GRID = (1, 2, 3, 5)
STOP_GRID = (0.5, 1.0, 1.5)


def _collect_forward_returns(symbol: str, sessions: list[str], horizons=HORIZONS):
    spike_fwd = {h: [] for h in horizons}
    base_fwd = {h: [] for h in horizons}
    for session in sessions:
        bars = market_hub.session_bars(symbol, session, tf="5m")
        if not bars:
            continue
        n = len(bars)
        avg_v = mean(b.get("v", 0) or 0 for b in bars) or 1.0
        for i in range(n):
            c0 = bars[i]["c"]
            is_spike = (bars[i].get("v") or 0) >= 2.0 * avg_v
            direction = 1 if bars[i]["c"] > bars[i]["o"] else (-1 if bars[i]["c"] < bars[i]["o"] else 0)
            if direction == 0:
                continue
            for h in horizons:
                if i + h >= n:
                    continue
                fwd = direction * (bars[i + h]["c"] - c0)
                (spike_fwd if is_spike else base_fwd)[h].append(fwd)
    return spike_fwd, base_fwd


def significance_report(symbol: str):
    for label, sessions in (("TRAIN", TRAIN), ("OOS", OOS), ("FULL", TRAIN + OOS)):
        sf, bf = _collect_forward_returns(symbol, sessions)
        print(f"=== {symbol} {label}: spike vs baseline forward drift ===")
        for h in HORIZONS:
            s, b = sf[h], bf[h]
            if len(s) < 2 or len(b) < 2:
                continue
            d = mean(s) - mean(b)
            se = math.sqrt(stdev(s) ** 2 / len(s) + stdev(b) ** 2 / len(b))
            z = d / se if se > 0 else 0
            print(f"  h={h:>3} spike_mean={mean(s):>8.4f} base_mean={mean(b):>8.4f} "
                 f"diff={d:>8.4f} z={z:>6.2f} n_spike={len(s)}")


def _time_exit_trades(symbol: str, sessions: list[str], hold_bars: int, stop_mult: float):
    rows = []
    for session in sessions:
        bars = market_hub.session_bars(symbol, session, tf="5m")
        if not bars:
            continue
        n = len(bars)
        avg_v = mean(b.get("v", 0) or 0 for b in bars) or 1.0
        for i in range(n):
            b0 = bars[i]
            if (b0.get("v") or 0) < 2.0 * avg_v:
                continue
            direction = 1 if b0["c"] > b0["o"] else (-1 if b0["c"] < b0["o"] else 0)
            if direction == 0:
                continue
            entry = b0["c"]
            rng = b0["h"] - b0["l"]
            stop = entry - direction * stop_mult * rng
            exit_price = None
            for k in range(i + 1, min(i + 1 + hold_bars, n)):
                bk = bars[k]
                path = (bk["o"], bk["l"], bk["h"], bk["c"]) if direction == 1 else (bk["o"], bk["h"], bk["l"], bk["c"])
                stopped = False
                for px in path:
                    if (direction == 1 and px <= stop) or (direction == -1 and px >= stop):
                        exit_price = stop
                        stopped = True
                        break
                if stopped:
                    break
            if exit_price is None:
                exit_price = bars[min(i + hold_bars, n - 1)]["c"]
            points = direction * (exit_price - entry)
            rows.append({"side": "BUY" if direction == 1 else "SELL", "points": points, "session": session})
    return rows


def _stats(rows):
    pts = [r["points"] if isinstance(r, dict) else r for r in rows]
    if not pts:
        return {"n": 0}
    win = [p for p in pts if p > 0]
    loss = [p for p in pts if p < 0]
    pf = round(sum(win) / abs(sum(loss)), 3) if loss else None
    return {"n": len(pts), "win_pct": round(100 * len(win) / len(pts), 1),
            "expectancy": round(mean(pts), 4), "profit_factor": pf, "net": round(sum(pts), 2)}


def time_exit_sweep(symbol: str):
    print(f"\n=== {symbol}: time-exit sweep (hold_bars x stop_mult), TRAIN vs OOS ===")
    for hold in HOLD_GRID:
        for stop in STOP_GRID:
            tr = _stats(_time_exit_trades(symbol, TRAIN, hold, stop))
            oo = _stats(_time_exit_trades(symbol, OOS, hold, stop))
            print(f"  hold={hold} stop={stop}  TRAIN={tr}  OOS={oo}")


def side_breakdown(symbol: str, configs=((3, 1.0), (3, 0.5), (2, 0.5))):
    print(f"\n=== {symbol}: BUY/SELL breakdown for top candidates ===")
    for hold, stop in configs:
        print(f"--- hold={hold} stop={stop} ---")
        for label, sessions in (("TRAIN", TRAIN), ("OOS", OOS)):
            rows = _time_exit_trades(symbol, sessions, hold, stop)
            buy = [r for r in rows if r["side"] == "BUY"]
            sell = [r for r in rows if r["side"] == "SELL"]
            print(f"  {label}: BUY={_stats(buy)}  SELL={_stats(sell)}")


if __name__ == "__main__":
    significance_report("NATURALGAS")
    time_exit_sweep("NATURALGAS")
    side_breakdown("NATURALGAS")
