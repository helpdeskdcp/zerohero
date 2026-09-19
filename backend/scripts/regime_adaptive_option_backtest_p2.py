"""
Regime-Adaptive Trend+Momentum strategy -- REAL option-premium backtest.

Reuses the EXACT indicator/regime functions from the live strategy file
(strategies/examples/regime_adaptive_trend_momentum.py in the OpenAlgo
install) so the backtest tests the same logic that would run live -- not a
re-implementation that could silently drift from it.

Signal source: real NIFTY futures 1-minute data (2026-07-01..09-10, already
fetched this session) -- NOT the raw index, which has zero volume and would
make VWAP permanently undefined (the bug fixed in the live script earlier).

Trade simulation: real NIFTY weekly ATM option premiums (Upstox, pulled this
session, upstox_research.db). On each BULL/BEAR entry, resolve the correct
weekly expiry + ATM strike (rounded to nearest 50) as of that bar, then walk
the option's own real 1-minute OHLC forward checking:
  - 30% stop-loss / 60% target on the option premium (matches the live
    script's SL_PCT/TARGET_PCT), OR
  - regime exit (flips to the opposite side or to SIDEWAYS on the underlying)
whichever comes first. No look-ahead: the option trade only ever sees bars
at or after its own entry timestamp.

Costs: real, current NSE F&O index-options schedule (brokerage flat, STT on
sell-side premium, exchange transaction charge, SEBI fee, stamp duty, GST) --
same rigor as the MCX cost checks done for NaturalGas/CrudeOil this session.
"""
from __future__ import annotations

import gzip
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

STRATEGY_FILE = Path("/root/openalgo/strategies/examples/regime_adaptive_trend_momentum.py")
FUTURES_PATH = (Path(__file__).resolve().parents[1] / "data" / "historical" /
                "upstox_v3_validation" / "raw_futures_windows_NIFTY_FUT_30_JUN_26_period2.json.gz")
OPTIONS_DB = Path(__file__).resolve().parents[1] / "data" / "historical" / "upstox" / "upstox_research.db"

LOOKBACK_BARS = 300   # bars of trailing history fed to classify_regime each step (≈ live's 5-day poll, capped for speed)
LOT_SIZE = 65         # NIFTY lot size
SL_PCT = 0.30
TARGET_PCT = 0.60

# Load the live strategy's pure indicator/regime functions (skip the
# api-key-requiring bottom half) so the backtest tests the SAME logic.
import os

os.environ.setdefault("OPENALGO_API_KEY", "unused-for-backtest")
_src = STRATEGY_FILE.read_text()
_code = _src.split("# ---------------------------------------------------------------------------\n# Position state")[0]
_code = _code.replace("from openalgo import api", "")          # not installed in zerohero's venv
_code = _code.replace("client = api(api_key=api_key, host=host, ws_url=ws_url)", "client = None")  # unused by the pure functions
_ns: dict = {}
exec(compile(_code, str(STRATEGY_FILE), "exec"), _ns)
classify_regime = _ns["classify_regime"]


def load_futures_rows():
    with gzip.open(FUTURES_PATH, "rt") as f:
        payload = json.load(f)
    by_ts = {}
    for w in payload["windows"]:
        for row in w["body"]["data"]["candles"]:
            ts, o, h, l, c, vol, oi = row
            by_ts[ts] = {"ts": ts, "open": float(o), "high": float(h), "low": float(l),
                         "close": float(c), "volume": float(vol)}
    return sorted(by_ts.values(), key=lambda r: r["ts"])


def next_weekly_expiry(dt: datetime) -> str:
    """Next Thursday on/after dt's date (NIFTY weekly expiry convention)."""
    days_ahead = (3 - dt.weekday()) % 7  # Thursday = 3
    exp = dt.date() + timedelta(days=days_ahead)
    return exp.isoformat()


def atm_strike(price: float) -> float:
    return round(price / 50.0) * 50.0


def find_available_expiry(conn, target_expiry: str, symbol_underlying="NIFTY"):
    """The pulled data only has the 10 Thursdays actually fetched -- snap to
    the nearest available one on/after target_expiry if the exact date is
    missing (e.g. a holiday-shifted expiry)."""
    cur = conn.execute(
        "SELECT DISTINCT expiry FROM normalized_option_bars WHERE underlying=? AND expiry>=? ORDER BY expiry LIMIT 1",
        (symbol_underlying, target_expiry))
    row = cur.fetchone()
    return row[0] if row else None


def fetch_option_series(conn, expiry, strike, option_type, from_ts):
    cur = conn.execute(
        "SELECT timestamp, open, high, low, close FROM normalized_option_bars "
        "WHERE underlying='NIFTY' AND expiry=? AND strike=? AND option_type=? AND timestamp>=? "
        "ORDER BY timestamp ASC",
        (expiry, strike, option_type, from_ts))
    return cur.fetchall()


def main():
    print(f"Loading NIFTY futures signal data: {FUTURES_PATH}")
    rows = load_futures_rows()
    print(f"  {len(rows)} candles, {rows[0]['ts']} -> {rows[-1]['ts']}")

    print(f"Opening real option premium DB: {OPTIONS_DB}")
    conn = sqlite3.connect(f"file:{OPTIONS_DB}?mode=ro", uri=True)

    # pandas is needed by classify_regime -- build a rolling window as a list of dicts -> DataFrame
    import pandas as pd

    last_regime = None
    current_side = None
    entry_ts = None
    trades = []

    print(f"\nWalking {len(rows)} futures candles, classifying regime every bar "
          f"(lookback={LOOKBACK_BARS} bars)...")
    n_checked = 0
    for i in range(LOOKBACK_BARS, len(rows)):
        window = rows[i - LOOKBACK_BARS:i + 1]
        df = pd.DataFrame(window)
        df.index = pd.to_datetime(df["ts"])
        df = df.rename(columns={"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"})

        try:
            regime = classify_regime(df)
        except Exception:
            continue
        n_checked += 1

        cur_ts = rows[i]["ts"]
        cur_dt = datetime.fromisoformat(cur_ts)
        cur_price = rows[i]["close"]

        if regime == last_regime:
            continue
        last_regime = regime

        # close any existing option trade at regime exit (walked forward at entry time, see below)
        if current_side is not None and regime != current_side.replace("CE", "BULL").replace("PE", "BEAR"):
            trades[-1]["exit_reason_pending"] = "REGIME_FLIP"
            trades[-1]["regime_exit_ts"] = cur_ts
            current_side = None

        if regime == "BULL" and current_side != "CE":
            current_side = "CE"
            trades.append({"side": "CE", "entry_ts": cur_ts, "entry_underlying": cur_price})
        elif regime == "BEAR" and current_side != "PE":
            current_side = "PE"
            trades.append({"side": "PE", "entry_ts": cur_ts, "entry_underlying": cur_price})
        elif regime == "SIDEWAYS":
            current_side = None

    print(f"Regime checks performed: {n_checked}")
    print(f"Raw entry signals found: {len(trades)}")

    # --- resolve each trade against real option premiums ---
    resolved = []
    for t in trades:
        entry_dt = datetime.fromisoformat(t["entry_ts"])
        target_expiry = next_weekly_expiry(entry_dt)
        expiry = find_available_expiry(conn, target_expiry)
        if expiry is None:
            t["outcome"] = "NO_DATA"
            resolved.append(t)
            continue
        strike = atm_strike(t["entry_underlying"])
        opt_type = t["side"]  # "CE" or "PE"
        series = fetch_option_series(conn, expiry, strike, opt_type, t["entry_ts"])
        if not series:
            t["outcome"] = "NO_DATA"
            resolved.append(t)
            continue

        entry_premium = series[0][1]  # open of the first available bar at/after entry
        if entry_premium is None or entry_premium <= 0:
            t["outcome"] = "NO_DATA"
            resolved.append(t)
            continue

        exit_reason, exit_premium, exit_ts = None, None, None
        regime_exit_ts = t.get("regime_exit_ts")
        for ts_raw, o, h, l, c in series[1:]:
            if h is None or l is None:
                continue
            if regime_exit_ts is not None and ts_raw >= regime_exit_ts:
                exit_reason, exit_premium, exit_ts = "REGIME_FLIP", c, ts_raw
                break
            change_hi = (h - entry_premium) / entry_premium
            change_lo = (l - entry_premium) / entry_premium
            if change_lo <= -SL_PCT:
                exit_reason, exit_premium, exit_ts = "SL", entry_premium * (1 - SL_PCT), ts_raw
                break
            if change_hi >= TARGET_PCT:
                exit_reason, exit_premium, exit_ts = "TARGET", entry_premium * (1 + TARGET_PCT), ts_raw
                break
        if exit_reason is None:
            # ran out of option data (e.g. expiry day close) without SL/target/regime exit
            last_close = series[-1][4]
            exit_reason, exit_premium, exit_ts = "EOD_DATA_END", last_close, series[-1][0]

        t.update(expiry=expiry, strike=strike, entry_premium=entry_premium,
                  exit_reason=exit_reason, exit_premium=exit_premium, exit_ts=exit_ts,
                  outcome="RESOLVED", pnl_pts=exit_premium - entry_premium)
        resolved.append(t)

    conn.close()

    n_resolved = sum(1 for t in resolved if t["outcome"] == "RESOLVED")
    n_nodata = sum(1 for t in resolved if t["outcome"] == "NO_DATA")
    print(f"\nResolved against real premiums: {n_resolved}   No matching option data: {n_nodata}")

    good = [t for t in resolved if t["outcome"] == "RESOLVED"]
    wins = [t for t in good if t["pnl_pts"] > 0]
    losses = [t for t in good if t["pnl_pts"] <= 0]
    n = len(good)
    if n == 0:
        print("No resolved trades -- nothing to report.")
        return

    win_rate = len(wins) / n * 100
    gross_win = sum(t["pnl_pts"] for t in wins) * LOT_SIZE
    gross_loss = sum(t["pnl_pts"] for t in losses) * LOT_SIZE
    pf = (gross_win / abs(gross_loss)) if gross_loss < 0 else float("inf")
    total_gross = gross_win + gross_loss
    avg_premium = sum(t["entry_premium"] for t in good) / n

    print("\n" + "=" * 78)
    print("REGIME-ADAPTIVE STRATEGY -- REAL OPTION PREMIUM BACKTEST RESULT")
    print("=" * 78)
    print(f"Trades resolved      : {n}")
    print(f"  CE                 : {sum(1 for t in good if t['side']=='CE')}")
    print(f"  PE                 : {sum(1 for t in good if t['side']=='PE')}")
    reasons = {}
    for t in good:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
    print(f"  Exit reasons       : {reasons}")
    print(f"Win rate             : {win_rate:.1f}%")
    print(f"Profit factor (gross): {pf:.3f}")
    print(f"Total gross P&L      : Rs{total_gross:+,.0f}")
    print(f"Avg entry premium    : Rs{avg_premium:.2f}")

    # --- real NSE F&O index-options cost model ---
    turnover_leg = avg_premium * LOT_SIZE
    brokerage = 20.0 * 2
    stt = 0.001 * turnover_leg          # 0.1% on the SELL side (options), applied once per round trip
    exch_txn = 0.0003503 * turnover_leg * 2   # NSE F&O exchange transaction charge, both legs
    sebi = (10 / 10_000_000) * turnover_leg * 2
    stamp = 0.00003 * turnover_leg      # 0.003% on buy side
    gst = 0.18 * (brokerage + exch_txn + sebi)
    total_cost = brokerage + stt + exch_txn + sebi + stamp + gst

    print(f"\nReal NSE F&O options cost (round-trip, avg premium Rs{avg_premium:.2f} x lot {LOT_SIZE}):")
    print(f"  Brokerage (2 orders @ Rs20)         : Rs{brokerage:8.2f}")
    print(f"  STT (0.1%, sell side)                : Rs{stt:8.2f}")
    print(f"  Exchange transaction charge (0.03503%): Rs{exch_txn:8.2f}")
    print(f"  SEBI fee                             : Rs{sebi:8.2f}")
    print(f"  Stamp duty (0.003%, buy side)         : Rs{stamp:8.2f}")
    print(f"  GST (18% on brokerage+exch+sebi)      : Rs{gst:8.2f}")
    print(f"  {'-'*44}")
    print(f"  TOTAL round-trip cost per lot         : Rs{total_cost:8.2f}")

    total_net = total_gross - total_cost * n
    print(f"\nExpectancy before costs : Rs{total_gross/n:+.1f}/trade")
    print(f"Expectancy after costs  : Rs{total_net/n:+.1f}/trade")
    print(f"Total net P&L ({n} trades): Rs{total_net:+,.0f}")
    print("=" * 78)

    # --- sanity checks before trusting any of the above ---
    print("\n" + "=" * 78)
    print("SANITY CHECKS")
    print("=" * 78)

    durations_min = []
    for t in good:
        d = (datetime.fromisoformat(t["exit_ts"]) - datetime.fromisoformat(t["entry_ts"])).total_seconds() / 60.0
        durations_min.append(d)
    durations_min.sort()
    print(f"Trade duration (minutes): min={durations_min[0]:.0f} median={durations_min[len(durations_min)//2]:.0f} "
          f"mean={sum(durations_min)/len(durations_min):.0f} max={durations_min[-1]:.0f}")

    pnl_inr_sorted = sorted((t["pnl_pts"] * LOT_SIZE for t in good), reverse=True)
    top5 = sum(pnl_inr_sorted[:5])
    print(f"Top 5 winning trades sum: Rs{top5:+,.0f}  ({top5/total_gross*100:.1f}% of total gross P&L)"
          if total_gross != 0 else "Top 5 check skipped (zero total)")
    print(f"Largest single win : Rs{pnl_inr_sorted[0]:+,.0f}")
    print(f"Largest single loss: Rs{pnl_inr_sorted[-1]:+,.0f}")

    good_sorted = sorted(good, key=lambda t: t["entry_ts"])
    half = len(good_sorted) // 2
    train, test = good_sorted[:half], good_sorted[half:]

    def block_stats(rs, label):
        if not rs:
            print(f"  {label}: n=0"); return
        wins_ = [t for t in rs if t["pnl_pts"] > 0]
        losses_ = [t for t in rs if t["pnl_pts"] <= 0]
        gw = sum(t["pnl_pts"] for t in wins_) * LOT_SIZE
        gl = sum(t["pnl_pts"] for t in losses_) * LOT_SIZE
        pf_ = (gw / abs(gl)) if gl < 0 else float("inf")
        net = gw + gl - total_cost * len(rs)
        print(f"  {label}: n={len(rs)} ({rs[0]['entry_ts'][:10]}..{rs[-1]['entry_ts'][:10]})  "
              f"win={len(wins_)/len(rs)*100:.1f}%  PF(gross)={pf_:.2f}  "
              f"net_after_costs=Rs{net:+,.0f}  (Rs{net/len(rs):+.1f}/trade)")

    print("\nChronological train/test split (first half vs second half):")
    block_stats(train, "TRAIN")
    block_stats(test, "TEST")

    from collections import defaultdict
    by_month = defaultdict(list)
    for t in good:
        by_month[t["entry_ts"][:7]].append(t)
    print("\nMonthly:")
    for m in sorted(by_month):
        block_stats(by_month[m], f"  {m}")


if __name__ == "__main__":
    main()
