#!/usr/bin/env python3
"""
orderflow_spike_backtest_histcap.py -- RESEARCH ONLY.

Runs the ORIGINAL spike / sideways_spike concept on histcap-captured NIFTY
*FUTURES* 5-minute bars (real traded volume) + polled OI, using the UNCHANGED
app/orderflow/smart_money engine. This is the "correct instrument, genuine
volume/OI" test the cash-index and 1-session Kaggle runs could not do.

FROZEN: no engine / H1-H7 / Stage-6 baseline / trading / execution / broker /
live_trading / paper_mode / test / production change. Rules are NOT optimised.

DATA:
  bars = market_hub.session_bars('NIFTY', date)  -> the captured NIFTY index
         FUTURE 5m OHLCV (market_hub prefers FUTURE over the volumeless INDEX).
         v is real broker candle volume.
  oi   = last quote_snapshots.oi at/<= each bar's close (~26-30s poll);
         d_oi = OI change across the bar. Aggregation of a real feed -- NOT
         synthetic, NOT reconstructed. No order-flow / aggressor inference.

Gate: >=40 sessions AND >=50 spike signals AND >=2 regimes AND OOS+HOLDOUT
positive expectancy on >=20 events each. Until then -> NOT VALIDATED.

Outputs:
  data/orderflow_spike_backtest_histcap_nifty_fut.csv
  stdout report
"""
from __future__ import annotations

import csv as _csv
import sqlite3
import statistics as st
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import market_hub
from app.orderflow import smart_money as SM
from scripts.orderflow_smartmoney_kaggle_nifty import agg, _line
from scripts.orderflow_h1h7_performance import _regime, FR_TRAIN, FR_VAL, FR_OOS

SYM = "NIFTY"
RR, VOL_MULT, STOP_FRAC = 3.0, 2.0, 1.0
_HDB = str(Path(__file__).resolve().parents[1] / "data" / "market_history.db")


def _oi_series(date):
    """[(ts_dt, oi)] for NIFTY FUTURE on `date`, sorted, from quote_snapshots."""
    con = sqlite3.connect(f"file:{_HDB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT received_ts, oi FROM quote_snapshots WHERE symbol=? AND kind='FUTURE' "
        "AND oi IS NOT NULL AND session_date_ist=? ORDER BY received_ts", (SYM, date)).fetchall()
    con.close()
    out = []
    for ts, oi in rows:
        try:
            out.append((datetime.fromisoformat(ts.replace("Z", "+00:00")), float(oi)))
        except (ValueError, AttributeError):
            pass
    return out


def _asof(series, t):
    lo, hi, res = 0, len(series) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= t:
            res = series[mid][1]; lo = mid + 1
        else:
            hi = mid - 1
    return res


def _bars_with_oi(date):
    bars = market_hub.session_bars(SYM, date)
    if not bars:
        return []
    oi = _oi_series(date)
    for b in bars:
        try:
            t0 = datetime.fromisoformat(b["bar_start"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            b["oi"] = b["d_oi"] = None
            continue
        b["oi"] = _asof(oi, t0 + timedelta(minutes=5)) if oi else None
        o0 = _asof(oi, t0) if oi else None
        b["d_oi"] = (b["oi"] - o0) if (b["oi"] is not None and o0 is not None) else None
    return bars


def _legs(bars, pattern, extra=None, avg_rng=None):
    out = SM.smart_money_setups(bars, volume_mult=VOL_MULT, rr=RR, stop_frac=STOP_FRAC,
                                trail=False, sig_filter="none", pattern=pattern)
    if out.get("status") != "OK":
        return [], out.get("status")
    by_ts = {b["bar_start"]: b for b in bars}
    rows = []
    for su in out["setups"]:
        bar = by_ts.get(su["candle"]["bar_start"], {})
        if extra:
            su["_avg_range"] = avg_rng
            if not extra(bar, su):
                continue
        for sk in ("buy", "sell"):
            leg = su.get(sk)
            if not leg:
                continue
            oc = leg["outcome"]
            rows.append({"candle_ts": su["candle"]["bar_start"],
                         "pattern": pattern + (("+" + extra.__name__) if extra else ""),
                         "side": leg["side"], "entry": leg["entry"], "stop_loss": leg["stop_loss"],
                         "target": leg["target"], "risk_points": leg["risk_points"],
                         "bar_vol": bar.get("v"), "d_oi": bar.get("d_oi"),
                         "result": oc["status"], "points": oc["points"]})
    return rows, "OK"


def vol_expansion(bar, su):
    ar = su.get("_avg_range")
    return (su.get("range_points") or 0) >= 1.5 * ar if ar else True


def oi_confirm(bar, su):
    d = bar.get("d_oi")
    return d is not None and d > 0        # OI expands on the break (new positions)


def main():
    dates = sorted(market_hub.session_dates(SYM, limit=400))
    p = print
    p("=" * 100)
    p("SPIKE / SIDEWAYS_SPIKE BACKTEST -- histcap NIFTY *FUTURES* 5m (real volume) + polled OI")
    p("RESEARCH ONLY. Unchanged smart_money engine. No engine/baseline/trading/execution/test change.")
    p("=" * 100)
    p(f"\nDATA: {len(dates)} NIFTY-FUTURE sessions  {dates[0] if dates else '-'} .. {dates[-1] if dates else '-'}")

    per_session = {}
    all_rows = []
    for d in dates:
        bars = _bars_with_oi(d)
        if len(bars) < 12:
            continue
        reg = _regime(bars)
        avg_rng = st.fmean(b["h"] - b["l"] for b in bars)
        per_session[d] = {"regime": reg, "bars": len(bars)}
        for name, pat, extra in (
            ("spike", "spike", None),
            ("sideways_spike", "sideways_spike", None),
            ("hammer", "hammer", None),
            ("spike+vol_expansion", "spike", vol_expansion),
            ("spike+oi_confirm", "spike", oi_confirm),
        ):
            r, _ = _legs(bars, pat, extra, avg_rng)
            for row in r:
                row["session"] = d
                row["regime"] = reg
                row["variant"] = name
            all_rows.extend(r)
        # hammer that is also a spike bar
        ham, _ = _legs(bars, "hammer")
        spk_ts = {x["candle_ts"] for x in _legs(bars, "spike")[0]}
        for row in ham:
            if row["candle_ts"] in spk_ts:
                row["session"] = d; row["regime"] = reg; row["variant"] = "hammer+spike"
                all_rows.append(row)

    n_sess = len(per_session)
    regimes = {}
    for v in per_session.values():
        regimes[v["regime"]] = regimes.get(v["regime"], 0) + 1
    p(f"  usable (>=12 bars): {n_sess} sessions | regimes {regimes}")

    tags = {}
    ds = sorted(per_session)
    a, b, c = int(len(ds) * FR_TRAIN), int(len(ds) * FR_VAL), int(len(ds) * FR_OOS)
    for i, d in enumerate(ds):
        tags[d] = "TRAIN" if i < a else "VALIDATION" if i < b else "OOS" if i < c else "HOLDOUT"

    VARIANTS = ("spike", "sideways_spike", "hammer", "hammer+spike",
                "spike+vol_expansion", "spike+oi_confirm")
    p("\n--- POOLED ---")
    for v in VARIANTS:
        p(_line(v, agg([r for r in all_rows if r["variant"] == v])))
    p("\n--- by regime (spike) ---")
    for reg in ("TREND_UP", "TREND_DOWN", "CHOP"):
        p(_line(f"spike / {reg}", agg([r for r in all_rows
                if r["variant"] == "spike" and r["regime"] == reg])))
    p("\n--- chronological split (spike) ---")
    for spl in ("TRAIN", "VALIDATION", "OOS", "HOLDOUT"):
        p(_line(f"spike / {spl}", agg([r for r in all_rows
                if r["variant"] == "spike" and tags.get(r["session"]) == spl])))

    outp = Path(__file__).resolve().parents[1] / "data" / "orderflow_spike_backtest_histcap_nifty_fut.csv"
    with outp.open("w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=["session", "regime", "variant", "pattern", "candle_ts",
                                           "side", "entry", "stop_loss", "target", "risk_points",
                                           "bar_vol", "d_oi", "result", "points"],
                            extrasaction="ignore")
        w.writeheader(); w.writerows(all_rows)

    spk = agg([r for r in all_rows if r["variant"] == "spike"])
    spk_oos = agg([r for r in all_rows if r["variant"] == "spike" and tags.get(r["session"]) == "OOS"])
    spk_hold = agg([r for r in all_rows if r["variant"] == "spike" and tags.get(r["session"]) == "HOLDOUT"])
    gate = {
        ">=40 sessions": n_sess >= 40,
        ">=50 spike signals": spk["signals"] >= 50,
        ">=2 regimes": len(regimes) >= 2,
        "OOS >=20 & E[pts]>0": spk_oos["resolved"] >= 20 and (spk_oos["expectancy_pts"] or -9) > 0,
        "HOLDOUT >=20 & E[pts]>0": spk_hold["resolved"] >= 20 and (spk_hold["expectancy_pts"] or -9) > 0,
    }
    p("\n" + "=" * 100)
    p("[GATE]")
    for k, ok in gate.items():
        p(f"   [{'PASS' if ok else 'FAIL'}] {k}")
    p("=" * 100)
    if all(gate.values()):
        p("  all gates PASS -- report the numbers; still not 'PROVEN' without an untouched holdout.")
    else:
        p(f"  ==> VERDICT = NOT VALIDATED. spike signals={spk['signals']} over {n_sess} sessions.")
        p("      Insufficient captured NIFTY-futures history. Nothing PROVEN. No rule changed,")
        p("      no promotion to production. Re-runs automatically once >=40 fresh sessions exist.")
    p(f"\nwrote {len(all_rows)} rows -> {outp}")


if __name__ == "__main__":
    main()
