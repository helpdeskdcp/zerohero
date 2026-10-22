"""
Phase 2: does the reversal-sweep pattern (same rule, same params as
backtest.py) survive when translated to REAL captured NIFTY option premium,
not just index points?

DATA: real captured ticks, market_history.db::quote_snapshots, NIFTY INDEX +
OPTION, 2026-09-02 -> 2026-09-25 (~24 calendar days -- this is the entire
real captured window that exists; no older option premium data exists in
this system). This is a HARD sample-size ceiling, disclosed up front: expect
few signals, likely INSUFFICIENT_SAMPLE for a real GO/NO-GO call. Reported
honestly regardless of what it shows.

Same rule, same entry/stop/target logic as backtest.py (target_r=1.5,
confirm_bars=3), just: (1) levels+bars built from real captured tick data
instead of the Kaggle CSV, (2) P&L computed from the REAL captured option
premium at the index-level entry/exit timestamps, not index points.

No calibrated cost model exists for NIFTY options in this codebase
(app.institutional_edge.costs itself reports NIFTY as UNCALIBRATED) -- gross
P&L is reported as the primary number; a labeled ESTIMATE brokerage/STT/
exchange cost is shown separately for context, never presented as
calibrated.
"""
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/root/zerohero/backend/data/research/reversal_sweep_2026-09")

import numpy as np
import pandas as pd

from backtest import build_levels, resample_ohlc

DB = "/root/zerohero/backend/data/market_history.db"
LOT_SIZE = 65  # NIFTY, VERIFIED (app.instrument_profiles)
TARGET_R = 1.5
CONFIRM_BARS = 3
STRIKE_STEP = 50


def load_index_ticks() -> pd.DataFrame:
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT received_ts, ltp FROM quote_snapshots WHERE symbol='NIFTY' AND kind='INDEX' "
        "AND ltp IS NOT NULL ORDER BY received_ts", conn)
    conn.close()
    df["datetime"] = pd.to_datetime(df["received_ts"], utc=True).dt.tz_convert("Asia/Kolkata")
    df = df.set_index("datetime")[["ltp"]].rename(columns={"ltp": "close"})
    df["open"], df["high"], df["low"] = df["close"], df["close"], df["close"]
    return df[["open", "high", "low", "close"]]


def load_option_ticks() -> pd.DataFrame:
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT received_ts, expiry, strike, option_type, ltp FROM quote_snapshots "
        "WHERE symbol='NIFTY' AND kind='OPTION' AND ltp IS NOT NULL", conn)
    conn.close()
    df["ts"] = pd.to_datetime(df["received_ts"], utc=True).dt.tz_convert("Asia/Kolkata")
    return df.sort_values("ts")


def resample_from_ticks(ticks: pd.DataFrame, rule: str) -> pd.DataFrame:
    r = ticks["close"].resample(rule, label="left", closed="left")
    out = pd.concat([r.first(), r.max(), r.min(), r.last()],
                    axis=1, keys=["open", "high", "low", "close"]).dropna()
    return out


def nearest_expiry(ts: pd.Timestamp, expiries: list[str]) -> str | None:
    parsed = [(pd.Timestamp(e, tz="Asia/Kolkata").normalize() + pd.Timedelta(hours=15, minutes=30), e)
             for e in expiries]
    future = [(d, e) for d, e in parsed if d >= ts]
    if not future:
        return None
    return min(future, key=lambda x: x[0])[1]


def real_premium_at(opt_ticks: pd.DataFrame, expiry: str, strike: float, opt_type: str,
                    ts: pd.Timestamp, tolerance_min: int = 30) -> float | None:
    sub = opt_ticks[(opt_ticks["expiry"] == expiry) & (opt_ticks["strike"] == strike)
                    & (opt_ticks["option_type"] == opt_type)]
    if sub.empty:
        return None
    idx = sub["ts"].searchsorted(ts)
    candidates = []
    if idx < len(sub):
        candidates.append(sub.iloc[idx])
    if idx > 0:
        candidates.append(sub.iloc[idx - 1])
    if not candidates:
        return None
    best = min(candidates, key=lambda r: abs((r["ts"] - ts).total_seconds()))
    if abs((best["ts"] - ts).total_seconds()) > tolerance_min * 60:
        return None
    return float(best["ltp"])


def main():
    print("Loading real captured NIFTY index ticks...")
    idx_ticks = load_index_ticks()
    print(f"  {len(idx_ticks)} index ticks, {idx_ticks.index.min()} -> {idx_ticks.index.max()}")

    m15 = resample_from_ticks(idx_ticks, "15min")
    daily = resample_from_ticks(idx_ticks, "1D")
    weekly = resample_from_ticks(idx_ticks, "1W")
    h4 = resample_from_ticks(idx_ticks, "4h")
    lv = build_levels(m15, daily, weekly, h4)
    lv["date"] = lv.index.date
    print(f"  {len(m15)} 15-min bars, {len(daily)} daily, {len(weekly)} weekly, {len(h4)} 4H periods")

    print("Loading real captured NIFTY option ticks...")
    opt_ticks = load_option_ticks()
    expiries = sorted(opt_ticks["expiry"].dropna().unique().tolist())
    print(f"  {len(opt_ticks)} option ticks, expiries: {expiries}")

    highs, lows, opens, closes = lv["high"].values, lv["low"].values, lv["open"].values, lv["close"].values
    dates = lv["date"].values
    n = len(lv)
    level_cols = {"d_high": "DAILY", "w_high": "WEEKLY", "h4_high": "4H"}
    level_cols_low = {"d_low": "DAILY", "w_low": "WEEKLY", "h4_low": "4H"}

    signals = []
    i = 0
    while i < n - CONFIRM_BARS - 1:
        red = closes[i] < opens[i]
        green = closes[i] > opens[i]
        fired = False
        fired_at = None
        if red:
            for col, tag in level_cols.items():
                lvl = lv[col].values[i]
                if lvl is not None and not np.isnan(lvl) and highs[i] > lvl:
                    for k in range(1, CONFIRM_BARS + 1):
                        j = i + k
                        if j >= n or dates[j] != dates[i]:
                            break
                        if closes[j] < lvl:
                            signals.append(("SHORT", tag, lv.index[j], closes[j], highs[i], j))
                            fired = True
                            fired_at = j
                            break
                    if fired:
                        break
        if not fired and green:
            for col, tag in level_cols_low.items():
                lvl = lv[col].values[i]
                if lvl is not None and not np.isnan(lvl) and lows[i] < lvl:
                    for k in range(1, CONFIRM_BARS + 1):
                        j = i + k
                        if j >= n or dates[j] != dates[i]:
                            break
                        if closes[j] > lvl:
                            signals.append(("LONG", tag, lv.index[j], closes[j], lows[i], j))
                            fired = True
                            fired_at = j
                            break
                    if fired:
                        break
        # advance PAST the confirmation bar on a fire -- same real overlap
        # bug fixed in backtest.py's get_trades()
        i = fired_at + 1 if fired else i + 1

    print(f"\n{len(signals)} real index-level signals found in the captured window")

    results = []
    for direction, level_tag, entry_ts, index_entry, index_stop_ref, entry_idx in signals:
        risk = abs(index_stop_ref - index_entry)
        if risk <= 0:
            continue
        if direction == "SHORT":
            index_target = index_entry - TARGET_R * risk
        else:
            index_target = index_entry + TARGET_R * risk

        # walk forward on the INDEX to find the real resolved exit_ts (same
        # stop/target/EOD logic as backtest.py's _simulate)
        exit_ts, exit_reason = None, None
        entry_date = dates[entry_idx]
        for k in range(1, 27):
            j = entry_idx + k
            if j >= n or dates[j] != entry_date:
                exit_ts, exit_reason = lv.index[min(j - 1, n - 1)], "EOD"
                break
            if direction == "SHORT":
                if highs[j] >= index_stop_ref:
                    exit_ts, exit_reason = lv.index[j], "STOP"
                    break
                if lows[j] <= index_target:
                    exit_ts, exit_reason = lv.index[j], "TARGET"
                    break
            else:
                if lows[j] <= index_stop_ref:
                    exit_ts, exit_reason = lv.index[j], "STOP"
                    break
                if highs[j] >= index_target:
                    exit_ts, exit_reason = lv.index[j], "TARGET"
                    break
        if exit_ts is None:
            continue

        opt_type = "PE" if direction == "SHORT" else "CE"
        strike = round(index_entry / STRIKE_STEP) * STRIKE_STEP
        expiry = nearest_expiry(entry_ts, expiries)
        if expiry is None:
            continue

        entry_prem = real_premium_at(opt_ticks, expiry, strike, opt_type, entry_ts)
        exit_prem = real_premium_at(opt_ticks, expiry, strike, opt_type, exit_ts)
        if entry_prem is None or exit_prem is None:
            results.append({"direction": direction, "level": level_tag, "status": "NO_REAL_QUOTE",
                            "entry_ts": str(entry_ts)})
            continue

        gross_pnl = (exit_prem - entry_prem) * LOT_SIZE  # long option either way (buy CE or buy PE)
        results.append({"direction": direction, "level": level_tag, "status": "OK",
                        "entry_ts": str(entry_ts), "exit_ts": str(exit_ts), "exit_reason": exit_reason,
                        "strike": strike, "option_type": opt_type, "expiry": expiry,
                        "entry_premium": entry_prem, "exit_premium": exit_prem, "gross_pnl": round(gross_pnl, 2)})

    ok = [r for r in results if r["status"] == "OK"]
    no_quote = [r for r in results if r["status"] == "NO_REAL_QUOTE"]
    print(f"\n{len(ok)} signals resolved with REAL captured entry+exit option premium")
    print(f"{len(no_quote)} signals had no real quote available (skipped, not fabricated)")

    if ok:
        pnl = np.array([r["gross_pnl"] for r in ok])
        wins = pnl[pnl > 0]
        print(f"\n=== GROSS P&L (real premiums, no cost) ===")
        print(f"n={len(pnl)}  win_rate={len(wins)/len(pnl):.3f}  "
              f"total_pnl=Rs{pnl.sum():.0f}  avg_pnl=Rs{pnl.mean():.1f}/trade")
        # illustrative ESTIMATE cost (NOT calibrated -- flagged): flat Rs40
        # brokerage (2 legs) + 0.0625% STT on sell-side premium value + ~Rs20
        # exchange/GST/stamp -- ballpark retail-discount-broker figures, not
        # a verified contract note (institutional_edge.costs itself reports
        # NIFTY options as UNCALIBRATED)
        est_costs = []
        for r in ok:
            sell_value = max(r["entry_premium"], r["exit_premium"]) * LOT_SIZE
            est = 40 + sell_value * 0.000625 + 20
            est_costs.append(est)
        net = pnl - np.array(est_costs)
        print(f"\n=== NET P&L (ESTIMATE cost only, NOT calibrated) ===")
        print(f"avg_est_cost=Rs{np.mean(est_costs):.1f}/trade")
        print(f"n={len(net)}  win_rate={(net>0).sum()/len(net):.3f}  "
              f"total_pnl=Rs{net.sum():.0f}  avg_pnl=Rs{net.mean():.1f}/trade")

        print("\n=== by level ===")
        for lvl in ("DAILY", "WEEKLY", "4H"):
            sub = [r["gross_pnl"] for r in ok if r["level"] == lvl]
            if sub:
                subarr = np.array(sub)
                print(f"  {lvl}: n={len(sub)} win_rate={(subarr>0).sum()/len(sub):.3f} "
                      f"avg_gross=Rs{subarr.mean():.1f}")
            else:
                print(f"  {lvl}: n=0")

        print("\n=== individual trades ===")
        for r in ok:
            print(f"  {r['entry_ts'][:16]} {r['direction']:5s} {r['level']:6s} "
                  f"{r['option_type']} {r['strike']:.0f} {r['expiry']}  "
                  f"entry={r['entry_premium']:.2f} exit={r['exit_premium']:.2f} "
                  f"({r['exit_reason']})  gross=Rs{r['gross_pnl']:.0f}")
    else:
        print("INSUFFICIENT_SAMPLE -- zero signals resolved with real option quotes")

    if no_quote:
        print("\nSkipped (no real quote within tolerance):")
        for r in no_quote:
            print(f"  {r['entry_ts']} {r['direction']} {r['level']}")


if __name__ == "__main__":
    main()
