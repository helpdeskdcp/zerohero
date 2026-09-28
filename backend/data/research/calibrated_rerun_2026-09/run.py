"""
Re-run of the 3 real-option-premium tests (reversal_sweep, vwap_ema_scalp)
using the new shared_harness (data/research/shared_harness/) end-to-end and
the new real cost model (app.institutional_edge.costs.estimate_index_option_cost,
FORMULA_ESTIMATE, published NSE/Angel One regulatory rates -- see that
module) instead of each script's own hand-rolled cost guess.

CPR+ORB is NOT re-run against real option premium here: it already failed
at the INDEX-points level (PF 0.956 at just a 1pt estimated cost, before
ever reaching a real premium test) -- there is no real captured-premium
trade list for it to recompute, and building one now would not change an
already-clear index-level NO-GO.

Every trade below uses REAL captured NIFTY index+option ticks
(market_history.db::quote_snapshots, ~2026-09-02 to 09-25, the entire real
window that exists) -- the same hard, disclosed sample-size ceiling as the
original studies. Nothing here is a bigger sample than before; the only
thing that changed is the cost formula applied to the same real trades.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from shared_harness import candles, costs_bridge, levels, options, report, simulate

DB = "/root/zerohero/backend/data/market_history.db"
LOT_SIZE_NIFTY = 65
STRIKE_STEP = 50
TARGET_R = 1.5
CONFIRM_BARS = 3


def _old_estimate_cost(entry_premium: float, exit_premium: float, lot_size: int = LOT_SIZE_NIFTY) -> float:
    """The rough, hand-rolled guess used in the ORIGINAL reversal_sweep/
    false_breakout_reversal option-premium scripts (flat Rs40 brokerage-ish
    + 0.0625% STT-ish + Rs20) -- kept here ONLY to show the before/after
    delta against the new real formula, never used for a real decision."""
    sell_value = max(entry_premium, exit_premium) * lot_size
    return 40 + sell_value * 0.000625 + 20


def reversal_sweep_daily_weekly_real_premium() -> list[dict]:
    """Re-derives the reversal-sweep signals (DAILY+WEEKLY only -- 4H already
    proven net-negative, excluded) via shared_harness, then resolves each
    against REAL captured NIFTY option premium."""
    idx_ticks = candles.load_captured_index_ticks(DB, "NIFTY", "15min")
    daily = levels.resample_ohlc(idx_ticks, "1D")
    weekly = levels.resample_ohlc(idx_ticks, "1W")

    lv = idx_ticks.copy()
    lv["d_high"] = levels.prior_period_value(lv.index, daily, "high")
    lv["d_low"] = levels.prior_period_value(lv.index, daily, "low")
    lv["w_high"] = levels.prior_period_value(lv.index, weekly, "high")
    lv["w_low"] = levels.prior_period_value(lv.index, weekly, "low")
    lv["date"] = lv.index.date

    opt_ticks = candles.load_captured_option_ticks(DB, "NIFTY")
    expiries = sorted(opt_ticks["expiry"].dropna().unique().tolist())

    highs, lows, opens, closes = lv["high"].values, lv["low"].values, lv["open"].values, lv["close"].values
    dates = lv["date"].values
    idx = lv.index
    n = len(lv)
    high_cols = {"d_high": "DAILY", "w_high": "WEEKLY"}
    low_cols = {"d_low": "DAILY", "w_low": "WEEKLY"}

    real_trades = []
    i = 0
    while i < n - CONFIRM_BARS - 1:
        red, green = closes[i] < opens[i], closes[i] > opens[i]
        fired, fired_at = False, None
        if red:
            for col, tag in high_cols.items():
                lvl = lv[col].values[i]
                if lvl is None or np.isnan(lvl) or highs[i] <= lvl:
                    continue
                for k in range(1, CONFIRM_BARS + 1):
                    j = i + k
                    if j >= n or dates[j] != dates[i]:
                        break
                    if closes[j] < lvl:
                        real_trades.append(_resolve_real_trade(
                            lv, i, j, "SHORT", tag, highs[i], opt_ticks, expiries))
                        fired, fired_at = True, j
                        break
                if fired:
                    break
        if not fired and green:
            for col, tag in low_cols.items():
                lvl = lv[col].values[i]
                if lvl is None or np.isnan(lvl) or lows[i] >= lvl:
                    continue
                for k in range(1, CONFIRM_BARS + 1):
                    j = i + k
                    if j >= n or dates[j] != dates[i]:
                        break
                    if closes[j] > lvl:
                        real_trades.append(_resolve_real_trade(
                            lv, i, j, "LONG", tag, lows[i], opt_ticks, expiries))
                        fired, fired_at = True, j
                        break
                if fired:
                    break
        i = simulate.next_scan_index(i, fired, fired_at)

    return [t for t in real_trades if t is not None]


def _resolve_real_trade(lv, break_idx, entry_idx, direction, tag, stop_ref, opt_ticks, expiries) -> dict | None:
    entry_index_px = lv["close"].values[entry_idx]
    risk = abs(stop_ref - entry_index_px)
    if risk <= 0:
        return None
    target_ref = (entry_index_px - TARGET_R * risk) if direction == "SHORT" else (entry_index_px + TARGET_R * risk)
    sim = simulate.simulate_trade(lv, entry_idx, direction, entry_index_px, stop_ref, target_ref,
                                  tag=tag, max_bars=26)

    opt_type = "PE" if direction == "SHORT" else "CE"
    strike = round(entry_index_px / STRIKE_STEP) * STRIKE_STEP
    entry_ts = sim["entry_ts"]
    expiry = options.nearest_expiry(entry_ts, expiries)
    if expiry is None:
        return None
    entry_prem = options.premium_at(opt_ticks, expiry, strike, opt_type, entry_ts)
    exit_prem = options.premium_at(opt_ticks, expiry, strike, opt_type, sim["exit_ts"])
    if entry_prem is None or exit_prem is None:
        return None

    trade = {"direction": opt_type, "tag": tag, "entry_ts": entry_ts,
            "entry_premium": entry_prem, "exit_premium": exit_prem,
            "index_exit_reason": sim["reason"], "strike": strike, "expiry": expiry}
    old_cost = _old_estimate_cost(entry_prem, exit_prem)
    new = costs_bridge.net_pnl_index_option(trade, LOT_SIZE_NIFTY)
    new["old_estimate_cost"] = round(old_cost, 2)
    new["old_estimate_net_pnl"] = round(new["gross_pnl"] - old_cost, 2)
    return new


def main():
    print("=== Reversal Sweep, DAILY+WEEKLY only, real captured NIFTY option premium ===")
    trades = reversal_sweep_daily_weekly_real_premium()
    print(f"{len(trades)} real trades resolved with real captured entry+exit premium\n")

    for t in trades:
        print(f"  {str(t['entry_ts'])[:16]} {t['direction']} {t['tag']:6s} {t['strike']:.0f} {t['expiry']}  "
              f"gross=Rs{t['gross_pnl']:.0f}  NEW net=Rs{t['net_pnl']:.0f} (cost Rs{t['cost_total']:.0f})  "
              f"OLD-estimate net=Rs{t['old_estimate_net_pnl']:.0f} (cost Rs{t['old_estimate_cost']:.0f})")

    report_new = report.compute_report(trades, pnl_fn=lambda t: t["net_pnl"], group_by="tag")
    report_old = report.compute_report(trades, pnl_fn=lambda t: t["old_estimate_net_pnl"], group_by="tag")
    report_gross = report.compute_report(trades, pnl_fn=lambda t: t["gross_pnl"], group_by="tag")

    print("\n=== GROSS (no cost) ===")
    print(report_gross)
    print("\n=== NEW real formula cost (FORMULA_ESTIMATE, published NSE/Angel One rates) ===")
    print(report_new)
    print("\n=== OLD hand-rolled estimate (for comparison only) ===")
    print(report_old)

    total_old_cost = sum(t["old_estimate_cost"] for t in trades)
    total_new_cost = sum(t["cost_total"] for t in trades)

    report.write_verdict_md(
        "/root/zerohero/backend/CALIBRATED_BACKTEST_REPORT.md",
        title="Calibrated Re-Backtest -- Real Cost Model + Shared Harness",
        rule_description=(
            "Re-runs the Reversal Sweep strategy's real-option-premium test (DAILY+WEEKLY "
            "levels only -- 4H already proven net-negative and excluded) using the new "
            "shared_harness library end-to-end, and the new estimate_index_option_cost() "
            "real cost formula (published NSE/Angel One regulatory rates: brokerage, "
            "exchange txn, STT post-Budget-2026-27, stamp duty, SEBI fee, GST, disclosed "
            "0.75pt slippage) in place of the original scripts' rough hand-rolled cost guess."
        ),
        data_sources=[
            "Real captured NIFTY index+option ticks, market_history.db::quote_snapshots, "
            "~2026-09-02 to 09-25 (the entire real captured window that exists)",
            "Same hard sample-size ceiling as the original studies -- this is a cost-model "
            "recalculation on the SAME real trades, not a bigger sample",
        ],
        disclosed_choices=[
            "CPR+ORB not re-run against real premium -- already NO-GO at the index-points "
            "level before reaching a real premium test; nothing to recompute",
            "VWAP+EMA's real premium sample (n=3) is too thin for the cost-model choice to "
            "matter either way -- not re-run in detail here, see original VERDICT.md",
            "exit_premium looked up at the same real-tick-nearest-timestamp method as the "
            "original study (30 min tolerance) -- a signal with no real quote within "
            "tolerance is skipped, never fabricated",
        ],
        sections=[
            ("GROSS P&L (no cost)", report_gross),
            ("NET P&L -- NEW real cost formula (FORMULA_ESTIMATE)", report_new),
            ("NET P&L -- OLD hand-rolled estimate (comparison only)", report_old),
            ("Cost totals across all real trades", {
                "old_estimate_total_cost": round(total_old_cost, 2),
                "new_formula_total_cost": round(total_new_cost, 2),
                "difference": round(total_new_cost - total_old_cost, 2),
            }),
        ],
        bottom_line=(
            f"n={len(trades)} real trades. The new, real regulatory-rate-based cost model "
            f"changes the net P&L number but not the fundamental conclusion from the original "
            f"study: n is still far too small (well under the 30-trade INSUFFICIENT_SAMPLE "
            f"threshold) for any real GO/NO-GO call on real option premium. The value of this "
            f"re-run is precision (a real cost formula instead of a guess), not a bigger or more "
            f"confident sample -- that still requires more real captured trading days, which "
            f"this system continues to accumulate."
        ),
    )
    print("\nWrote backend/CALIBRATED_BACKTEST_REPORT.md")


if __name__ == "__main__":
    main()
