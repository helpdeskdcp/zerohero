"""
Research-only: builds and backtests the Stage-3.9 "Absorption" detector
(ORDERFLOW_ENGINE_V2_SPEC.md section 3.9 -- previously only a written spec,
"buildable (weak)", never actually run) against REAL captured L2 snapquote
data (data/l2_capture.db, snapquote_ticks). This is the only one of three
candidate hypotheses this session identified as genuinely unexplored --
H7_TRAP fade was already tested and REJECTED (ORDERFLOW_STAGE8_H7_FADE.md),
and a related MTF-confirmation mechanic was already NOT VALIDATED
(IMBALANCE_NEXT_CANDLE_1R3_MTF_OI.md).

SCOPE / HONEST LIMITATION: only 7-8 real captured trading days exist per
symbol (2026-09-09 .. 2026-09-18) -- far thinner than any other backtest
this session. This is a first, exploratory read, not a validated result
in either direction.

delta_snap proxy: Angel's SnapQuote carries tot_buy_qty/tot_sell_qty
(cumulative aggregate order quantities on each side of the book at that
instant) but NOT per-trade aggressor side -- there is no genuine tick
delta in this data. `delta_snap_bar` here = the CHANGE in
(tot_buy_qty - tot_sell_qty) across the bar, which is a directional PROXY
for buy/sell pressure, not real trade delta. Labeled honestly as a proxy,
same convention as IMBALANCE_NEXT_CANDLE_1R3_MTF_OI.md's passive_book proxy.

Read-only research. No production code touched, no signal, no orders, no
live wiring -- same discipline as every other orderflow stage this session.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).parents[1]))

L2_DB = Path(__file__).parents[1] / "data" / "l2_capture.db"

# Spec defaults (ORDERFLOW_ENGINE_V2_SPEC.md 3.9) -- not tuned on this data,
# taken as written.
WINDOW_W = 4            # bars per absorption window
ALPHA = 0.25            # |C_end - C_start| <= alpha * ATR
BETA = 0.6              # range(W) <= beta * ATR
GAMMA = 0.35            # |sum(delta_snap)| / sum(volume) >= gamma
KAPPA = 2               # >= kappa bars touching the band
BAND_ATR_MULT = 0.15    # band half-width
P90_LOOKBACK = 20       # trailing windows for the volume P90 reference

STOP_ATR_MULT = 1.0     # stop beyond the band
TARGET_R = 2.0          # target in R (band width proxy = ATR)
MAX_HOLD_BARS = 12      # 1 hour at 5m


def _load_ticks(symbol: str) -> list[dict]:
    con = sqlite3.connect(L2_DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT exch_ts_ms, received_ts, ltp, volume, tot_buy_qty, tot_sell_qty "
        "FROM snapquote_ticks WHERE symbol=? AND ltp IS NOT NULL "
        "ORDER BY COALESCE(exch_ts_ms, 0), received_ts", (symbol,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def _epoch(r: dict) -> float:
    if r.get("exch_ts_ms"):
        return r["exch_ts_ms"] / 1000.0
    return datetime.fromisoformat(r["received_ts"].replace("Z", "+00:00")).timestamp()


def _resample_5m(ticks: list[dict]) -> list[dict]:
    """OHLC from ltp, real per-bar volume from the cumulative session
    `volume` field (delta, never negative -- a session rollover resets it,
    handled by clamping to 0), delta_snap_bar = the buy-sell proxy change
    across the bar."""
    bars: dict[int, dict] = {}
    order = []
    last_vol = None
    last_imb = None
    for r in ticks:
        if r["ltp"] is None or r["ltp"] <= 0:
            continue
        ep = _epoch(r)
        bucket = int(ep // 300) * 300
        b = bars.get(bucket)
        vol_cum = r.get("volume")
        imb = (r.get("tot_buy_qty") or 0.0) - (r.get("tot_sell_qty") or 0.0)
        vol_delta = 0.0
        if last_vol is not None and vol_cum is not None and vol_cum >= last_vol:
            vol_delta = vol_cum - last_vol
        if vol_cum is not None:
            last_vol = vol_cum
        imb_delta = 0.0
        if last_imb is not None:
            imb_delta = imb - last_imb
        last_imb = imb
        if b is None:
            b = {"t": bucket, "o": r["ltp"], "h": r["ltp"], "l": r["ltp"], "c": r["ltp"],
                "v": 0.0, "delta_snap": 0.0}
            bars[bucket] = b
            order.append(bucket)
        b["h"] = max(b["h"], r["ltp"])
        b["l"] = min(b["l"], r["ltp"])
        b["c"] = r["ltp"]
        b["v"] += vol_delta
        b["delta_snap"] += imb_delta
    return [bars[k] for k in sorted(order)]


def _atr(bars: list[dict], i: int, period: int = 14) -> float | None:
    if i < period:
        return None
    trs = []
    for j in range(i - period + 1, i + 1):
        h, l = bars[j]["h"], bars[j]["l"]
        pc = bars[j - 1]["c"] if j > 0 else bars[j]["o"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return mean(trs) if trs else None


def detect_absorption_events(bars: list[dict]) -> list[dict]:
    events = []
    vol_hist = []
    for i in range(WINDOW_W, len(bars)):
        window = bars[i - WINDOW_W:i]
        atr = _atr(bars, i - 1)
        if not atr or atr <= 0:
            continue
        vsum = sum(b["v"] for b in window)
        vol_hist.append(vsum)
        if len(vol_hist) < P90_LOOKBACK:
            continue
        ref = sorted(vol_hist[-P90_LOOKBACK:])
        p90 = ref[int(0.9 * (len(ref) - 1))]
        if vsum < p90 or p90 <= 0:
            continue  # S1

        c_start, c_end = window[0]["o"], window[-1]["c"]
        rng = max(b["h"] for b in window) - min(b["l"] for b in window)
        if abs(c_end - c_start) > ALPHA * atr or rng > BETA * atr:
            continue  # S2

        dsum = sum(b["delta_snap"] for b in window)
        if vsum <= 0 or abs(dsum) / vsum < GAMMA:
            continue  # S3

        band_lo, band_hi = c_end - BAND_ATR_MULT * atr, c_end + BAND_ATR_MULT * atr
        touches = sum(1 for b in window if b["l"] <= band_hi and b["h"] >= band_lo)
        if touches < KAPPA:
            continue  # S4

        direction = "BULLISH" if dsum < 0 else "BEARISH"  # S5: opposite of pressure sign
        events.append({"bar_index": i - 1, "t": window[-1]["t"], "direction": direction,
                      "atr": atr, "band_lo": band_lo, "band_hi": band_hi, "entry": c_end,
                      "vsum": vsum, "dsum": dsum})
    return events


def _simulate(direction: str, entry: float, atr: float, future_bars: list[dict]) -> dict:
    sign = 1 if direction == "BULLISH" else -1
    stop = entry - sign * STOP_ATR_MULT * atr
    target = entry + sign * TARGET_R * STOP_ATR_MULT * atr
    for k, bar in enumerate(future_bars[:MAX_HOLD_BARS]):
        path = (bar["o"], bar["l"], bar["h"], bar["c"]) if sign == 1 else (bar["o"], bar["h"], bar["l"], bar["c"])
        for px in path:
            if (sign == 1 and px <= stop) or (sign == -1 and px >= stop):
                return {"exit_reason": "STOP", "points": round(sign * (stop - entry), 4)}
            if (sign == 1 and px >= target) or (sign == -1 and px <= target):
                return {"exit_reason": "TARGET", "points": round(sign * (target - entry), 4)}
    if not future_bars:
        return None
    last_c = future_bars[min(len(future_bars), MAX_HOLD_BARS) - 1]["c"]
    return {"exit_reason": "TIME", "points": round(sign * (last_c - entry), 4)}


def _stats(rows):
    pts = [r["points"] for r in rows]
    if not pts:
        return {"n": 0}
    win = [p for p in pts if p > 0]
    loss = [p for p in pts if p < 0]
    pf = round(sum(win) / abs(sum(loss)), 3) if loss else None
    reasons = {}
    for r in rows:
        reasons[r["exit_reason"]] = reasons.get(r["exit_reason"], 0) + 1
    return {"n": len(pts), "win_rate_pct": round(100.0 * len(win) / len(pts), 1),
            "expectancy": round(mean(pts), 4), "profit_factor": pf, "exit_reasons": reasons}


def run_symbol(symbol: str) -> dict:
    ticks = _load_ticks(symbol)
    bars = _resample_5m(ticks)
    events = detect_absorption_events(bars)
    resolved = []
    for e in events:
        outcome = _simulate(e["direction"], e["entry"], e["atr"], bars[e["bar_index"] + 1:])
        if outcome is None:
            continue
        resolved.append({**e, **outcome})

    n_days = len({datetime.fromtimestamp(b["t"]).date().isoformat() for b in bars})
    split = int(len(resolved) * 0.6)
    train, oos = resolved[:split], resolved[split:]

    return {"symbol": symbol, "n_bars_5m": len(bars), "n_real_days": n_days,
           "n_events": len(resolved), "overall": _stats(resolved),
           "chronological_train_60pct": _stats(train), "chronological_oos_40pct": _stats(oos)}


def main():
    report = {}
    for sym in ("NATURALGAS", "CRUDEOIL", "NIFTY"):
        print(f"{sym}...")
        report[sym] = run_symbol(sym)
    print(json.dumps(report, indent=2))
    out = Path(__file__).parents[1] / "data" / "research" / "orderflow" / "absorption_backtest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nsaved to {out}")


if __name__ == "__main__":
    main()
