"""Phase B: false-breakout-reversal (PDH/PDL/ORH/ORL + RSI) against REAL
captured NIFTY option premium, ~24 days (same hard sample ceiling disclosed
in the other two studies this session)."""
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/root/zerohero/backend/data/research/false_breakout_reversal_2026-09")
from backtest import build_levels, in_entry_window, apply_daily_limits, RSI_HIGH, RSI_LOW, CONFIRM_BARS

DB = "/root/zerohero/backend/data/market_history.db"
SL_PTS, TP_PTS = 8.0, 16.0
LOT_SIZE = 65
STRIKE_STEP = 50


def load_index_1m() -> pd.DataFrame:
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT received_ts, ltp FROM quote_snapshots WHERE symbol='NIFTY' AND kind='INDEX' "
        "AND ltp IS NOT NULL ORDER BY received_ts", conn)
    conn.close()
    df["datetime"] = pd.to_datetime(df["received_ts"], utc=True).dt.tz_convert("Asia/Kolkata")
    df = df.set_index("datetime")[["ltp"]]
    r = df["ltp"].resample("1min", label="left", closed="left")
    return pd.concat([r.first(), r.max(), r.min(), r.last()],
                     axis=1, keys=["open", "high", "low", "close"]).dropna()


def load_option_ticks() -> pd.DataFrame:
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT received_ts, expiry, strike, option_type, ltp FROM quote_snapshots "
        "WHERE symbol='NIFTY' AND kind='OPTION' AND ltp IS NOT NULL", conn)
    conn.close()
    df["ts"] = pd.to_datetime(df["received_ts"], utc=True).dt.tz_convert("Asia/Kolkata")
    return df.sort_values("ts")


def nearest_expiry(ts, expiries):
    parsed = [(pd.Timestamp(e, tz="Asia/Kolkata").normalize() + pd.Timedelta(hours=15, minutes=30), e)
             for e in expiries]
    future = [(d, e) for d, e in parsed if d >= ts]
    return min(future, key=lambda x: x[0])[1] if future else None


def real_premium_at(opt_ticks, expiry, strike, opt_type, ts, tolerance_min=30):
    sub = opt_ticks[(opt_ticks["expiry"] == expiry) & (opt_ticks["strike"] == strike)
                    & (opt_ticks["option_type"] == opt_type)]
    if sub.empty:
        return None
    idx = sub["ts"].searchsorted(ts)
    cands = []
    if idx < len(sub):
        cands.append(sub.iloc[idx])
    if idx > 0:
        cands.append(sub.iloc[idx - 1])
    if not cands:
        return None
    best = min(cands, key=lambda r: abs((r["ts"] - ts).total_seconds()))
    if abs((best["ts"] - ts).total_seconds()) > tolerance_min * 60:
        return None
    return float(best["ltp"])


def main():
    print("Loading real captured NIFTY index ticks...")
    idx = load_index_1m()
    lv = build_levels(idx)
    print(f"  {len(lv)} 1-min bars, {lv.index.min()} -> {lv.index.max()}")

    opt_ticks = load_option_ticks()
    expiries = sorted(opt_ticks["expiry"].dropna().unique().tolist())
    print(f"  {len(opt_ticks)} option ticks, expiries: {expiries}")

    highs, lows, closes = lv["high"].values, lv["low"].values, lv["close"].values
    pdh, pdl = lv["pd_high"].values, lv["pd_low"].values
    orh, orl = lv["or_high"].values, lv["or_low"].values
    rsi_v = lv["rsi14"].values
    dates = lv["date"].values
    ts_idx = lv.index
    n = len(lv)

    signals = []
    i = 0
    while i < n - CONFIRM_BARS - 1:
        fired = False
        fired_at = None
        for tag, lvl in (("PDH", pdh[i]), ("ORH", orh[i])):
            if lvl is None or np.isnan(lvl) or highs[i] <= lvl or np.isnan(rsi_v[i]) or rsi_v[i] <= RSI_HIGH:
                continue
            for k in range(1, CONFIRM_BARS + 1):
                j = i + k
                if j >= n or dates[j] != dates[i] or np.isnan(rsi_v[j]):
                    break
                if closes[j] < lvl and rsi_v[j] < rsi_v[i] and in_entry_window(ts_idx[j]):
                    signals.append(("PE", tag, ts_idx[j], closes[j]))
                    fired, fired_at = True, j
                    break
            if fired:
                break
        if not fired:
            for tag, lvl in (("PDL", pdl[i]), ("ORL", orl[i])):
                if lvl is None or np.isnan(lvl) or lows[i] >= lvl or np.isnan(rsi_v[i]) or rsi_v[i] >= RSI_LOW:
                    continue
                for k in range(1, CONFIRM_BARS + 1):
                    j = i + k
                    if j >= n or dates[j] != dates[i] or np.isnan(rsi_v[j]):
                        break
                    if closes[j] > lvl and rsi_v[j] > rsi_v[i] and in_entry_window(ts_idx[j]):
                        signals.append(("CE", tag, ts_idx[j], closes[j]))
                        fired, fired_at = True, j
                        break
                if fired:
                    break
        i = fired_at + 1 if fired else i + 1

    print(f"\n{len(signals)} real index-level signals found in the captured window")

    trades = []
    for opt_type, level, entry_ts, spot in signals:
        strike = round(spot / STRIKE_STEP) * STRIKE_STEP
        expiry = nearest_expiry(entry_ts, expiries)
        if expiry is None:
            continue
        entry_prem = real_premium_at(opt_ticks, expiry, strike, opt_type, entry_ts)
        if entry_prem is None:
            continue
        sl_level, tp_level = entry_prem - SL_PTS, entry_prem + TP_PTS
        series = opt_ticks[(opt_ticks["expiry"] == expiry) & (opt_ticks["strike"] == strike)
                          & (opt_ticks["option_type"] == opt_type) & (opt_ticks["ts"] > entry_ts)]
        series = series[series["ts"].dt.date == entry_ts.date()].sort_values("ts")
        exit_prem, reason = None, None
        for _, row in series.iterrows():
            px = float(row["ltp"])
            if px <= sl_level:
                exit_prem, reason = px, "SL"
                break
            if px >= tp_level:
                exit_prem, reason = px, "TP"
                break
        if exit_prem is None:
            if series.empty:
                continue
            exit_prem, reason = float(series.iloc[-1]["ltp"]), "EOD"
        pnl = (exit_prem - entry_prem) * LOT_SIZE
        trades.append({"direction": opt_type, "level": level, "entry_ts": entry_ts, "reason": reason,
                       "entry_premium": entry_prem, "exit_premium": exit_prem, "pnl": round(pnl, 2)})

    print(f"{len(trades)} trades resolved with real captured option data\n")
    if not trades:
        print("INSUFFICIENT_SAMPLE")
        return
    pnl = np.array([t["pnl"] for t in trades])
    wins = pnl[pnl > 0]
    print(f"n={len(pnl)}  win_rate={len(wins)/len(pnl):.3f}  total=Rs{pnl.sum():.0f}  avg=Rs{pnl.mean():.1f}/trade")
    for t in trades:
        print(f"  {str(t['entry_ts'])[:16]} {t['direction']} {t['level']:4s} "
              f"entry={t['entry_premium']:.2f} exit={t['exit_premium']:.2f} ({t['reason']})  pnl=Rs{t['pnl']:.0f}")


if __name__ == "__main__":
    main()
