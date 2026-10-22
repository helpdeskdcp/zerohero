"""Robustness checks on the reversal-sweep pattern, using the SAME rule/
parameters found in backtest.py (target_r=1.5, confirm_bars=3) -- no
re-tuning here, just stress-testing the one already-run result:
  1. DAILY+WEEKLY only (4H already shown net-negative -- drop it, don't
     average it away)
  2. Chronological train/test split (first 7y vs last ~3.9y) -- a real
     edge should not live only in one half
  3. Realistic cost drag (NIFTY futures round-trip ~2-3 points: brokerage+
     STT+exchange+slippage) subtracted per trade
  4. BANKNIFTY cross-validation, SAME rule/params, zero re-fitting
"""
import sys
sys.path.insert(0, "/root/zerohero/backend/data/research/reversal_sweep_2026-09")
import json

import numpy as np

from backtest import CSV, get_trades, _report

BANKNIFTY_CSV = ("/root/zerohero/backend/data/historical/kaggle/"
                  "sandeepkapri__banknifty-data-upto-2024/bank-nifty-1m-data.csv")


def daily_weekly_only(trades):
    return [t for t in trades if t["level"] in ("DAILY", "WEEKLY")]


def pnl_of(t):
    return (t["entry"] - t["exit"]) if t["direction"] == "SHORT" else (t["exit"] - t["entry"])


def report_subset(trades, label, cost_pts=0.0):
    if not trades:
        print(f"{label}: n=0 INSUFFICIENT_SAMPLE")
        return
    pnl = np.array([pnl_of(t) - cost_pts for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    win_rate = len(wins) / len(pnl)
    pf = (wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else None
    pf_str = f"{pf:.3f}" if pf is not None else "inf"
    print(f"{label}: n={len(pnl)} win_rate={win_rate:.3f} "
          f"PF={pf_str} avg_pnl={pnl.mean():.3f} total={pnl.sum():.1f}")


def main():
    print("=== loading NIFTY trades (target_r=1.5, confirm_bars=3) ===")
    trades = get_trades(CSV, confirm_bars=3, target_r=1.5)
    trades.sort(key=lambda t: t["entry_ts"])

    print("\n--- 1. DAILY+WEEKLY only (4H dropped) ---")
    dw = daily_weekly_only(trades)
    report_subset(dw, "NIFTY DAILY+WEEKLY, no cost")
    report_subset(dw, "NIFTY DAILY+WEEKLY, 2.5pt cost/trade", cost_pts=2.5)

    print("\n--- 2. Chronological train/test split (all levels pooled) ---")
    split = len(trades) // 2
    # split by TIME, not index count, for an honest calendar split
    ts_sorted = [t["entry_ts"] for t in trades]
    mid_ts = ts_sorted[len(ts_sorted) // 2]
    train = [t for t in trades if t["entry_ts"] < mid_ts]
    test = [t for t in trades if t["entry_ts"] >= mid_ts]
    print(f"split point: {mid_ts}")
    report_subset(train, "TRAIN (earlier half), all levels")
    report_subset(test, "TEST (later half), all levels")

    train_dw = daily_weekly_only(train)
    test_dw = daily_weekly_only(test)
    report_subset(train_dw, "TRAIN (earlier half), DAILY+WEEKLY only")
    report_subset(test_dw, "TEST (later half), DAILY+WEEKLY only")

    print("\n--- 3. DAILY+WEEKLY only, split further by exact level ---")
    for lvl in ("DAILY", "WEEKLY"):
        lvl_trades = [t for t in trades if t["level"] == lvl]
        mid = len(lvl_trades) // 2
        lvl_sorted = sorted(lvl_trades, key=lambda t: t["entry_ts"])
        report_subset(lvl_sorted[:mid], f"{lvl} TRAIN")
        report_subset(lvl_sorted[mid:], f"{lvl} TEST")

    print("\n=== 4. BANKNIFTY cross-validation (same rule, same params, zero re-fit) ===")
    bn_trades = get_trades(BANKNIFTY_CSV, confirm_bars=3, target_r=1.5)
    report_subset(bn_trades, "BANKNIFTY all levels, no cost")
    report_subset(daily_weekly_only(bn_trades), "BANKNIFTY DAILY+WEEKLY only, no cost")
    report_subset(daily_weekly_only(bn_trades), "BANKNIFTY DAILY+WEEKLY only, 2.5pt cost", cost_pts=2.5)


if __name__ == "__main__":
    main()
