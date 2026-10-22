"""Phase B: the VWAP+9EMA rejection-scalp rule against REAL captured NIFTY
option premium (~24 days, the entire real window that exists in this
system -- same hard sample-size ceiling as the reversal-sweep study,
disclosed up front). Applies the literal 6pt SL / 12pt TP on premium,
09:15-10:30 + 14:15-15:15 entry windows, max 3 trades/day, stop after 2
consecutive losses, 8-min timeout -- all from the rule as given.
"""
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/root/zerohero/backend/data/research/vwap_ema_scalp_2026-09")
from backtest import build_signals, ENTRY_WINDOWS, MAX_TRADES_PER_DAY, MAX_CONSEC_LOSSES, TIMEOUT_MIN, STRIKE_STEP

DB = "/root/zerohero/backend/data/market_history.db"
SL_PTS, TP_PTS = 6.0, 12.0
LOT_SIZE = 65


def load_index_1m() -> pd.DataFrame:
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT received_ts, ltp FROM quote_snapshots WHERE symbol='NIFTY' AND kind='INDEX' "
        "AND ltp IS NOT NULL ORDER BY received_ts", conn)
    conn.close()
    df["datetime"] = pd.to_datetime(df["received_ts"], utc=True).dt.tz_convert("Asia/Kolkata")
    df = df.set_index("datetime")[["ltp"]]
    r = df["ltp"].resample("1min", label="left", closed="left")
    out = pd.concat([r.first(), r.max(), r.min(), r.last()],
                    axis=1, keys=["open", "high", "low", "close"]).dropna()
    return out


def load_option_ticks() -> pd.DataFrame:
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT received_ts, expiry, strike, option_type, ltp FROM quote_snapshots "
        "WHERE symbol='NIFTY' AND kind='OPTION' AND ltp IS NOT NULL", conn)
    conn.close()
    df["ts"] = pd.to_datetime(df["received_ts"], utc=True).dt.tz_convert("Asia/Kolkata")
    return df.sort_values("ts")


def nearest_expiry(ts: pd.Timestamp, expiries: list[str]) -> str | None:
    parsed = [(pd.Timestamp(e, tz="Asia/Kolkata").normalize() + pd.Timedelta(hours=15, minutes=30), e)
             for e in expiries]
    future = [(d, e) for d, e in parsed if d >= ts]
    return min(future, key=lambda x: x[0])[1] if future else None


def option_series(opt_ticks, expiry, strike, opt_type) -> pd.DataFrame:
    sub = opt_ticks[(opt_ticks["expiry"] == expiry) & (opt_ticks["strike"] == strike)
                    & (opt_ticks["option_type"] == opt_type)]
    return sub.sort_values("ts")


def simulate_trade(opt_ticks, expiry, strike, opt_type, entry_ts) -> dict | None:
    series = option_series(opt_ticks, expiry, strike, opt_type)
    if series.empty:
        return None
    after_entry = series[series["ts"] >= entry_ts]
    if after_entry.empty:
        return None
    entry_row = after_entry.iloc[0]
    if abs((entry_row["ts"] - entry_ts).total_seconds()) > 30 * 60:
        return None
    entry_premium = float(entry_row["ltp"])
    sl_level, tp_level = entry_premium - SL_PTS, entry_premium + TP_PTS
    timeout_ts = entry_ts + pd.Timedelta(minutes=TIMEOUT_MIN)

    window = after_entry[(after_entry["ts"] > entry_row["ts"]) & (after_entry["ts"] <= timeout_ts + pd.Timedelta(minutes=15))]
    for _, row in window.iterrows():
        px = float(row["ltp"])
        if row["ts"] <= timeout_ts:
            if px <= sl_level:
                return {"entry_ts": entry_ts, "entry_premium": entry_premium, "exit_ts": row["ts"],
                        "exit_premium": px, "reason": "SL", "expiry": expiry, "strike": strike, "opt_type": opt_type}
            if px >= tp_level:
                return {"entry_ts": entry_ts, "entry_premium": entry_premium, "exit_ts": row["ts"],
                        "exit_premium": px, "reason": "TP", "expiry": expiry, "strike": strike, "opt_type": opt_type}
        else:
            return {"entry_ts": entry_ts, "entry_premium": entry_premium, "exit_ts": row["ts"],
                    "exit_premium": px, "reason": "TIMEOUT", "expiry": expiry, "strike": strike, "opt_type": opt_type}
    return None  # no real tick data covered the resolution window -- skip, don't fabricate


def main():
    print("Loading real captured NIFTY index ticks (1-min resampled)...")
    idx = load_index_1m()
    print(f"  {len(idx)} 1-min bars, {idx.index.min()} -> {idx.index.max()}")
    sig = build_signals(idx)

    print("Loading real captured NIFTY option ticks...")
    opt_ticks = load_option_ticks()
    expiries = sorted(opt_ticks["expiry"].dropna().unique().tolist())
    print(f"  {len(opt_ticks)} option ticks, expiries: {expiries}")

    candidates = sig[(sig["ce_signal"] | sig["pe_signal"]) & sig["in_window"]].copy()
    candidates["date"] = candidates.index.date
    print(f"\n{len(candidates)} raw signals in entry windows across the real captured window")

    trades = []
    skipped_no_data = 0
    for date, day_rows in candidates.groupby("date"):
        trades_today = 0
        consec_losses = 0
        for ts, row in day_rows.iterrows():
            if trades_today >= MAX_TRADES_PER_DAY or consec_losses >= MAX_CONSEC_LOSSES:
                break
            opt_type = "CE" if row["ce_signal"] else "PE"
            spot = row["close"]
            strike = round(spot / STRIKE_STEP) * STRIKE_STEP
            expiry = nearest_expiry(ts, expiries)
            if expiry is None:
                continue
            result = simulate_trade(opt_ticks, expiry, strike, opt_type, ts)
            if result is None:
                skipped_no_data += 1
                continue
            pnl = (result["exit_premium"] - result["entry_premium"]) * LOT_SIZE
            result["pnl"] = round(pnl, 2)
            trades.append(result)
            trades_today += 1
            consec_losses = consec_losses + 1 if pnl <= 0 else 0

    print(f"{len(trades)} trades resolved with real captured option data")
    print(f"{skipped_no_data} signals skipped -- no real option tick covered resolution window")

    if not trades:
        print("INSUFFICIENT_SAMPLE -- zero trades resolved")
        return

    pnl = np.array([t["pnl"] for t in trades])
    wins = pnl[pnl > 0]
    win_rate = len(wins) / len(pnl)
    print(f"\n=== RESULTS (real premiums, GROSS, no cost) ===")
    print(f"n={len(pnl)}  win_rate={win_rate:.3f}  total=Rs{pnl.sum():.0f}  avg=Rs{pnl.mean():.1f}/trade")

    by_reason = {}
    for t in trades:
        by_reason[t["reason"]] = by_reason.get(t["reason"], 0) + 1
    print(f"exit reasons: {by_reason}")

    print("\n=== individual trades ===")
    for t in trades:
        print(f"  {str(t['entry_ts'])[:16]} {t['opt_type']} {t['strike']:.0f} {t['expiry']}  "
              f"entry={t['entry_premium']:.2f} exit={t['exit_premium']:.2f} ({t['reason']})  "
              f"pnl=Rs{t['pnl']:.0f}")


if __name__ == "__main__":
    main()
