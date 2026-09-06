#!/usr/bin/env python3
"""
orderflow_smartmoney_kaggle_nifty.py -- RESEARCH / EVALUATION ONLY.

Runs the EXISTING, UNCHANGED smart-money trigger-candle breakout engine
(app/orderflow/smart_money.smart_money_setups) over the Kaggle NIFTY 50 5-minute
CASH-INDEX series (debashis74017/nifty-50-minute-data, 2015-2026) and aggregates
the same way app/orderflow/backtest does.

Does NOT modify smart_money.py / backtest.py / service.py / api.py or anything on
the trading / execution / broker path. live_trading stays false, paper_mode
stays true.

IMPORTANT -- this is a PRICE-SHAPE breakout backtest, NOT order flow:
  * NIFTY 50 cash index has NO volume -> the "spike" / "sideways_spike" patterns
    (which gate on volume) produce ZERO signals here. Only "hammer" (pure candle
    shape) is meaningful.
  * cash index, not the NIFTY FUTURE (the tradable contract differs).
  * no bid/ask, no depth, no OI, no aggressor -> real order flow is UNOBSERVABLE.
  * option-premium P&L is NOT modelled (Stage-4: the index edge does not survive
    ATM spread + theta).

Outputs:
  data/orderflow_smartmoney_kaggle_nifty.csv   (one row per resolved trade leg)
  stdout report
"""
from __future__ import annotations

import csv as _csv
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.orderflow import smart_money as SM
from scripts.orderflow_h1h7_performance import _load_kaggle_nifty

PATTERNS = ("hammer", "spike", "sideways_spike")
RR = 3.0
VOL_MULT = 2.0
STOP_FRAC = 1.0


def _legs_for_session(date, bars, pattern):
    out = SM.smart_money_setups(bars, volume_mult=VOL_MULT, rr=RR,
                                stop_frac=STOP_FRAC, trail=False,
                                sig_filter="none", pattern=pattern)
    rows = []
    if out.get("status") != "OK":
        return rows, out.get("status"), out.get("reason")
    for su in out["setups"]:
        for side_key in ("buy", "sell"):
            leg = su.get(side_key)
            if not leg:
                continue
            oc = leg["outcome"]
            rows.append({
                "session": date,
                "year": date[:4],
                "candle_ts": su["candle"]["bar_start"],
                "pattern": pattern,
                "side": leg["side"],
                "entry": leg["entry"],
                "stop_loss": leg["stop_loss"],
                "target": leg["target"],
                "risk_points": leg["risk_points"],
                "reward_points": leg["reward_points"],
                "rr": leg["rr"],
                "breakout_bar": leg["breakout_bar"],
                "result": oc["status"],
                "exit_price": oc["exit_price"],
                "points": oc["points"],
            })
    return rows, "OK", None


def agg(rows):
    resolved = [r for r in rows if r["result"] in ("TARGET_HIT", "STOP_HIT")]
    n_all = len(rows)
    n = len(resolved)
    wins = [r for r in resolved if r["result"] == "TARGET_HIT"]
    losses = [r for r in resolved if r["result"] == "STOP_HIT"]
    triggered = [r for r in rows if r["result"] == "TRIGGERED"]   # broke out, unresolved EOD
    pending = [r for r in rows if r["result"] == "PENDING"]       # never broke out
    gw = sum(r["points"] for r in wins)
    gl = -sum(r["points"] for r in losses)
    pts = [r["points"] for r in resolved]
    # chronological max drawdown on realised points
    cum = peak = dd = 0.0
    for r in sorted(resolved, key=lambda r: r["candle_ts"]):
        cum += r["points"]
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    # max consecutive losses
    mcl = cur = 0
    for r in sorted(resolved, key=lambda r: r["candle_ts"]):
        if r["result"] == "STOP_HIT":
            cur += 1; mcl = max(mcl, cur)
        else:
            cur = 0
    return {
        "signals": n_all, "resolved": n, "wins": len(wins), "losses": len(losses),
        "triggered_open": len(triggered), "pending_nobreak": len(pending),
        "win_rate": round(len(wins) / n, 4) if n else None,
        "gross_win_pts": round(gw, 1), "gross_loss_pts": round(gl, 1),
        "net_pts": round(sum(pts), 1) if pts else 0.0,
        "expectancy_pts": round(st.fmean(pts), 3) if pts else None,
        "profit_factor": round(gw / gl, 3) if gl > 0 else None,
        "max_dd_pts": round(dd, 1),
        "max_consec_losses": mcl,
        "avg_win_pts": round(st.fmean([r["points"] for r in wins]), 2) if wins else None,
        "avg_loss_pts": round(st.fmean([r["points"] for r in losses]), 2) if losses else None,
        "sessions": len({r["session"] for r in rows}),
    }


def _line(name, m):
    if not m or not m["signals"]:
        return f"  {name:<26} signals=0"
    return (f"  {name:<26} sig={m['signals']:>5} resolved={m['resolved']:>5} "
            f"W/L={m['wins']}/{m['losses']} open={m['triggered_open']} nobreak={m['pending_nobreak']} "
            f"win%={(m['win_rate'] or 0)*100:>5.1f} E[pts]={m['expectancy_pts']} "
            f"PF={m['profit_factor']} net={m['net_pts']:>9.1f} maxDD={m['max_dd_pts']:>9.1f} "
            f"maxCL={m['max_consec_losses']} avgW={m['avg_win_pts']} avgL={m['avg_loss_pts']}")


def main():
    data = _load_kaggle_nifty()
    dates = sorted(data)
    p = print
    p("=" * 104)
    p("SMART-MONEY TRIGGER-CANDLE BREAKOUT BACKTEST -- Kaggle NIFTY 50 5m CASH INDEX (debashis74017)")
    p("RESEARCH ONLY. Unchanged engine (app/orderflow/smart_money). No trading / execution / prod change.")
    p("PRICE-SHAPE breakout only -- NOT order flow. Cash index (no volume, no future, no depth, no")
    p("aggressor). Points are INDEX points; option-premium P&L NOT modelled (Stage-4).")
    p("=" * 104)
    p(f"\nDATA: {len(dates)} NIFTY sessions  {dates[0]} .. {dates[-1]}  "
      f"(rr={RR}, stop_frac={STOP_FRAC}, volume_mult={VOL_MULT}, sig_filter=none)")

    all_rows = []
    for pat in PATTERNS:
        rows = []
        statuses = {}
        for d in dates:
            r, stt, why = _legs_for_session(d, data[d], pat)
            rows.extend(r)
            statuses[stt] = statuses.get(stt, 0) + 1
        all_rows.extend(rows)
        p("\n" + "-" * 104)
        p(f"[pattern = {pat}]  session status counts: {statuses}")
        p("-" * 104)
        m = agg(rows)
        p(_line("POOLED", m))
        if not rows:
            if pat in ("spike", "sideways_spike"):
                p("  -> 0 signals as expected: NIFTY 50 is a CASH INDEX with no volume; the volume")
                p("     spike gate can never fire. (This is why the H1/H7 engine is volume-free.)")
            continue
        # per side
        for side in ("BUY", "SELL"):
            p(_line(f"  side={side}", agg([r for r in rows if r["side"] == side])))
        # per year (regime/time proxy)
        p("  by year:")
        for y in sorted({r["year"] for r in rows}):
            p("  " + _line(f"    {y}", agg([r for r in rows if r["year"] == y])))
        # chronological split by session-date rank
        n = len(dates)
        a, b, c = int(n*0.45), int(n*0.65), int(n*0.82)
        tag = {d: ("TRAIN" if i < a else "VALIDATION" if i < b else "OOS" if i < c else "HOLDOUT")
               for i, d in enumerate(dates)}
        p("  chronological split:")
        for spl in ("TRAIN", "VALIDATION", "OOS", "HOLDOUT"):
            p("  " + _line(f"    {spl}", agg([r for r in rows if tag[r["session"]] == spl])))

    out_csv = Path(__file__).resolve().parents[1] / "data" / "orderflow_smartmoney_kaggle_nifty.csv"
    with out_csv.open("w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=["session", "year", "candle_ts", "pattern", "side",
                                           "entry", "stop_loss", "target", "risk_points",
                                           "reward_points", "rr", "breakout_bar", "result",
                                           "exit_price", "points"])
        w.writeheader()
        w.writerows(all_rows)

    p("\n" + "=" * 104)
    p("[VERDICT]")
    p("=" * 104)
    ham = agg([r for r in all_rows if r["pattern"] == "hammer"])
    p(f"  Only the 'hammer' (volume-free) pattern produces signals on this data: "
      f"{ham['signals']} legs / {ham['resolved']} resolved over {ham['sessions']} sessions,")
    p(f"  win {(ham['win_rate'] or 0)*100:.1f}%  E[pts] {ham['expectancy_pts']}  PF {ham['profit_factor']}  "
      f"net {ham['net_pts']} pts  maxDD {ham['max_dd_pts']} pts.")
    p("  'spike' / 'sideways_spike' = 0 signals (cash index, no volume).")
    p("  This is a PRICE-SHAPE breakout study on the NIFTY INDEX -- it is NOT an order-flow edge,")
    p("  NOT the NIFTY future, NOT option P&L, and is NOT statistically validated as a trading")
    p("  signal. Nothing PROVEN. No engine / baseline / production change.")
    p(f"\nwrote {len(all_rows)} trade-leg rows -> {out_csv}")


if __name__ == "__main__":
    main()
