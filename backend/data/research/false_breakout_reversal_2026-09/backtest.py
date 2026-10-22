"""
False Breakout Reversal (PDH/PDL + Opening Range) -- backtest before any
production code, same standing discipline as the other two strategy specs
tested this session.

RULE (as given, restated):
  Levels: Previous Day High/Low (PDH/PDL) + Opening Range High/Low (ORH/ORL,
    09:15-09:30). Execution: 1-min.
  BEARISH (buy PE): price breaks ABOVE PDH/ORH, fails within 1-3 candles
    (closes back below), AND [RSI(14) bearish divergence OR RSI>65 dropping
    back down] OR [reversal-candle volume > breakout-candle volume]. Buy PE
    on candle close.
  BULLISH (buy CE): mirror.
  SL 8pts / TP 16pts on OPTION PREMIUM (1:2 R:R). Max 3 trades/day or stop
  after 2 consecutive SL. Entry windows 09:30-11:00 and 14:00-15:15.

DISCLOSED CHOICES / DATA LIMITS:
  - Real Kaggle NIFTY 1-min data has NO trustworthy volume (all zero in the
    source file) -- the volume-spike confirmation clause CANNOT be tested
    honestly here. Only the RSI clause is implemented (the rule's own "OR"
    already offers this as a standalone sufficient trigger).
  - "RSI bearish/bullish divergence" needs swing-point comparison logic the
    rule didn't fully specify; implemented the OTHER, unambiguous half of
    the OR instead: RSI>65 at the breakout candle, RSI has dropped by the
    reversal candle (bearish); RSI<35 at breakdown, risen back by reversal
    (bullish). Divergence itself is NOT implemented -- flagged, not silently
    dropped.
  - Execution TF = 1-min (rule offered 1-min OR 3-min; 1-min gives more
    signals / finer resolution, disclosed choice).
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

NIFTY_CSV = ("/root/zerohero/backend/data/historical/kaggle/"
             "mavrick136__10-years-of-nifty-50-1-minute-data-20152025/NIFTY_1MIN_2015-2025.csv")
DB = "/root/zerohero/backend/data/market_history.db"

ENTRY_WINDOWS = [((9, 30), (11, 0)), ((14, 0), (15, 15))]
MAX_TRADES_PER_DAY = 3
MAX_CONSEC_SL = 2
CONFIRM_BARS = 3
RSI_LEN = 14
RSI_HIGH, RSI_LOW = 65.0, 35.0


def load_nifty_1m(csv_path: str = NIFTY_CSV) -> pd.DataFrame:
    """Handles both real formats seen in data/historical/kaggle/: a single
    'datetime' column (NIFTY), or separate 'Date'+'Time' columns with
    capitalized OHLC (BANKNIFTY) -- see reversal_sweep_2026-09/backtest.py
    for the same handling."""
    df = pd.read_csv(csv_path)
    cols = {c.lower(): c for c in df.columns}
    if "datetime" in cols:
        df["datetime"] = pd.to_datetime(df[cols["datetime"]])
    elif "date" in cols and "time" in cols:
        df["datetime"] = pd.to_datetime(df[cols["date"]] + " " + df[cols["time"]], format="%d-%m-%Y %H:%M:%S")
    else:
        raise ValueError(f"{csv_path}: no recognizable datetime column(s)")
    df = df.rename(columns={cols.get("open", "Open"): "open", cols.get("high", "High"): "high",
                            cols.get("low", "Low"): "low", cols.get("close", "Close"): "close"})
    df = df.set_index("datetime").sort_index()
    return df[~df.index.duplicated(keep="first")][["open", "high", "low", "close"]]


def rsi(closes: pd.Series, length: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def in_entry_window(ts: pd.Timestamp) -> bool:
    t = (ts.hour, ts.minute)
    return any(start <= t <= end for start, end in ENTRY_WINDOWS)


def build_levels(df: pd.DataFrame) -> pd.DataFrame:
    """PDH/PDL: prior COMPLETED day's high/low, no look-ahead (shift(1) +
    merge_asof, same pattern as reversal_sweep_2026-09/backtest.py).
    ORH/ORL: today's OWN 09:15-09:30 high/low -- visible only from 09:30
    onward the SAME day (never before the range itself has closed)."""
    out = df.copy()
    daily = df.resample("1D", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    shifted = daily.shift(1)
    for col in ("high", "low"):
        s = shifted[[col]].reset_index().rename(columns={"datetime": "datetime"})
        merged = pd.merge_asof(out.reset_index()[["datetime"]], s, on="datetime", direction="backward")
        out[f"pd_{col}"] = merged[col].values

    out["date"] = out.index.date
    or_high, or_low = {}, {}
    for date, day_df in out.groupby("date"):
        t = day_df.index.time
        rng = day_df[(t >= pd.Timestamp("09:15").time()) & (t < pd.Timestamp("09:30").time())]
        if not rng.empty:
            or_high[date] = rng["high"].max()
            or_low[date] = rng["low"].min()
    out["or_high"] = out["date"].map(or_high)
    out["or_low"] = out["date"].map(or_low)
    # ORH/ORL only visible from 09:30 onward (the range must have closed)
    before_930 = pd.Series([t < pd.Timestamp("09:30").time() for t in out.index.time], index=out.index)
    out.loc[before_930, ["or_high", "or_low"]] = np.nan

    out["rsi14"] = rsi(out["close"], RSI_LEN)
    return out


def get_trades(csv_path: str = NIFTY_CSV, sl_pts: float = 8.0, tp_pts: float = 16.0,
               confirm_bars: int = CONFIRM_BARS, points_are_index: bool = False,
               index_delta: float = 0.5) -> list[dict]:
    """points_are_index=False: sl_pts/tp_pts are OPTION-premium points (the
    real rule) -- converted here to index points via `index_delta` ONLY for
    the large-sample exploratory pass (disclosed assumption, same pattern as
    vwap_ema_scalp_2026-09). points_are_index=True: sl_pts/tp_pts used
    directly as index points."""
    df = load_nifty_1m(csv_path)
    lv = build_levels(df)
    sl_idx = sl_pts if points_are_index else sl_pts / index_delta
    tp_idx = tp_pts if points_are_index else tp_pts / index_delta

    highs, lows, closes = lv["high"].values, lv["low"].values, lv["close"].values
    pdh, pdl = lv["pd_high"].values, lv["pd_low"].values
    orh, orl = lv["or_high"].values, lv["or_low"].values
    rsi_v = lv["rsi14"].values
    dates = lv["date"].values
    idx = lv.index
    n = len(lv)

    trades = []
    i = 0
    while i < n - confirm_bars - 1:
        fired = False
        fired_at = None
        levels_above = [("PDH", pdh[i]), ("ORH", orh[i])]
        for tag, lvl in levels_above:
            if lvl is None or np.isnan(lvl) or highs[i] <= lvl or np.isnan(rsi_v[i]):
                continue
            if rsi_v[i] <= RSI_HIGH:
                continue
            for k in range(1, confirm_bars + 1):
                j = i + k
                if j >= n or dates[j] != dates[i] or np.isnan(rsi_v[j]):
                    break
                if closes[j] < lvl and rsi_v[j] < rsi_v[i] and in_entry_window(idx[j]):
                    entry = closes[j]
                    stop = entry + sl_idx
                    target = entry - tp_idx
                    trades.append(_simulate(lv, j, "PE", entry, stop, target, tag, idx[j]))
                    fired, fired_at = True, j
                    break
            if fired:
                break
        if not fired:
            levels_below = [("PDL", pdl[i]), ("ORL", orl[i])]
            for tag, lvl in levels_below:
                if lvl is None or np.isnan(lvl) or lows[i] >= lvl or np.isnan(rsi_v[i]):
                    continue
                if rsi_v[i] >= RSI_LOW:
                    continue
                for k in range(1, confirm_bars + 1):
                    j = i + k
                    if j >= n or dates[j] != dates[i] or np.isnan(rsi_v[j]):
                        break
                    if closes[j] > lvl and rsi_v[j] > rsi_v[i] and in_entry_window(idx[j]):
                        entry = closes[j]
                        stop = entry - sl_idx
                        target = entry + tp_idx
                        trades.append(_simulate(lv, j, "CE", entry, stop, target, tag, idx[j]))
                        fired, fired_at = True, j
                        break
                if fired:
                    break
        i = fired_at + 1 if fired else i + 1
    return trades


def _simulate(lv, entry_idx, direction, entry, stop, target, level_tag, entry_ts, max_bars=45):
    highs, lows, closes = lv["high"].values, lv["low"].values, lv["close"].values
    dates = lv["date"].values
    entry_date = dates[entry_idx]
    n = len(lv)
    base = {"direction": direction, "entry": entry, "stop": stop, "target": target,
            "level": level_tag, "entry_ts": entry_ts}
    for k in range(1, max_bars + 1):
        j = entry_idx + k
        if j >= n or dates[j] != entry_date:
            return {**base, "exit": closes[min(j - 1, n - 1)], "reason": "EOD"}
        if direction == "PE":  # profits when price falls
            if highs[j] >= stop:
                return {**base, "exit": stop, "reason": "SL"}
            if lows[j] <= target:
                return {**base, "exit": target, "reason": "TP"}
        else:
            if lows[j] <= stop:
                return {**base, "exit": stop, "reason": "SL"}
            if highs[j] >= target:
                return {**base, "exit": target, "reason": "TP"}
    return {**base, "exit": closes[min(entry_idx + max_bars, n - 1)], "reason": "MAXBARS"}


def apply_daily_limits(trades: list[dict]) -> list[dict]:
    """Max 3 trades/day, stop after 2 consecutive SL -- applied chronologically."""
    by_date: dict = {}
    for t in sorted(trades, key=lambda x: x["entry_ts"]):
        by_date.setdefault(t["entry_ts"].date(), []).append(t)
    kept = []
    for date, day_trades in by_date.items():
        count, consec_sl = 0, 0
        for t in day_trades:
            if count >= MAX_TRADES_PER_DAY or consec_sl >= MAX_CONSEC_SL:
                break
            kept.append(t)
            count += 1
            consec_sl = consec_sl + 1 if t["reason"] == "SL" else 0
    return kept


def pnl_of(t, direction_sign=True):
    return (t["entry"] - t["exit"]) if t["direction"] == "PE" else (t["exit"] - t["entry"])


def report(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "verdict": "INSUFFICIENT_SAMPLE"}
    pnl = np.array([pnl_of(t) for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    pf = (wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else None
    by_level = {}
    for t, p in zip(trades, pnl):
        by_level.setdefault(t["level"], []).append(p)
    by_level_stats = {k: {"n": len(v), "win_rate": round(sum(1 for x in v if x > 0) / len(v), 3),
                          "avg_pnl": round(float(np.mean(v)), 2)} for k, v in by_level.items()}
    return {"n": len(pnl), "win_rate": round(len(wins) / len(pnl), 4),
            "profit_factor": round(pf, 3) if pf else None,
            "avg_pnl": round(float(pnl.mean()), 3), "total_pnl": round(float(pnl.sum()), 1),
            "by_level": by_level_stats, "verdict": "OK" if len(pnl) >= 30 else "INSUFFICIENT_SAMPLE"}


if __name__ == "__main__":
    import json
    print("=== exploratory index-points pass (SL/TP as index pts, delta=0.5 assumed) ===")
    raw = get_trades(sl_pts=8.0, tp_pts=16.0, points_are_index=False, index_delta=0.5)
    print(f"raw signals (before daily limits): {len(raw)}")
    limited = apply_daily_limits(raw)
    print(json.dumps(report(limited), indent=2))
