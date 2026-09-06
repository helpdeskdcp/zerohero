#!/usr/bin/env python3
"""
orderflow_spike_futures_research.py -- RESEARCH ONLY. Tests the ORIGINAL
spike / sideways_spike concept on the CORRECT instrument -- a NIFTY FUTURES
session with GENUINE traded volume and open interest -- instead of the volumeless
cash index.

Frozen: uses the UNCHANGED app/orderflow/smart_money engine. No production /
engine / baseline / trading / execution / broker / live_trading / paper_mode /
test change.

DATA REALITY (Kaggle audit, 2026-09-06):
  Kaggle has NO genuine NIFTY FUTURES intraday (1m/5m) OHLC+volume+OI multi-year
  time series. The ONLY genuine NIFTY-futures intraday data with real volume+OI
  is ranjan15/niftyfutures-tick-by-tick-level-5-depth-data -- ONE session
  (2025-04-28), 1-second LTP+cumVolume+OI snapshots. Everything else intraday is
  the cash index (volume=0) or synthetic (kaalicharan9080, sumansarkar24).

  This script resamples that ONE real session to 5m bars:
    o/h/l/c  = first/max/min/last LTP in the 5-min bucket
    v        = delta of the cumulative traded-volume field over the bucket
               (aggregation of a real feed -- NOT synthetic, NOT reconstructed)
    oi       = last OI in the bucket ;  d_oi = OI change over the bucket
  No aggressor / order-flow inference. No OHLC->tick conversion.

  N = 1 session  ->  NO validation gate can pass  ->  VERDICT = NOT VALIDATED.
  What it DOES show: with real volume the spike gate CAN fire (it is structurally
  0 on the cash index). That is the only claim.

Outputs:
  data/orderflow_spike_futures_research.csv
  stdout report
"""
from __future__ import annotations

import csv as _csv
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.orderflow import smart_money as SM

SRC = (Path(__file__).resolve().parents[1] / "data" / "historical" / "kaggle" /
       "ranjan15__niftyfutures-tick-by-tick-level-5-depth-data" / "data.csv")
RR, VOL_MULT, STOP_FRAC = 3.0, 2.0, 1.0


def resample_5m():
    rows = list(_csv.DictReader(SRC.open()))
    buckets: dict = {}
    for r in rows:
        try:
            t = datetime.strptime(r["ist_datetime"], "%Y-%m-%d %H:%M:%S")
            ltp = float(r["ltp"]); vol = float(r["volume"]); oi = float(r["oi"])
        except (ValueError, KeyError):
            continue
        key = t.replace(minute=(t.minute // 5) * 5, second=0)
        b = buckets.setdefault(key, {"ltps": [], "vol_first": vol, "vol_last": vol,
                                     "oi_first": oi, "oi_last": oi})
        b["ltps"].append(ltp)
        b["vol_last"] = vol
        b["oi_last"] = oi
    out = []
    for key in sorted(buckets):
        b = buckets[key]
        L = b["ltps"]
        out.append({
            "bar_start": key.strftime("%Y-%m-%dT%H:%M:%S"),
            "o": L[0], "h": max(L), "l": min(L), "c": L[-1],
            "v": max(0.0, b["vol_last"] - b["vol_first"]),
            "oi": b["oi_last"], "d_oi": b["oi_last"] - b["oi_first"],
        })
    return out


def _legs(bars, pattern, extra=None):
    out = SM.smart_money_setups(bars, volume_mult=VOL_MULT, rr=RR, stop_frac=STOP_FRAC,
                                trail=False, sig_filter="none", pattern=pattern)
    if out.get("status") != "OK":
        return [], out.get("status")
    by_ts = {b["bar_start"]: b for b in bars}
    rows = []
    for su in out["setups"]:
        cts = su["candle"]["bar_start"]
        bar = by_ts.get(cts, {})
        if extra and not extra(bar, su):
            continue
        for sk in ("buy", "sell"):
            leg = su.get(sk)
            if not leg:
                continue
            oc = leg["outcome"]
            rows.append({"candle_ts": cts, "pattern": pattern + (("+" + extra.__name__) if extra else ""),
                         "side": leg["side"], "entry": leg["entry"], "stop_loss": leg["stop_loss"],
                         "target": leg["target"], "risk_points": leg["risk_points"],
                         "d_oi": bar.get("d_oi"), "bar_vol": bar.get("v"),
                         "result": oc["status"], "points": oc["points"]})
    return rows, "OK"


def vol_expansion(bar, su):
    """spike bar range also >= 1.5x the setup's session avg range (proxy for a
    volatility/volume-expansion candle)."""
    return (su.get("range_points") or 0) >= 1.5 * (su.get("_avg_range") or 0) if su.get("_avg_range") else True


def oi_confirm(bar, su):
    """OI rose over the spike bar in the trade's direction of travel (long spike
    -> OI up = new longs; short spike -> OI up = new shorts). Uses only the
    completed spike bar's own d_oi."""
    d = bar.get("d_oi")
    if d is None:
        return False
    up = su["candle"]["c"] >= su["candle"]["o"]
    return (d > 0) if up else (d > 0)   # OI expansion on the break, either side


def agg(rows):
    res = [r for r in rows if r["result"] in ("TARGET_HIT", "STOP_HIT")]
    n = len(res)
    wins = [r for r in res if r["result"] == "TARGET_HIT"]
    losses = [r for r in res if r["result"] == "STOP_HIT"]
    gw = sum(r["points"] for r in wins); gl = -sum(r["points"] for r in losses)
    pts = [r["points"] for r in res]
    cum = peak = dd = 0.0
    for r in sorted(res, key=lambda r: r["candle_ts"]):
        cum += r["points"]; peak = max(peak, cum); dd = min(dd, cum - peak)
    mcl = cur = 0
    for r in sorted(res, key=lambda r: r["candle_ts"]):
        cur = cur + 1 if r["result"] == "STOP_HIT" else 0
        mcl = max(mcl, cur)
    return {"signals": len(rows), "resolved": n, "wins": len(wins), "losses": len(losses),
            "open": len(rows) - n,
            "win_rate": round(len(wins) / n, 4) if n else None,
            "expectancy_pts": round(st.fmean(pts), 3) if pts else None,
            "profit_factor": round(gw / gl, 3) if gl > 0 else None,
            "net_pts": round(sum(pts), 1) if pts else 0.0,
            "max_dd_pts": round(dd, 1), "max_consec_losses": mcl}


def line(name, m):
    if not m["signals"]:
        return f"  {name:<34} signals=0"
    return (f"  {name:<34} sig={m['signals']:>3} resolved={m['resolved']:>3} "
            f"W/L={m['wins']}/{m['losses']} open={m['open']} "
            f"win%={(m['win_rate'] or 0)*100:>5.1f} E[pts]={m['expectancy_pts']} "
            f"PF={m['profit_factor']} net={m['net_pts']} maxDD={m['max_dd_pts']} maxCL={m['max_consec_losses']}")


def main():
    bars = resample_5m()
    p = print
    p("=" * 100)
    p("SPIKE / SIDEWAYS_SPIKE CONCEPT ON GENUINE NIFTY *FUTURES* DATA (real volume + OI)")
    p("RESEARCH ONLY. Unchanged smart_money engine. No production / engine / baseline / test change.")
    p("=" * 100)
    p(f"\nDATA: ranjan15 NIFTY-futures session 2025-04-28, resampled to 5m -> {len(bars)} bars.")
    p(f"  session traded volume = {bars[-1]['oi'] and sum(b['v'] for b in bars):,.0f}  "
      f"OI {bars[0]['oi']:,.0f} -> {bars[-1]['oi']:,.0f}")
    p(f"  bar volume: min {min(b['v'] for b in bars):,.0f}  median {st.median(b['v'] for b in bars):,.0f}  "
      f"max {max(b['v'] for b in bars):,.0f}   (NON-zero -> the spike gate CAN fire)")
    # attach avg range for the vol_expansion filter
    avg_rng = st.fmean(b["h"] - b["l"] for b in bars)

    all_rows = []
    p("\n--- variants ---")
    for name, pat, extra in (
        ("spike", "spike", None),
        ("sideways_spike", "sideways_spike", None),
        ("hammer", "hammer", None),
        ("hammer + (also a spike bar)", "hammer", None),   # handled below via intersection
        ("spike + vol_expansion", "spike", vol_expansion),
        ("spike + oi_confirm", "spike", oi_confirm),
    ):
        if name == "hammer + (also a spike bar)":
            # intersection: hammer candles that are ALSO volume spikes
            ham, _ = _legs(bars, "hammer")
            spk, _ = _legs(bars, "spike")
            spk_ts = {r["candle_ts"] for r in spk}
            rows = [r for r in ham if r["candle_ts"] in spk_ts]
        else:
            for b in bars:
                pass
            # inject avg range so vol_expansion has a reference
            def _wrap(fn):
                if fn is None:
                    return None
                def g(bar, su):
                    su["_avg_range"] = avg_rng
                    return fn(bar, su)
                g.__name__ = fn.__name__
                return g
            rows, stt = _legs(bars, pat, _wrap(extra))
        all_rows.extend(rows)
        p(line(name, agg(rows)))

    outp = Path(__file__).resolve().parents[1] / "data" / "orderflow_spike_futures_research.csv"
    with outp.open("w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=["candle_ts", "pattern", "side", "entry", "stop_loss",
                                           "target", "risk_points", "d_oi", "bar_vol", "result", "points"])
        w.writeheader(); w.writerows(all_rows)

    p("\n" + "=" * 100)
    p("[VERDICT]")
    p("=" * 100)
    spk = agg([r for r in all_rows if r["pattern"] == "spike"])
    p(f"  spike gate FIRES on genuine-volume futures data ({spk['signals']} legs) -- structurally")
    p("  impossible on the cash index (0). So the spike/sideways_spike concept is NOT invalid; it")
    p("  simply requires the correct instrument.")
    p("  BUT N = 1 session -> NO train/validation/OOS/HOLDOUT, NO regime split, NO statistical")
    p("  power. Every pre-declared validation gate FAILS on sample size alone.")
    p("  ==> VERDICT = NOT VALIDATED. Nothing PROVEN. No rule optimised, no promotion to production.")
    p("  Kaggle has no multi-session NIFTY-futures intraday OHLC+volume+OI dataset; a real answer")
    p("  needs that data (paid vendor, or the zerohero histcap capturing the NIFTY future).")
    p(f"\nwrote {len(all_rows)} rows -> {outp}")


if __name__ == "__main__":
    main()
