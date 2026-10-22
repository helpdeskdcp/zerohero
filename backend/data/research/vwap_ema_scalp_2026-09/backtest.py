"""
VWAP + 9EMA pullback-rejection option scalp -- backtest before any
"production" code, matching this project's standing discipline (never write
live/broker-execution code for an unvalidated rule).

RULE (as given, restated exactly):
  Context (5-min): NIFTY spot vs intraday VWAP (session-reset daily).
  Execution (1-min): 9 EMA.
  CE BUY: 5m spot > 5m VWAP, AND 1m price pulls back to test 9EMA/VWAP
    (candle LOW touches/dips to EMA9 or VWAP), AND that 1m candle is a
    Bullish Rejection (Hammer OR Bullish Engulfing) closing ABOVE 9EMA.
  PE BUY: mirror (5m spot < 5m VWAP, rally tests 9EMA/VWAP from above,
    Bearish Rejection (Shooting Star OR Bearish Engulfing) closing BELOW 9EMA).
  Entries only 09:15-10:30 and 14:15-15:15 IST.
  SL 6pts / TP 12pts on the OPTION PREMIUM (1:2 R:R).
  Max 3 trades/day OR stop after 2 consecutive losses (whichever first).
  8-minute timeout exit at market if neither SL nor TP hit.

MY DISCLOSED INTERPRETATION CHOICES:
  - "or below signal candle low" (the SL's alt clause) is NOT implemented --
    it's an index-price unit, the primary SL is stated in option-premium
    points, mixing the two is ambiguous. Fixed 6pt premium SL only.
  - "Tests 9EMA or VWAP" = candle's low (CE) / high (PE) reaches to within
    the EMA/VWAP level (crosses it intrabar) while the candle still closes
    on the correct side.
  - Standard Hammer/Shooting-Star/Engulfing definitions (documented in code).

DATA:
  Phase A: real NIFTY 1-min spot, 2015-2025 (10 years) -- pattern-quality
    check on a large sample, index POINTS only (not real option economics).
  Phase B: real captured NIFTY option premium, ~2026-09-02 to 09-25 (~24
    days, the entire real window that exists) -- the real, disclosed test,
    with the ACTUAL 6/12-point premium SL/TP rule, thin sample flagged up
    front exactly like the reversal-sweep study.
"""
from __future__ import annotations

import sqlite3
import sys

import numpy as np
import pandas as pd

NIFTY_CSV = ("/root/zerohero/backend/data/historical/kaggle/"
             "mavrick136__10-years-of-nifty-50-1-minute-data-20152025/NIFTY_1MIN_2015-2025.csv")
DB = "/root/zerohero/backend/data/market_history.db"

ENTRY_WINDOWS = [((9, 15), (10, 30)), ((14, 15), (15, 15))]
MAX_TRADES_PER_DAY = 3
MAX_CONSEC_LOSSES = 2
TIMEOUT_MIN = 8
STRIKE_STEP = 50


def load_nifty_1m() -> pd.DataFrame:
    df = pd.read_csv(NIFTY_CSV, parse_dates=["datetime"])
    df = df.set_index("datetime").sort_index()
    return df[~df.index.duplicated(keep="first")][["open", "high", "low", "close"]]


def session_vwap(m1: pd.DataFrame) -> pd.Series:
    """Real intraday VWAP needs volume; the Kaggle 1-min file has none we
    trust (all zeros in the sample rows seen). Use the TYPICAL PRICE running
    mean per session as the closest available real-data VWAP proxy, reset
    every trading day -- disclosed, not a substitute for a true volume-VWAP."""
    tp = (m1["high"] + m1["low"] + m1["close"]) / 3.0
    date = m1.index.date
    cum_tp = tp.groupby(date).cumsum()
    count = pd.Series(1, index=m1.index).groupby(date).cumsum()
    return cum_tp / count


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def is_bullish_engulfing(o, h, l, c, po, ph, pl, pc) -> bool:
    return pc < po and c > o and o <= pc and c >= po


def is_bearish_engulfing(o, h, l, c, po, ph, pl, pc) -> bool:
    return pc > po and c < o and o >= pc and c <= po


def is_hammer(o, h, l, c) -> bool:
    body = abs(c - o)
    rng = h - l
    if rng <= 0:
        return False
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    return body > 0 and lower_wick >= 2 * body and upper_wick <= body * 0.5 and body / rng < 0.4


def is_shooting_star(o, h, l, c) -> bool:
    body = abs(c - o)
    rng = h - l
    if rng <= 0:
        return False
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    return body > 0 and upper_wick >= 2 * body and lower_wick <= body * 0.5 and body / rng < 0.4


def in_entry_window(ts: pd.Timestamp) -> bool:
    t = (ts.hour, ts.minute)
    return any(start <= t <= end for start, end in ENTRY_WINDOWS)


def build_signals(m1: pd.DataFrame) -> pd.DataFrame:
    """Adds vwap5 (5-min VWAP proxy, forward-filled onto 1-min index -- the
    5-min bar must have fully CLOSED before its VWAP value is visible to a
    1-min bar, i.e. shifted, no look-ahead), ema9 (1-min), and the
    CE/PE signal booleans."""
    df = m1.copy()
    df["ema9"] = ema(df["close"], 9)
    vwap1 = session_vwap(df)
    m5 = df["close"].resample("5min", label="left", closed="left").last().to_frame("close5")
    m5["vwap5"] = session_vwap(
        df.resample("5min", label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna())
    m5_shifted = m5.shift(1)  # only a CLOSED 5-min bar's own vwap is visible
    merged = pd.merge_asof(df.reset_index(), m5_shifted.reset_index(),
                           on="datetime", direction="backward")
    df["vwap5"] = merged["vwap5"].values
    df["spot5_above_vwap5"] = merged["close5"].values > merged["vwap5"].values
    df["vwap1"] = vwap1.values

    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    po = np.roll(o, 1); ph = np.roll(h, 1); pl = np.roll(l, 1); pc = np.roll(c, 1)
    ema9, vwap1v, vwap5_above = df["ema9"].values, df["vwap1"].values, df["spot5_above_vwap5"].values

    ce_signal = np.zeros(len(df), dtype=bool)
    pe_signal = np.zeros(len(df), dtype=bool)
    for i in range(1, len(df)):
        if not vwap5_above[i]:
            pass
        else:
            tested = (l[i] <= ema9[i]) or (l[i] <= vwap1v[i])
            rejection = (is_hammer(o[i], h[i], l[i], c[i])
                        or is_bullish_engulfing(o[i], h[i], l[i], c[i], po[i], ph[i], pl[i], pc[i]))
            if tested and rejection and c[i] > ema9[i]:
                ce_signal[i] = True
        if vwap5_above[i]:
            pass
        else:
            tested = (h[i] >= ema9[i]) or (h[i] >= vwap1v[i])
            rejection = (is_shooting_star(o[i], h[i], l[i], c[i])
                        or is_bearish_engulfing(o[i], h[i], l[i], c[i], po[i], ph[i], pl[i], pc[i]))
            if tested and rejection and c[i] < ema9[i]:
                pe_signal[i] = True

    df["ce_signal"] = ce_signal
    df["pe_signal"] = pe_signal
    df["in_window"] = [in_entry_window(ts) for ts in df.index]
    return df


def simulate_index_points(sig: pd.DataFrame, sl_pts: float, tp_pts: float, timeout_min: int) -> dict:
    """Exploratory ONLY: applies the rule's SL/TP as INDEX points directly
    (not option premium) purely to get a large-sample read on whether the
    entry LOGIC has any real signal quality, independent of the thin real
    option-premium sample. NOT a substitute for the real premium test --
    an index point is not an option point (no delta conversion applied,
    stated as index pts explicitly so it's never confused with the real
    6/12 premium-point rule)."""
    sig = sig.copy()
    sig["date"] = sig.index.date
    closes, highs, lows = sig["close"].values, sig["high"].values, sig["low"].values
    idx = sig.index
    n = len(sig)
    trades = []
    for date, day_idx in sig.groupby("date").indices.items():
        trades_today, consec_losses = 0, 0
        for i in day_idx:
            if not (sig["in_window"].values[i] and (sig["ce_signal"].values[i] or sig["pe_signal"].values[i])):
                continue
            if trades_today >= MAX_TRADES_PER_DAY or consec_losses >= MAX_CONSEC_LOSSES:
                break
            direction = "CE" if sig["ce_signal"].values[i] else "PE"
            entry = closes[i]
            sl = entry - sl_pts if direction == "CE" else entry + sl_pts
            tp = entry + tp_pts if direction == "CE" else entry - tp_pts
            timeout_ts = idx[i] + pd.Timedelta(minutes=timeout_min)
            exit_price, reason = None, None
            for j in range(i + 1, n):
                if idx[j].date() != date:
                    exit_price, reason = closes[j - 1], "EOD"
                    break
                if idx[j] > timeout_ts:
                    exit_price, reason = closes[j], "TIMEOUT"
                    break
                if direction == "CE":
                    if lows[j] <= sl:
                        exit_price, reason = sl, "SL"
                        break
                    if highs[j] >= tp:
                        exit_price, reason = tp, "TP"
                        break
                else:
                    if highs[j] >= sl:
                        exit_price, reason = sl, "SL"
                        break
                    if lows[j] <= tp:
                        exit_price, reason = tp, "TP"
                        break
            if exit_price is None:
                continue
            pnl = (exit_price - entry) if direction == "CE" else (entry - exit_price)
            trades.append({"direction": direction, "pnl": pnl, "reason": reason})
            trades_today += 1
            consec_losses = consec_losses + 1 if pnl <= 0 else 0

    if not trades:
        return {"n": 0, "verdict": "INSUFFICIENT_SAMPLE"}
    pnl = np.array([t["pnl"] for t in trades])
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    pf = (wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else None
    return {"n": len(trades), "win_rate": round(len(wins) / len(pnl), 4),
            "profit_factor": round(pf, 3) if pf else None,
            "avg_pnl_index_pts": round(float(pnl.mean()), 3), "total_pnl_index_pts": round(float(pnl.sum()), 1)}


if __name__ == "__main__":
    import json
    m1 = load_nifty_1m()
    sig = build_signals(m1)
    ce_n = int((sig["ce_signal"] & sig["in_window"]).sum())
    pe_n = int((sig["pe_signal"] & sig["in_window"]).sum())
    print(f"10-year NIFTY: {ce_n} CE signals, {pe_n} PE signals in entry windows "
          f"({len(sig)} 1-min bars scanned)")
    print("\n=== exploratory index-points P&L (SL=12/TP=24 index pts, assuming ATM "
          "delta~0.5 so a 6/12-premium-pt rule maps to ~12/24 index pts -- disclosed "
          "assumption, NOT the real premium rule) ===")
    print(json.dumps(simulate_index_points(sig, 12.0, 24.0, TIMEOUT_MIN), indent=2))
