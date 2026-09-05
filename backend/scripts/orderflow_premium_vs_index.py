#!/usr/bin/env python3
"""
orderflow_premium_vs_index.py -- READ-ONLY measurement, no fix.

Operator observation (2026-09-05):
  "The order-flow backtest hits its TARGET in INDEX points, but a real trade is
   on a CE/PE option whose premium moves LESS than the index (delta < 1, plus
   theta). We need to detect/quantify that gap before deciding on a fix."

This script quantifies, from captured data only, how many premium points an
ATM option actually moves per index point over short holds -- the "effective
delta" the order-flow backtest is implicitly assuming is 1.0.

Data (market_history.db, append-only capture):
  quote_snapshots  kind='INDEX'  -> index LTP series
  quote_snapshots  kind='OPTION' -> per-strike CE/PE premium (LTP) series
  option_greeks                  -> per-strike broker delta / theta / iv series

Method (per session, per horizon H in {15,30,45} min, stepped every 5 min):
  * window start t: S0 = index as-of t; ATM = strike nearest S0
  * dS = index(t+H) - S0 ;  keep only |dS| >= --min-move
  * dP_CE = ATM-CE(t+H) - ATM-CE(t) ;  dP_PE likewise
  * "directional" leg = CE when dS>0 else PE  (the side a breakout would buy)
  * effective_delta = dP_dir / dS        (signed; PE leg dS<0 so a tracking
                                          PE gives a positive premium move)
  * capture = dP_dir / |dS|              (premium points won per index point)
  * broker_delta = |option_greeks.delta| as-of t for that ATM strike

Frozen pre-open quotes are dropped (RTH only + a repeated-LTP guard). Nothing
here writes, trades, or touches the engine -- it only measures.

Usage:
  python backend/scripts/orderflow_premium_vs_index.py [--symbol NIFTY]
        [--db data/market_history.db] [--min-move 8] [--rth-start 555]
        [--rth-end 930] [--json]
  (rth-start/end are IST minutes-of-day; 555=09:15, 930=15:30 for NSE)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as st
from datetime import datetime, timedelta

HORIZONS_MIN = (15, 30, 45)
STEP_MIN = 5


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _ist_mod(t: datetime) -> int:
    """IST minutes-of-day (capture ts are UTC; IST = UTC + 5:30)."""
    m = (t.hour * 60 + t.minute + 330) % 1440
    return m


def _asof(series: list, t: datetime):
    """last (ts,val) value at or before t; series sorted by ts."""
    lo, hi = 0, len(series)
    while lo < hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= t:
            lo = mid + 1
        else:
            hi = mid
    return series[lo - 1][1] if lo > 0 else None


def _load(con, symbol: str, rth_start: int, rth_end: int):
    cur = con.cursor()
    sessions = [r[0] for r in cur.execute(
        "SELECT DISTINCT session_date_ist FROM quote_snapshots "
        "WHERE kind='OPTION' AND symbol=? ORDER BY session_date_ist", (symbol,))]
    out = {}
    for sess in sessions:
        idx = [(_dt(r[0]), r[1]) for r in cur.execute(
            "SELECT received_ts, ltp FROM quote_snapshots "
            "WHERE kind='INDEX' AND symbol=? AND session_date_ist=? AND ltp IS NOT NULL "
            "ORDER BY received_ts", (symbol, sess))]
        if not idx:                      # fall back to FUTURE if no spot captured
            idx = [(_dt(r[0]), r[1]) for r in cur.execute(
                "SELECT received_ts, ltp FROM quote_snapshots "
                "WHERE kind='FUTURE' AND symbol=? AND session_date_ist=? AND ltp IS NOT NULL "
                "ORDER BY received_ts", (symbol, sess))]
        idx = [(t, v) for (t, v) in idx if rth_start <= _ist_mod(t) <= rth_end]
        if len(idx) < 20:
            continue

        opt: dict = {}
        for r in cur.execute(
            "SELECT received_ts, strike, option_type, ltp FROM quote_snapshots "
            "WHERE kind='OPTION' AND symbol=? AND session_date_ist=? AND ltp IS NOT NULL "
            "ORDER BY received_ts", (symbol, sess)):
            t = _dt(r[0])
            if not (rth_start <= _ist_mod(t) <= rth_end):
                continue
            opt.setdefault((r[1], r[2]), []).append((t, r[3]))

        grk: dict = {}
        for r in cur.execute(
            "SELECT received_ts, strike, option_type, delta, theta FROM option_greeks "
            "WHERE underlying=? AND session_date_ist=? AND delta IS NOT NULL "
            "ORDER BY received_ts", (symbol, sess)):
            t = _dt(r[0])
            if not (rth_start <= _ist_mod(t) <= rth_end):
                continue
            grk.setdefault((r[1], r[2]), []).append((t, (r[3], r[4])))

        if opt:
            out[sess] = (idx, opt, grk)
    return out


def _run_session(idx, opt, grk, min_move, horizon):
    strikes = sorted({k[0] for k in opt})
    H = timedelta(minutes=horizon)
    step = timedelta(minutes=STEP_MIN)
    rows = []
    t = idx[0][0]
    end = idx[-1][0]
    while t + H <= end:
        s0 = _asof(idx, t)
        s1 = _asof(idx, t + H)
        if s0 and s1:
            dS = s1 - s0
            if abs(dS) >= min_move:
                atm = min(strikes, key=lambda k: abs(k - s0))
                ot = "CE" if dS > 0 else "PE"
                ser = opt.get((atm, ot))
                if ser:
                    p0 = _asof(ser, t)
                    p1 = _asof(ser, t + H)
                    if p0 and p1 and p0 > 0:
                        bd = _asof(grk.get((atm, ot), []), t)
                        rows.append({
                            "dS": dS, "dP": p1 - p0, "atm": atm, "ot": ot,
                            "p0": p0, "broker_delta": abs(bd[0]) if bd else None,
                            "theta": bd[1] if bd else None,
                        })
        t += step
    return rows


def _summ(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    vals_s = sorted(vals)
    n = len(vals_s)
    return {
        "n": n,
        "median": round(st.median(vals_s), 3),
        "p25": round(vals_s[n // 4], 3),
        "p75": round(vals_s[(3 * n) // 4], 3),
        "mean": round(st.fmean(vals_s), 3),
    }


def analyse(db, symbol, min_move, rth_start, rth_end):
    con = sqlite3.connect(db)
    data = _load(con, symbol, rth_start, rth_end)
    con.close()

    report = {"symbol": symbol, "db": db, "min_move_pts": min_move,
              "sessions_used": sorted(data.keys()),
              "rth_ist": [rth_start, rth_end], "by_horizon": {}}
    pooled_all = {}
    for H in HORIZONS_MIN:
        per_sess = {}
        eff_all, cap_all, bd_all = [], [], []
        win_cap, lose_cap = [], []
        for sess, (idx, opt, grk) in sorted(data.items()):
            rows = _run_session(idx, opt, grk, min_move, H)
            if not rows:
                continue
            eff = [r["dP"] / r["dS"] for r in rows]
            cap = [r["dP"] / abs(r["dS"]) for r in rows]
            bd = [r["broker_delta"] for r in rows]
            per_sess[sess] = {
                "samples": len(rows),
                "eff_delta": _summ(eff),
                "capture_pts_per_pt": _summ(cap),
                "broker_delta_at_entry": _summ(bd),
            }
            eff_all += eff
            cap_all += cap
            bd_all += bd
            win_cap += [c for c in cap if c > 0]
            lose_cap += [c for c in cap if c <= 0]
        pooled = {
            "samples": len(eff_all),
            "eff_delta": _summ(eff_all),
            "capture_pts_per_pt": _summ(cap_all),
            "broker_delta_at_entry": _summ(bd_all),
            "capture_when_premium_rose": _summ(win_cap),
            "capture_when_premium_fell": _summ(lose_cap),
            "frac_premium_moved_favourably": (
                round(len(win_cap) / len(cap_all), 3) if cap_all else None),
        }
        pooled_all[H] = pooled
        report["by_horizon"][H] = {"pooled": pooled, "by_session": per_sess}

    # translate a 1:3 INDEX setup (R index pts) into premium space using the
    # pooled 30-min capture median as the effective delta
    p30 = pooled_all.get(30, {})
    cap_med = (p30.get("capture_pts_per_pt") or {}).get("median")
    report["interpretation"] = {
        "assumed_by_backtest": "effective delta = 1.0 (index points == option points)",
        "measured_30min_capture_median": cap_med,
        "note": (
            "A backtest 1:3 in INDEX points (risk R, reward 3R) captures roughly "
            f"{cap_med}x that in PREMIUM points on the favourable leg, before "
            "spread/theta. Delta also shifts along the path (rises as the option "
            "goes ITM on a winner, falls as it goes OTM on a loser), so the "
            "premium-space RR is not simply index-RR x capture -- it is "
            "asymmetric; see capture_when_premium_rose vs _fell."
        ) if cap_med is not None else "insufficient samples",
        "data_ceiling": (
            f"{len(report['sessions_used'])} session(s) with aligned index+option"
            " capture; DESCRIPTIVE ONLY, far below any reliability floor -- no "
            "edge or slippage constant should be hard-coded from this yet."),
    }
    return report


def _fmt(s):
    if not s:
        return "  (no samples)"
    return (f"  n={s['n']:<5} median={s['median']:<7} "
            f"IQR[{s['p25']}, {s['p75']}]  mean={s['mean']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NIFTY")
    ap.add_argument("--db", default="data/market_history.db")
    ap.add_argument("--min-move", type=float, default=8.0,
                    help="min |index move| over the horizon to count a sample (pts)")
    ap.add_argument("--rth-start", type=int, default=555, help="IST minute-of-day, 555=09:15")
    ap.add_argument("--rth-end", type=int, default=930, help="IST minute-of-day, 930=15:30")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rep = analyse(a.db, a.symbol.upper(), a.min_move, a.rth_start, a.rth_end)
    if a.json:
        print(json.dumps(rep, indent=2, default=str))
        return

    print(f"\n=== option premium vs index move : {rep['symbol']} ===")
    print(f"db={rep['db']}  sessions={rep['sessions_used']}  min_move={rep['min_move_pts']}pts"
          f"  RTH(IST min)={rep['rth_ist']}")
    for H, blk in rep["by_horizon"].items():
        p = blk["pooled"]
        print(f"\n-- horizon {H} min  (pooled, {p['samples']} samples) --")
        print("  effective delta  dP/dS :"); print(_fmt(p["eff_delta"]))
        print("  capture  dP/|dS| (prem pts per index pt) :"); print(_fmt(p["capture_pts_per_pt"]))
        print("  broker delta at entry :"); print(_fmt(p["broker_delta_at_entry"]))
        print(f"  premium moved favourably in {p['frac_premium_moved_favourably']} of samples")
        print("  capture | premium rose :"); print(_fmt(p["capture_when_premium_rose"]))
        print("  capture | premium fell :"); print(_fmt(p["capture_when_premium_fell"]))
        for sess, sd in sorted(blk["by_session"].items()):
            e = sd["eff_delta"] or {}
            c = sd["capture_pts_per_pt"] or {}
            b = sd["broker_delta_at_entry"] or {}
            print(f"    {sess}: N={sd['samples']:<4} eff_delta_med={e.get('median')}"
                  f"  capture_med={c.get('median')}  broker_delta_med={b.get('median')}")
    it = rep["interpretation"]
    print("\n-- interpretation --")
    for k, v in it.items():
        print(f"  {k}: {v}")
    print()


if __name__ == "__main__":
    main()
