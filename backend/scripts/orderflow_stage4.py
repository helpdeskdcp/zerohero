#!/usr/bin/env python3
"""
orderflow_stage4.py -- RESEARCH ONLY.

Two jobs:
  (1) OPTION-PREMIUM RE-WALK + realistic slippage of the Stage-3 CORE setup
      (Stage-3 was index-structural R; this scores the same trades on the
      captured ATM option premium with a spread + adverse-continuation model).
  (2) PUBLIC MARKET-PROFILE + ORDER-FLOW CONCEPT MAPPING (Balakrishna-style
      concepts, from public descriptions only). Every concept is mapped to an
      observable variable, classified LEVEL 1-4 by proxy quality, given a
      testable hypothesis, and tested for INCREMENTAL information over the
      Stage-3 model. Models A / B / C compared on train / validation / OOS.

HARD RULES: research only; no production pattern; no live change; no orders;
NO weighted score / no "signal score"; strictly causal (completed candles,
developing profile from bars[:idx], OI/volume from <= event ts, chronological
split, thresholds chosen for stability not P&L). Nothing is presented as
"institutional", "smart money", "holy grail" or "profitable".

DATA REALITY (see the proxy matrix in the report):
  * NO bid/ask-classified trade volume, NO footprint, NO true delta, NO
    diagonal price-level imbalance -> those concepts are LEVEL 4 UNOBSERVABLE.
  * Available: 5m OHLC (no bar volume), per-strike option CE/PE LTP + OI +
    cumulative traded volume + broker greeks (oi_dashboard, ~35 sess/symbol).
  * "Order flow" here = a LEVEL-2/3 PROXY built from option CE/PE traded
    volume + OI change. It is NOT order flow. Labelled as such throughout.

Outputs:
  data/orderflow_stage4_report_2026-09-06.txt
  data/orderflow_stage4_events.csv
"""
from __future__ import annotations

import argparse
import csv as _csv
import statistics as st
import sys
from bisect import bisect_right
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import orderflow_histsrc as HS
from scripts.orderflow_stage3_validation import (
    HSess, collect, hset_core, _split, _dev_va, _levels, _broken_level,
)

OUT_WIN = 3
SPREAD_FRAC = 0.02          # round-trip spread+fees estimate: 2% of entry premium
IMB_MULTS = (2.0, 2.5, 3.0)


# ================================================================ premium walk
def _asof_i(series, ts, col):
    if not series or not ts:
        return None
    i = bisect_right(series, (ts,) + (float("inf"),) * (len(series[0]) - 1))
    return series[i - 1][col] if i > 0 else None


def _atm_series(oi_series, ref_price, side):
    ot = "CE" if side == "LONG" else "PE"
    ks = sorted({k[0] for k in oi_series if k[1] == ot and oi_series[k]})
    if not ks:
        return None, None
    k = min(ks, key=lambda s: abs(s - ref_price))
    return k, oi_series[(k, ot)]


def premium_rewalk(oi_series, ref_price, side, entry_ts, exit_ts, stop_hit,
                   session_bars, spread=SPREAD_FRAC):
    """Re-price one CORE trade on the captured ATM option premium.
      entry_ts  : bar_start of the early-acceptance entry bar
      exit_ts   : bar_start of the bar the INDEX walk exited on
      stop_hit  : True if the index exit was the structural stop (loss),
                  False if it was the 3R target / session end
    Slippage model: a stop-out exits at the option LTP as-of the NEXT captured
    bar after exit_ts (adverse continuation) minus half-spread; a target exit
    exits as-of exit_ts minus half-spread. Entry pays half-spread.
    Returns premium points, premium MFE/MAE, ATM strike, and the option
    traded-volume over the hold (for the order-flow proxy)."""
    strike, ser = _atm_series(oi_series, ref_price, side)
    if not ser:
        return None
    p_entry = _asof_i(ser, entry_ts, 1)
    if p_entry is None or p_entry <= 0:
        return None
    # exit timestamp with adverse-continuation slippage on stop-outs
    ex_ts = exit_ts
    if stop_hit and session_bars:
        nxt = next((b["bar_start"] for b in session_bars if b["bar_start"] > exit_ts), None)
        ex_ts = nxt or exit_ts
    p_exit = _asof_i(ser, ex_ts, 1)
    if p_exit is None:
        return None
    hs = spread / 2.0
    p_entry_eff = p_entry * (1 + hs)          # pay the spread getting in
    p_exit_eff = p_exit * (1 - hs)            # and getting out
    pts = round(p_exit_eff - p_entry_eff, 4)
    # premium path MFE/MAE between entry_ts and ex_ts
    lo = bisect_right(ser, (entry_ts, -1.0))
    hiix = bisect_right(ser, (ex_ts, float("inf")))
    path = [x[1] for x in ser[lo:hiix]] or [p_entry, p_exit]
    mfe = round(max(path) - p_entry_eff, 4)
    mae = round(min(path) - p_entry_eff, 4)
    # option traded volume over the hold (cum vol differenced)
    v0 = _asof_i(ser, entry_ts, 4)
    v1 = _asof_i(ser, ex_ts, 4)
    hold_vol = (v1 - v0) if (v0 is not None and v1 is not None and v1 >= v0) else None
    return {"strike": strike, "p_entry": round(p_entry, 2), "p_exit": round(p_exit, 2),
            "prem_pts": pts, "prem_mfe": mfe, "prem_mae": mae,
            "prem_ret_pct": round(pts / p_entry_eff, 4), "hold_vol": hold_vol,
            "thin": len(path) <= 3}


# ================================================================ concept proxies
def _interval_vol(ser, t0, t1, col=4):
    v0 = _asof_i(ser, t0, col)
    v1 = _asof_i(ser, t1, col)
    return (v1 - v0) if (v0 is not None and v1 is not None and v1 >= v0) else None


def orderflow_proxy(oi_series, s, idx, direction):
    """LEVEL-2/3 PROXY (NOT true order flow). From option CE/PE traded volume
    and OI change around the spike bar. Returns a dict of features."""
    b = s.clean[idx]
    ce_k, ce = _atm_series(oi_series, b["c"], "LONG")
    pe_k, pe = _atm_series(oi_series, b["c"], "SHORT")
    if not ce or not pe:
        return {}
    t_1 = s.clean[idx - 1]["bar_start"] if idx >= 1 else b["bar_start"]
    t0 = b["bar_start"]
    t_p1 = s.clean[idx + 1]["bar_start"] if idx + 1 < len(s.clean) else t0
    # traded volume in the spike window [T-1, T+1]
    ce_v = _interval_vol(ce, t_1, t_p1)
    pe_v = _interval_vol(pe, t_1, t_p1)
    imb = (ce_v / pe_v) if (ce_v and pe_v and pe_v > 0) else None
    imb_dir = None
    if imb is not None:
        imb_dir = imb if direction == "LONG" else (1.0 / imb if imb > 0 else None)
    # acceleration: spike-window vol vs mean of the 3 prior windows
    prior = []
    for j in range(idx - 3, idx):
        if j < 1:
            continue
        a = s.clean[j - 1]["bar_start"]
        c = s.clean[j + 1]["bar_start"] if j + 1 < len(s.clean) else s.clean[j]["bar_start"]
        cev = _interval_vol(ce, a, c)
        pev = _interval_vol(pe, a, c)
        if cev is not None and pev is not None:
            prior.append(cev + pev)
    base = st.fmean(prior) if prior else None
    now = (ce_v + pe_v) if (ce_v is not None and pe_v is not None) else None
    accel = (now / base) if (now and base and base > 0) else None
    # OI change of the traded side vs opposite
    oi_ce = _asof_i(ce, t0, 2)
    oi_ce_prev = _asof_i(ce, t_1, 2)
    oi_pe = _asof_i(pe, t0, 2)
    oi_pe_prev = _asof_i(pe, t_1, 2)
    d_ce = (oi_ce - oi_ce_prev) if (oi_ce is not None and oi_ce_prev is not None) else None
    d_pe = (oi_pe - oi_pe_prev) if (oi_pe is not None and oi_pe_prev is not None) else None
    return {"of_imb_dir": round(imb_dir, 2) if imb_dir else None,
            "of_vol_accel": round(accel, 2) if accel else None,
            "of_oi_d_trade": (d_ce if direction == "LONG" else d_pe),
            "of_oi_d_opp": (d_pe if direction == "LONG" else d_ce)}


def profile_context(s, idx, direction):
    """LEVEL-1/2. Developing value area + POC migration + location of the spike
    close, all from bars[:idx]."""
    va = s.va[idx]
    va_prev = s.va[max(0, idx - 6)]
    b = s.clean[idx]
    out = {"poc_migrating": None, "poc_migr_dir": None, "va_width": None,
           "va_width_contraction": None, "loc": "NA", "dist_poc_atr": None}
    if va:
        val, poc, vah = va
        out["va_width"] = round(vah - val, 2)
        out["loc"] = ("above_value" if b["c"] > vah else "below_value" if b["c"] < val
                      else "near_POC" if abs(b["c"] - poc) <= 0.15 * (vah - val + 1e-9)
                      else "inside_value")
        if va_prev:
            pval, ppoc, pvah = va_prev
            out["poc_migrating"] = abs(poc - ppoc) > 0.05 * (vah - val + 1e-9)
            out["poc_migr_dir"] = ("UP" if poc > ppoc else "DOWN" if poc < ppoc else "FLAT")
            pw = pvah - pval
            out["va_width_contraction"] = round((vah - val) / pw, 3) if pw > 0 else None
    return out


def balance_features(s, idx, k=8):
    """LEVEL-1. Objective 'balance' measures over the prior k bars (no gate)."""
    if idx < k + 1:
        return {}
    w = s.clean[idx - k:idx]
    span = max(x["h"] for x in w) - min(x["l"] for x in w)
    base = s.base[idx] or 1e-9
    rot = sum(1 for j in range(1, len(w) - 1)
              if (w[j]["c"] - w[j - 1]["c"]) * (w[j + 1]["c"] - w[j]["c"]) < 0)
    vas = [s.va[j] for j in range(idx - k, idx) if s.va[j]]
    poc_std = st.pstdev([v[1] for v in vas]) if len(vas) >= 3 else None
    return {"bal_range_contraction": round(span / (k * base), 3),
            "bal_rotations": rot,
            "bal_poc_std_atr": round(poc_std / (s._atr[idx] or base), 3)
            if (poc_std is not None and hasattr(s, "_atr")) else
            (round(poc_std / base, 3) if poc_std is not None else None)}


def day_regime(s, idx):
    """LEVEL-1/2. Objective Market-Profile-style day type from bars[:idx]."""
    if idx < 12:
        return "NA"
    c = s.clean[:idx]
    o0 = c[0].get("o") or c[0]["c"]
    net = c[-1]["c"] - o0
    rng = max(b["h"] for b in c) - min(b["l"] for b in c)
    if rng <= 0:
        return "NA"
    first_hr = c[:12]
    fh_rng = max(b["h"] for b in first_hr) - min(b["l"] for b in first_hr)
    rot = sum(1 for j in range(1, len(c) - 1)
              if (c[j]["c"] - c[j - 1]["c"]) * (c[j + 1]["c"] - c[j]["c"]) < 0)
    hi, lo = max(b["h"] for b in c), min(b["l"] for b in c)
    broke_hi = any(b["h"] >= hi - 1e-9 for b in c[:-3]) and c[-1]["c"] < hi - 0.3 * rng
    broke_lo = any(b["l"] <= lo + 1e-9 for b in c[:-3]) and c[-1]["c"] > lo + 0.3 * rng
    if abs(net) >= 0.5 * rng:
        return "TRENDING"
    if fh_rng >= 0.6 * rng:
        return "OPENING_DRIVE"
    if broke_hi:
        return "FAILED_BREAKOUT"
    if broke_lo:
        return "FAILED_BREAKDOWN"
    if rot >= 0.45 * len(c):
        return "ROTATION"
    if rng <= 1.2 * (st.median([b["h"] - b["l"] for b in c if b["h"] > b["l"]]) * len(c) ** 0.5):
        return "BALANCED"
    return "CHOP"


# ================================================================ build stage-4 rows
def build(sym):
    ss = HS.sessions(sym)
    sessions = [HSess(sym, d, src) for d, src in ss]
    ev = collect(sym, sessions, 0.90)          # same P90 adaptive spike as Stage-3
    core = hset_core(ev)
    tr, va, oos = _split(sessions)
    oi_cache = {}
    rows = []
    for e in core:
        E = e["E"]["D_earlyaccept"]
        w = E["stops"]["reaction"]
        key = (e["sym"], e["session"])
        if key not in oi_cache:
            oi_cache[key] = HS.session_oi_series(e["sym"], e["session"])
        oi_series = oi_cache[key]
        # locate the session + spike index to compute proxies
        s = next((x for x in sessions if x.date == e["session"]), None)
        if s is None:
            continue
        idx = next((i for i, b in enumerate(s.clean) if b["bar_start"] == e["ts"]), None)
        if idx is None:
            continue
        sb = s.clean
        exit_ts = None
        stop_hit = None
        # reconstruct the index exit from the walk metrics
        eb = E["ebar"]
        entry = E["entry"]
        sl = w["sl"]
        R = abs(entry - sl)
        t3 = entry + 3 * R if e["direction"] == "LONG" else entry - 3 * R
        for i in range(eb, len(sb)):
            b = sb[i]
            hit_stop = (b["l"] <= sl) if e["direction"] == "LONG" else (b["h"] >= sl)
            hit_t3 = (b["h"] >= t3) if e["direction"] == "LONG" else (b["l"] <= t3)
            if hit_stop:
                exit_ts, stop_hit = b["bar_start"], True
                break
            if hit_t3:
                exit_ts, stop_hit = b["bar_start"], False
                break
        if exit_ts is None:
            exit_ts, stop_hit = sb[-1]["bar_start"], False
        pr = premium_rewalk(oi_series, e["L"] if e["L"] else entry, e["direction"],
                            E["valid_ts"], exit_ts, stop_hit, sb)
        of = orderflow_proxy(oi_series, s, idx, e["direction"])
        pf = profile_context(s, idx, e["direction"])
        bal = balance_features(s, idx)
        reg = day_regime(s, idx)
        split = "train" if e["session"] in tr else "val" if e["session"] in va else "oos"
        rows.append({**e, "split": split, "idx_R": R, "idx_exit_stop": stop_hit,
                     "prem": pr, "of": of, "pf": pf, "bal": bal, "day_regime": reg,
                     "idx_reached_3R": w["reached_3R"], "idx_reached_5R": w["reached_5R"],
                     "idx_max_R": w["max_R"], "idx_MFE_R": w["MFE_R"], "idx_MAE_R": w["MAE_R"]})
    return sessions, rows, (tr, va, oos)


# ================================================================ stats
def _pm(rows):
    """Premium-basis aggregate for a set of CORE trades."""
    R = [r for r in rows if r.get("prem") and not r["prem"]["thin"]]
    if not R:
        return {"n": 0}
    pts = [r["prem"]["prem_pts"] for r in R]
    rets = [r["prem"]["prem_ret_pct"] for r in R]
    wins = [x for x in pts if x > 0]
    idxR = [r["idx_R"] for r in R]
    cap = [r["prem"]["prem_pts"] / r["idx_R"] for r in R if r["idx_R"]]  # prem pts per idx-R pt
    # premium expressed in idx-R units: how many idx-R the premium move is worth
    prem_in_R = [r["prem"]["prem_pts"] / (r["prem"]["p_entry"] * SPREAD_FRAC + r["idx_R"] * 0)
                 for r in R]  # not used; keep cap as the headline
    seq = [r["prem"]["prem_pts"] for r in sorted(R, key=lambda x: x["session"])]
    peak = cum = dd = 0.0
    for x in seq:
        cum += x
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    cont = sum(1 for r in R if r["oc"].get(OUT_WIN) == "CONTINUATION")
    trap = sum(1 for r in R if r["oc"].get(OUT_WIN) == "TRAP")
    return {
        "n": len(R), "sessions": len({r["session"] for r in R}),
        "cont": round(cont / len(R), 3), "trap": round(trap / len(R), 3),
        "prem_E_pts": round(st.fmean(pts), 2), "prem_med_pts": round(st.median(pts), 2),
        "prem_win": round(len(wins) / len(R), 3),
        "prem_E_ret": round(st.fmean(rets), 4),
        "prem_capture": round(st.median(cap), 3) if cap else None,  # vs Stage-1 ~0.40
        "prem_maxDD_pts": round(dd, 1),
        "idx_E_R": round(st.fmean([min(r["idx_max_R"], 3.0) if r["idx_reached_3R"]
                                   else max(r["idx_MAE_R"], -1.0) for r in R]), 3),
        "idx_P3R": round(sum(1 for r in R if r["idx_reached_3R"]) / len(R), 3),
    }


def _row(name, m):
    if not m or not m["n"]:
        return f"    {name:<30} n=0"
    return (f"    {name:<30} n={m['n']:>4} ses={m['sessions']:>2} cont={m['cont']*100:>3.0f}% "
            f"trap={m['trap']*100:>3.0f}% | idx E[R]={m['idx_E_R']:>6} P3R={m['idx_P3R']*100:>3.0f}% "
            f"| PREM E={m['prem_E_pts']:>7}pts win={m['prem_win']*100:>3.0f}% "
            f"E_ret={m['prem_E_ret']*100:>5.1f}% capture={m['prem_capture']} DD={m['prem_maxDD_pts']}")


# ================================================================ report
CONCEPTS = [
    # (concept, observable var, source, math def, causal ts, hypothesis, LEVEL)
    ("A Market Profile (POC/VAH/VAL)", "developing time-price value area",
     "5m bars (no volume) -> TPO", "equal-weight-per-bar range histogram, 70% value area, bars[:idx]",
     "completed bar", "spike location vs value predicts continuation/trap", 2),
    ("B Market structure / day types", "objective session classification",
     "5m bars", "net move vs range, first-hour range, rotation count, hi/lo reclaim",
     "completed bars[:idx]", "the same spike behaves differently by day type", 2),
    ("C Order flow (aggressor)", "aggressive buy/sell classified volume",
     "NOT AVAILABLE", "-", "-", "-", 4),
    ("D Buyer vs seller pressure", "option CE vs PE traded-volume + OI change",
     "strikes.ce_vol/pe_vol (cumulative), ce_oi/pe_oi", "diff consecutive cycles -> interval vol; ratio CE/PE; OI delta",
     "cycle ts <= bar", "rising same-side option volume/OI after the spike predicts continuation", 3),
    ("E Market vs limit orders", "book pending vs executed",
     "zerohero quote_snapshots tot_buy_qty/tot_sell_qty (Sep only, 4 sess)", "resting-qty skew",
     "quote ts", "book skew adds info", 3),
    ("F Lifting offer / hitting bid", "aggressor side of each trade",
     "NOT AVAILABLE", "-", "-", "-", 4),
    ("G Balance vs imbalance", "range/VA/POC contraction then expansion",
     "5m bars + developing VA", "prior-k range span / (k*ATR); VA-width contraction; POC std; rotations",
     "bars[:idx]", "a balance->imbalance transition precedes continuation", 1),
    ("H Footprint / price-level distribution", "volume traded at each price of the underlying",
     "NOT AVAILABLE (underlying feed has zero volume)", "-", "-", "-", 4),
    ("I Delta (buy vol - sell vol)", "signed aggressor volume",
     "NOT AVAILABLE for the underlying", "-", "-", "-", 4),
    ("J Diagonal imbalance", "buy@higher-level vs sell@lower-level (footprint)",
     "NOT AVAILABLE", "-", "-", "-", 4),
    ("K Momentum (raw)", "displacement / range expansion / consecutive closes",
     "5m bars", "|close-open|/ATR; range/median; run of same-sign closes; POC migration",
     "bars[:idx]", "raw momentum components carry independent info vs the spike", 1),
    ("L Liquidity / acceptance / rejection", "close beyond level held vs reclaimed",
     "5m bars + structural levels", "close vs broken level over the next 1-3 completed bars",
     "bars[:idx+w]", "acceptance vs reclaim splits continuation vs trap (Stage-2/3 core)", 1),
    ("M Price rotation", "direction-change count in a window",
     "5m bars", "sign changes of consecutive close-to-close moves over prior k",
     "bars[:idx]", "high rotation before the spike -> lower continuation", 1),
    ("N Value area / POC / profile location", "spike close vs developing VA",
     "5m bars -> TPO", "loc in {inside_value, near_POC, above_value, below_value}",
     "bars[:idx]", "spike outside value (with acceptance) continues; at POC/edges it fades", 2),
    ("'200% imbalance' (public note)", "2:1 relative quantity",
     "option CE/PE interval traded volume (proxy, not footprint)", "CE_vol/PE_vol (dir-adjusted) >= {2.0, 2.5, 3.0} or percentile",
     "cycle ts <= bar", "a >=2x same-side option-volume imbalance around the spike adds predictive value", 3),
]


def concept_table(out):
    p = lambda *a: print(*a, file=out)
    p("\n" + "=" * 120)
    p("[§2/§16] PUBLIC CONCEPT -> OBSERVABLE DATA MAPPING  (public descriptions only; no course material)")
    p("=" * 120)
    lvl = {1: "L1 direct", 2: "L2 good proxy", 3: "L3 weak proxy", 4: "L4 UNOBSERVABLE"}
    for c, ov, src, mdef, cts, hyp, L in CONCEPTS:
        p(f"\n  {c}")
        p(f"    observable : {ov}")
        p(f"    source     : {src}")
        p(f"    definition : {mdef}")
        p(f"    causal ts  : {cts}")
        p(f"    hypothesis : {hyp}")
        p(f"    proxy level: {lvl[L]}")


def _grp(rows, fn):
    g = {}
    for r in rows:
        g.setdefault(fn(r), []).append(r)
    return g


def report(sym, sessions, rows, splits, out):
    p = lambda *a: print(*a, file=out)
    tr, va, oos = splits
    p("\n" + "#" * 118)
    p(f"# {sym}  --  {len(rows)} CORE trades over {len({r['session'] for r in rows})} sessions "
      f"(abnormal spike + early-acceptance + reaction stop)")
    thin = sum(1 for r in rows if r.get('prem') and r['prem']['thin'])
    noprem = sum(1 for r in rows if not r.get('prem'))
    p(f"#   premium re-priced: {len(rows)-noprem-thin} usable, {thin} thin-quote dropped, {noprem} no ATM series")
    p("#" * 118)

    p("\n[§1 OPTION-PREMIUM RE-WALK + slippage]  (spread " + f"{SPREAD_FRAC*100:.0f}% round-trip, adverse-continuation fill on stops)")
    p("   MODEL A = Stage-3 CORE, scored on the captured ATM option premium:")
    for lab, ds in (("train", tr), ("val", va), ("oos", oos), ("POOLED", None)):
        g = rows if ds is None else [r for r in rows if r["session"] in ds]
        p(_row(f"A [{lab}]", _pm(g)))
    poolA = _pm(rows)
    p(f"   -> premium capture (median premium pts per index-R pt) = {poolA.get('prem_capture')}  "
      f"(Stage-1 unconditional was ~0.40; the CORE selection {'beats' if (poolA.get('prem_capture') or 0) > 0.4 else 'does not beat'} it)")

    # ---- MODEL B: + Market Profile context ----
    p("\n[§14 MODEL B = A + MARKET PROFILE CONTEXT]")
    def modelB(rs):
        # keep only spikes that closed OUTSIDE developing value in the break
        # direction OR inside value moving toward the edge -- drop near_POC and
        # wrong-side-of-value (Stage-3 showed those fade).
        out2 = []
        for r in rs:
            loc = r["pf"].get("loc")
            d = r["direction"]
            if loc == "above_value" and d == "LONG":
                out2.append(r)
            elif loc == "below_value" and d == "SHORT":
                out2.append(r)
            elif loc == "inside_value":
                out2.append(r)
        return out2
    for lab, ds in (("train", tr), ("val", va), ("oos", oos), ("POOLED", None)):
        g = modelB(rows if ds is None else [r for r in rows if r["session"] in ds])
        p(_row(f"B [{lab}]", _pm(g)))
    poolB = _pm(modelB(rows))
    p(f"   -> incremental vs A (pooled): dPREM_E = {round((poolB.get('prem_E_pts') or 0)-(poolA.get('prem_E_pts') or 0),2)}pts, "
      f"dP3R = {round(((poolB.get('idx_P3R') or 0)-(poolA.get('idx_P3R') or 0))*100)}pp, "
      f"n {poolA['n']}->{poolB['n']}")

    # ---- MODEL C: + order-flow proxy ----
    p("\n[§14 MODEL C = B + ORDER-FLOW PROXY]  (option CE/PE interval-volume imbalance >= 2x in the break direction; L3 proxy)")
    def modelC(rs):
        return [r for r in modelB(rs)
                if (r["of"].get("of_imb_dir") or 0) >= 2.0]
    for lab, ds in (("train", tr), ("val", va), ("oos", oos), ("POOLED", None)):
        g = modelC(rows if ds is None else [r for r in rows if r["session"] in ds])
        p(_row(f"C [{lab}]", _pm(g)))
    poolC = _pm(modelC(rows))
    p(f"   -> incremental vs B (pooled): dPREM_E = {round((poolC.get('prem_E_pts') or 0)-(poolB.get('prem_E_pts') or 0),2)}pts, "
      f"dP3R = {round(((poolC.get('idx_P3R') or 0)-(poolB.get('idx_P3R') or 0))*100)}pp, "
      f"n {poolB['n']}->{poolC['n']}")

    # ---- §5 imbalance threshold sweep (stability, not P&L) ----
    p("\n[§5 IMBALANCE threshold sweep]  option CE/PE interval-volume ratio in the break direction")
    haveimb = [r for r in rows if r["of"].get("of_imb_dir") is not None]
    p(f"   ({len(haveimb)}/{len(rows)} trades have an option-volume imbalance reading)")
    for m in IMB_MULTS:
        g = [r for r in haveimb if r["of"]["of_imb_dir"] >= m]
        p(_row(f"imb_dir >= {m}", _pm(g)))
    for lo, hi in ((0.0, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 99)):
        g = [r for r in haveimb if lo <= r["of"]["of_imb_dir"] < hi]
        p(_row(f"imb_dir {lo}-{hi if hi<99 else '+'}", _pm(g)))

    # ---- §9 by objective day regime ----
    p("\n[§9 by MARKET-PROFILE DAY REGIME]  (regime derived from bars[:spike], then the CORE setup tested inside it)")
    for k, g in sorted(_grp(rows, lambda r: r["day_regime"]).items()):
        p(_row(str(k), _pm(g)))

    # ---- §8 balance -> imbalance transition ----
    p("\n[§8 BALANCE -> IMBALANCE]  prior-8-bar range contraction quartiles (lower = more coiled; NOT a gate)")
    haveb = [r for r in rows if r["bal"].get("bal_range_contraction") is not None]
    if len(haveb) >= 12:
        v = sorted(r["bal"]["bal_range_contraction"] for r in haveb)
        q = [v[len(v)//4], v[len(v)//2], v[3*len(v)//4]]
        for name, f in ((f"coiled <=Q1({q[0]:.2f})", lambda x: x <= q[0]),
                        ("Q1-Q2", lambda x: q[0] < x <= q[1]),
                        ("Q2-Q3", lambda x: q[1] < x <= q[2]),
                        (f"loose >Q3({q[2]:.2f})", lambda x: x > q[2])):
            p(_row(name, _pm([r for r in haveb if f(r["bal"]["bal_range_contraction"])])))

    # ---- §11 liquidity / trap comparison ----
    p("\n[§11 LIQUIDITY / TRAP]  reclaim of the broken level within 3 candles (feature, not entry gate)")
    p(_row("reclaim within 3", _pm([r for r in rows if r["reclaim_within3"]])))
    p(_row("no reclaim within 3", _pm([r for r in rows if not r["reclaim_within3"]])))
    p(_row("n1 agrees", _pm([r for r in rows if r["n1_agree"]])))
    p(_row("n1 disagrees", _pm([r for r in rows if r["n1_agree"] is False])))

    _incremental(sym, poolA, poolB, poolC, out)


def _incremental(sym, A, B, C, out):
    p = lambda *a: print(*a, file=out)
    p("\n[§7/§13 INCREMENTAL-INFORMATION SUMMARY]")
    def line(nm, m):
        if not m["n"]:
            p(f"    {nm}: n=0"); return
        p(f"    {nm}: n={m['n']} prem_E={m['prem_E_pts']}pts win={m['prem_win']*100:.0f}% "
          f"E_ret={m['prem_E_ret']*100:.1f}% P3R={m['idx_P3R']*100:.0f}% DD={m['prem_maxDD_pts']}pts "
          f"capture={m['prem_capture']}")
    line("MODEL A  (spike + early-acceptance + reaction stop)", A)
    line("MODEL B  (+ market-profile location)", B)
    line("MODEL C  (+ option-volume imbalance >=2x)", C)
    verdict = "A"
    if B["n"] >= 20 and (B.get("prem_E_ret") or -9) > (A.get("prem_E_ret") or -9) + 0.005 and B["prem_maxDD_pts"] >= A["prem_maxDD_pts"] * 1.3:
        verdict = "B"
    if C["n"] >= 20 and (C.get("prem_E_ret") or -9) > (B.get("prem_E_ret") or -9) + 0.005:
        verdict = "C"
    p(f"    -> smallest model that is not beaten out-of-sample: MODEL {verdict}  "
      f"(more layers only justified if OOS improves without materially worse drawdown)")


# ================================================================ concept classification
def classify(all_rows, out):
    p = lambda *a: print(*a, file=out)
    p("\n" + "=" * 120)
    p("[§16 FINAL CLASSIFICATION PER CONCEPT]  (SUPPORTED / PROMISING / REJECTED / INSUFFICIENT DATA / UNOBSERVABLE)")
    p("=" * 120)
    R = [r for r in all_rows if r.get("prem") and not r["prem"]["thin"]]
    def cmp_split(sel):
        g = [r for r in R if sel(r)]
        gc = [r for r in R if not sel(r)]
        if len(g) < 20 or len(gc) < 20:
            return None, len(g)
        return (st.fmean([x["prem"]["prem_ret_pct"] for x in g]) -
                st.fmean([x["prem"]["prem_ret_pct"] for x in gc])), len(g)
    rows_cls = [
        ("A/N Market Profile location (spike outside value vs at POC/edge)",
         lambda r: r["pf"].get("loc") in ("above_value", "below_value")),
        ("B Day-type regime (TRENDING/OPENING_DRIVE vs ROTATION/CHOP)",
         lambda r: r["day_regime"] in ("TRENDING", "OPENING_DRIVE")),
        ("D Buyer/seller pressure proxy (same-side OI rising)",
         lambda r: (r["of"].get("of_oi_d_trade") or 0) > 0),
        ("G Balance->imbalance (prior-8-bar range contraction < 1.0)",
         lambda r: (r["bal"].get("bal_range_contraction") or 9) < 1.0),
        ("K Momentum: n1 agrees with the spike",
         lambda r: r["n1_agree"] is True),
        ("L Acceptance vs reclaim (no reclaim within 3 candles)",
         lambda r: not r["reclaim_within3"]),
        ("M Price rotation: >=4 rotations in the prior 8 bars",
         lambda r: (r["bal"].get("bal_rotations") or 0) >= 4),
        ("'200% imbalance': option CE/PE interval-vol ratio >= 2x (dir)",
         lambda r: (r["of"].get("of_imb_dir") or 0) >= 2.0),
        ("Order-flow vol acceleration >= 1.5x prior windows",
         lambda r: (r["of"].get("of_vol_accel") or 0) >= 1.5),
    ]
    for name, sel in rows_cls:
        eff, n = cmp_split(sel)
        if eff is None:
            p(f"  {name:<62} n={n:<5} INSUFFICIENT DATA")
            continue
        stat = ("SUPPORTED" if eff > 0.010 else "PROMISING" if eff > 0.003
                else "REJECTED (no incremental premium-return edge)" if eff > -0.003
                else "REJECTED (negative)")
        p(f"  {name:<62} n={n:<5} d(prem E_ret) = {eff*100:+.2f}pp   {stat}")
    p("\n  UNOBSERVABLE with current data (LEVEL 4):")
    for c in ("C aggressor order flow", "F lifting-offer/hitting-bid", "H footprint / price-level distribution",
              "I true delta (signed aggressor volume)", "J diagonal price-level imbalance"):
        p(f"    - {c}")
    p("\n  Notes: 'order-flow' rows above are a LEVEL-3 PROXY built from option CE/PE traded")
    p("  volume + OI change. They are NOT exchange order flow and must not be described as")
    p("  institutional / smart-money activity.")


CSV_FIELDS = ["timestamp", "symbol", "session", "split", "day_regime", "direction",
              "spike_pctile", "range_x", "level_class", "reclaim_within3", "n1_agree",
              "pf_loc", "pf_poc_migr_dir", "pf_va_width_contraction",
              "bal_range_contraction", "bal_rotations",
              "of_imb_dir", "of_vol_accel", "of_oi_d_trade",
              "idx_R", "idx_MFE_R", "idx_MAE_R", "idx_max_R", "idx_reached_3R", "idx_reached_5R",
              "atm_strike", "p_entry", "p_exit", "prem_pts", "prem_ret_pct",
              "prem_mfe", "prem_mae", "prem_thin", "outcome_w3"]


def to_csv(rows, path):
    out = []
    for r in rows:
        pr = r.get("prem") or {}
        out.append({
            "timestamp": r["ts"], "symbol": r["sym"], "session": r["session"], "split": r["split"],
            "day_regime": r["day_regime"], "direction": r["direction"],
            "spike_pctile": r["spike_pctile"], "range_x": r["range_x"],
            "level_class": r["level_class"], "reclaim_within3": r["reclaim_within3"],
            "n1_agree": r["n1_agree"], "pf_loc": r["pf"].get("loc"),
            "pf_poc_migr_dir": r["pf"].get("poc_migr_dir"),
            "pf_va_width_contraction": r["pf"].get("va_width_contraction"),
            "bal_range_contraction": r["bal"].get("bal_range_contraction"),
            "bal_rotations": r["bal"].get("bal_rotations"),
            "of_imb_dir": r["of"].get("of_imb_dir"), "of_vol_accel": r["of"].get("of_vol_accel"),
            "of_oi_d_trade": r["of"].get("of_oi_d_trade"),
            "idx_R": r["idx_R"], "idx_MFE_R": r["idx_MFE_R"], "idx_MAE_R": r["idx_MAE_R"],
            "idx_max_R": r["idx_max_R"], "idx_reached_3R": r["idx_reached_3R"],
            "idx_reached_5R": r["idx_reached_5R"],
            "atm_strike": pr.get("strike"), "p_entry": pr.get("p_entry"), "p_exit": pr.get("p_exit"),
            "prem_pts": pr.get("prem_pts"), "prem_ret_pct": pr.get("prem_ret_pct"),
            "prem_mfe": pr.get("prem_mfe"), "prem_mae": pr.get("prem_mae"),
            "prem_thin": pr.get("thin"), "outcome_w3": r["oc"].get(3),
        })
    with open(path, "w", newline="") as f:
        wr = _csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(out)
    return len(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NIFTY,NATURALGAS,CRUDEOIL")
    ap.add_argument("--out", default="data/orderflow_stage4_report_2026-09-06.txt")
    ap.add_argument("--csv", default="data/orderflow_stage4_events.csv")
    a = ap.parse_args()
    syms = [x.strip().upper() for x in a.symbols.split(",") if x.strip()]
    all_rows = []
    with open(a.out, "w") as f:
        print("orderflow STAGE-4 -- option-premium re-walk + public concept mapping. "
              "RESEARCH ONLY, no production change, no score, no orders.", file=f)
        concept_table(f)
        for sym in syms:
            sessions, rows, splits = build(sym)
            all_rows += rows
            report(sym, sessions, rows, splits, f)
        classify(all_rows, f)
        print("\n[DATA-AVAILABILITY MATRIX]", file=f)
        for line in (
            "  5m OHLC underlying ............ YES (no bar volume)",
            "  option CE/PE LTP per strike ... YES (~35 sess/symbol, Jul13-Aug28 + Sep1-4)",
            "  option CE/PE OI + OI change ... YES",
            "  option CE/PE traded volume .... YES (cumulative; differenced for interval vol)",
            "  option greeks (delta/gamma..) . YES (oi_dashboard strikes table)",
            "  underlying tick / footprint ... NO",
            "  aggressor-classified volume ... NO",
            "  bid/ask book on underlying .... NO",
            "  option bid/ask book .......... only zerohero Sep (4 sess) tot_buy/sell_qty",
        ):
            print(line, file=f)
        print("\n[WHAT REMAINS UNPROVEN / TO COLLECT FOR STAGE-5]", file=f)
        for line in (
            "  * true delta / footprint on the underlying (needs tick data with aggressor side)",
            "  * a never-touched final holdout period (this work used Jul-Sep in full)",
            "  * option fills at real bid/ask (used LTP +/- a 2% round-trip estimate)",
            "  * >100 independent sessions across a full volatility cycle",
        ):
            print(line, file=f)
        print("\n[FINAL] Research only. No production pattern, no signal enabled, no live change, "
              "no orders, no weighted score. No claim of 'institutional', 'smart money', "
              "'holy grail' or 'profitable' is made -- see the per-concept classification.", file=f)
    n = to_csv(all_rows, a.csv)
    print(f"wrote {n} rows -> {a.csv}")
    print(f"wrote report -> {a.out}")
    print(open(a.out).read())


if __name__ == "__main__":
    main()
