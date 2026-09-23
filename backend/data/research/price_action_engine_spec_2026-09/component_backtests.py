"""
Standalone research script -- backtests 4 genuinely-new components of the
user's "Price Action Research + Probability Engine" spec, SEPARATELY, on
real data. Read-only reuse of app/liquidity_sweep/ (structure.py, resample.py)
and app/backtest/replay.py (cost model) -- nothing in either package is
modified. No integration into any live/paper path. Run manually:

    cd backend && venv/bin/python3 data/research/price_action_engine_spec_2026-09/component_backtests.py
"""
from __future__ import annotations

import sqlite3
import statistics as st
from datetime import datetime

from app.liquidity_sweep.backtest import load_kaggle_nifty_bars
from app.liquidity_sweep.resample import resample_bars, resample_daily
from app.liquidity_sweep.structure import swings, equal_levels, htf_bias, _ema

HORIZON = 12    # ~1h on 5m bars -- matches liquidity_sweep's own HORIZON_BARS, for consistency


def _fwd_ret(bars, i, horizon=HORIZON):
    if i + horizon >= len(bars):
        return None
    return bars[i + horizon]["c"] - bars[i]["c"]


def _split_swap(rows):
    """rows: list of (label_bool, value). Returns (fwd_result, swap_result) --
    fwd = first half train-like / second half test-like; swap = reversed.
    Each result is (mean_true, mean_false, n_true, n_false)."""
    def _stat(sub):
        t = [v for lab, v in sub if lab]
        f = [v for lab, v in sub if not lab]
        return (round(st.mean(t), 2) if t else None, round(st.mean(f), 2) if f else None, len(t), len(f))
    mid = len(rows) // 2
    return _stat(rows[mid:]), _stat(rows[:mid])   # test on 2nd half; swap = test on 1st half


print("Loading real Kaggle NIFTY 5m bars (2yr)...")
bars5 = load_kaggle_nifty_bars(limit_years=2.0)
print(f"  {len(bars5)} real 5m bars, {bars5[0]['t']} .. {bars5[-1]['t']}")
bars15 = resample_bars(bars5, 15)
bars30 = resample_bars(bars5, 30)
daily = resample_daily(bars5)
print(f"  resampled: {len(bars15)} 15m, {len(bars30)} 30m, {len(daily)} daily (all real aggregation, no fabrication)")

# index maps: for a given 5m bar timestamp, find the last CLOSED 15m/30m/daily bar
def _closed_index_map(base_bars, agg_bars):
    """For each base bar i, the index into agg_bars of the latest bar whose
    close time <= base_bars[i]'s time (strictly causal)."""
    out = [None] * len(base_bars)
    j = -1
    for i, b in enumerate(base_bars):
        while j + 1 < len(agg_bars) and agg_bars[j + 1]["t"] <= b["t"]:
            j += 1
        out[i] = j
    return out

map15 = _closed_index_map(bars5, bars15)
map30 = _closed_index_map(bars5, bars30)
mapD = _closed_index_map(bars5, daily)

results = {}

# ============================================================ COMPONENT 1
# 30m->15m->5m cascade alignment. 1m/3m: DATA_BLOCKED -- Kaggle NIFTY has
# 5m bars only (already documented in liquidity_sweep/backtest.py's own
# module docstring); building 1m/3m from 5m would fabricate data. Tested
# here: does 30m+15m+5m trend agreement predict the next-hour forward
# return direction better than disagreement?
print("\n=== Component 1: MTF cascade (30m/15m/5m; 1m/3m DATA_BLOCKED) ===")
rows1 = []
for i in range(60, len(bars5) - HORIZON):
    i15, i30 = map15[i], map30[i]
    if i15 is None or i30 is None or i15 < 21 or i30 < 21:
        continue
    b5 = htf_bias({"15m": bars5[max(0, i - 40):i + 1]})   # 5m own bias via same EMA20 method
    b15 = htf_bias({"15m": bars15[:i15 + 1]})
    b30 = htf_bias({"15m": bars30[:i30 + 1]})
    dirs = [b5["bias"], b15["bias"], b30["bias"]]
    aligned_bull = all(d == "BULLISH" for d in dirs)
    aligned_bear = all(d == "BEARISH" for d in dirs)
    if not (aligned_bull or aligned_bear):
        continue
    fwd = _fwd_ret(bars5, i)
    if fwd is None:
        continue
    favorable = (fwd > 0) if aligned_bull else (fwd < 0)
    rows1.append((True, favorable))   # "aligned" bucket only -- compare its hit-rate to 0.5 base rate
n1 = len(rows1)
hit1 = sum(1 for _, f in rows1 if f) / n1 if n1 else None
mid = n1 // 2
hit1_a = sum(1 for _, f in rows1[mid:] if f) / (n1 - mid) if n1 > mid else None
hit1_b = sum(1 for _, f in rows1[:mid] if f) / mid if mid else None
results["component_1"] = {"n_aligned_setups": n1, "favorable_hit_rate_all": round(hit1, 4) if hit1 else None,
                          "second_half": round(hit1_a, 4) if hit1_a else None,
                          "first_half": round(hit1_b, 4) if hit1_b else None}
print(f"  n={n1} full-cascade-aligned bars; favorable-direction hit-rate = {hit1}")
print(f"  first half = {hit1_b}, second half = {hit1_a} (should be similar if not overfit)")

# ============================================================ COMPONENT 2a
# Pivot-alone gate on the full 2yr real dataset (no volume needed).
print("\n=== Component 2a: Daily floor-pivot side, 2yr real data (pivot-only, no VWAP -- see 2b) ===")
rows2a = []
for i, b in enumerate(bars5):
    di = mapD[i]
    if di is None or di < 1:
        continue
    prev = daily[di - 1] if daily[di]["t"][:10] == b["t"][:10] else daily[di]
    # prev session = daily[di-1] when di is TODAY's bar; guard: use the daily
    # bar strictly before today's session date
    today = b["t"][:10]
    pd_idx = di
    while pd_idx >= 0 and daily[pd_idx]["t"][:10] >= today:
        pd_idx -= 1
    if pd_idx < 0:
        continue
    ph, pl, pc = daily[pd_idx]["h"], daily[pd_idx]["l"], daily[pd_idx]["c"]
    pp = (ph + pl + pc) / 3.0
    fwd = _fwd_ret(bars5, i)
    if fwd is None:
        continue
    bullish_side = b["c"] > pp
    rows2a.append((bullish_side, fwd))
mid = len(rows2a) // 2
def _side_stats(rows):
    bull = [f for side, f in rows if side]
    bear = [f for side, f in rows if not side]
    return {"n_bull_side": len(bull), "mean_fwd_ret_bull_side": round(st.mean(bull), 3) if bull else None,
           "n_bear_side": len(bear), "mean_fwd_ret_bear_side": round(st.mean(bear), 3) if bear else None}
s2a_all = _side_stats(rows2a)
s2a_h1 = _side_stats(rows2a[:mid])
s2a_h2 = _side_stats(rows2a[mid:])
results["component_2a_pivot_only"] = {"all": s2a_all, "first_half": s2a_h1, "second_half": s2a_h2}
print(f"  all: {s2a_all}")
print(f"  1st half: {s2a_h1}")
print(f"  2nd half: {s2a_h2}")

# ============================================================ COMPONENT 2b
# Real VWAP+Pivot AND-gate on real-volume data (market_history.db, NIFTY
# FUTURE -- index itself has zero real volume, confirmed in the Kaggle set
# too). Much smaller real window -- honestly reported, not padded.
print("\n=== Component 2b: VWAP+Pivot AND-gate, real-volume NIFTY FUTURE (market_history.db) ===")
import os
MH_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "data", "market_history.db")
conn = sqlite3.connect(MH_DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("""SELECT received_ts AS t, MIN(ltp) AS l, MAX(ltp) AS h, ltp AS c, volume AS v
               FROM quote_snapshots WHERE symbol='NIFTY' AND kind='FUTURE'
               GROUP BY substr(received_ts,1,16) ORDER BY received_ts""")
fut_rows = [dict(r) for r in cur.fetchall() if r["v"] is not None]
conn.close()
print(f"  {len(fut_rows)} real 1-min-bucketed NIFTY FUTURE snapshots with volume, "
     f"{fut_rows[0]['t'] if fut_rows else 'N/A'} .. {fut_rows[-1]['t'] if fut_rows else 'N/A'}")
if len(fut_rows) < 40:
    results["component_2b_vwap_pivot_and_gate"] = {"status": "INSUFFICIENT_DATA", "n": len(fut_rows)}
    print("  INSUFFICIENT_DATA -- real-volume window too small to test honestly.")
else:
    # session-anchored real VWAP (typical price = close here, only field we have per-bucket)
    rows2b = []
    day_cum_pv, day_cum_v, cur_day = 0.0, 0.0, None
    daily_fut = {}
    for r in fut_rows:
        d = r["t"][:10]
        daily_fut.setdefault(d, []).append(r)
    days = sorted(daily_fut.keys())
    for di, d in enumerate(days):
        if di == 0:
            continue
        prev = daily_fut[days[di - 1]]
        ph, pl, pc = max(x["h"] for x in prev), min(x["l"] for x in prev), prev[-1]["c"]
        pp = (ph + pl + pc) / 3.0
        cum_pv = cum_v = 0.0
        session = daily_fut[d]
        for i, r in enumerate(session):
            cum_pv += r["c"] * (r["v"] or 0)
            cum_v += (r["v"] or 0)
            if cum_v <= 0 or i + HORIZON >= len(session):
                continue
            vwap = cum_pv / cum_v
            fwd = session[i + HORIZON]["c"] - r["c"]
            above_vwap, above_pp = r["c"] > vwap, r["c"] > pp
            gate = "AND_BULL" if (above_vwap and above_pp) else ("AND_BEAR" if (not above_vwap and not above_pp) else "MIXED")
            rows2b.append((gate, fwd))
    from collections import defaultdict
    g = defaultdict(list)
    for gate, fwd in rows2b:
        g[gate].append(fwd)
    out2b = {k: {"n": len(v), "mean_fwd_ret": round(st.mean(v), 3)} for k, v in g.items() if v}
    results["component_2b_vwap_pivot_and_gate"] = {"n_total": len(rows2b), "by_gate": out2b,
                                                    "real_days": len(days)}
    print(f"  n={len(rows2b)} real 1-min observations across {len(days)} real sessions: {out2b}")

# ============================================================ COMPONENT 3
# Multi-TF S/R confluence: does a 5m swing level near a 30m swing level
# ("confluence") bounce more reliably than a 5m-only level?
print("\n=== Component 3: Multi-TF S/R confluence (5m swing near a 30m swing) ===")
sw5 = swings(bars5, left=2, right=2)
sw30 = swings(bars30, left=2, right=2)
sw30_prices = sorted(p.price for p in sw30)


def _near_htf(price, tol_pct=0.03):
    for hp in sw30_prices:
        if abs(price - hp) <= hp * tol_pct / 100:
            return True
    return False


rows3 = []
for p in sw5:
    i = p.index
    fwd = _fwd_ret(bars5, i)
    if fwd is None:
        continue
    reacted_favorably = (fwd > 0) if p.kind == "L" else (fwd < 0)   # bounce off a low -> up; rejection at a high -> down
    confluence = _near_htf(p.price)
    rows3.append((confluence, reacted_favorably))
mid = len(rows3) // 2
def _conf_stats(rows):
    conf = [r for c, r in rows if c]
    solo = [r for c, r in rows if not c]
    return {"n_confluence": len(conf), "bounce_rate_confluence": round(sum(conf) / len(conf), 4) if conf else None,
           "n_single_tf": len(solo), "bounce_rate_single_tf": round(sum(solo) / len(solo), 4) if solo else None}
s3_all = _conf_stats(rows3)
s3_h1 = _conf_stats(rows3[:mid])
s3_h2 = _conf_stats(rows3[mid:])
results["component_3_sr_confluence"] = {"all": s3_all, "first_half": s3_h1, "second_half": s3_h2}
print(f"  all: {s3_all}")
print(f"  1st half: {s3_h1}")
print(f"  2nd half: {s3_h2}")

# ============================================================ COMPONENT 4
# T1/T2/T3 = 2R/3R/4R ladder hit-rates, structural SL = the broken swing's
# opposite extreme (same philosophy as liquidity_sweep/risk.py's
# sweep_extreme-based stop -- not re-derived, just measured against).
print("\n=== Component 4: T1(2R)/T2(3R)/T3(4R) ladder hit-rates (structural SL) ===")
rows4 = []
BREAKOUT_SEARCH = 40   # bars after swing confirmation to look for a real close beyond it
for k, p in enumerate(sw5):
    if p.kind != "H" and p.kind != "L":
        continue
    i = p.index
    # swings() confirms a point using bars[i+1..i+right] -- those bars are
    # GUARANTEED not to exceed it (that's the swing definition), so a
    # breakout can only be searched for starting AFTER the confirmation
    # window, not at i+1 (which is logically near-impossible and was this
    # script's own bug on the first run, not a real finding).
    start = i + 3
    entry_i = None
    for j in range(start, min(start + BREAKOUT_SEARCH, len(bars5))):
        if p.kind == "H" and bars5[j]["c"] > p.price:
            entry_i = j
            break
        if p.kind == "L" and bars5[j]["c"] < p.price:
            entry_i = j
            break
    if entry_i is None:
        continue
    entry = bars5[entry_i]["c"]
    direction = "UP" if p.kind == "H" else "DOWN"
    sl = p.price
    risk = abs(entry - sl)
    if risk <= 0:
        continue
    targets = {"T1_2R": entry + 2 * risk if direction == "UP" else entry - 2 * risk,
              "T2_3R": entry + 3 * risk if direction == "UP" else entry - 3 * risk,
              "T3_4R": entry + 4 * risk if direction == "UP" else entry - 4 * risk}
    hit = {"T1_2R": False, "T2_3R": False, "T3_4R": False}
    sl_hit = False
    for j in range(entry_i + 1, min(entry_i + 200, len(bars5))):
        bj = bars5[j]
        stopped = (bj["l"] <= sl) if direction == "UP" else (bj["h"] >= sl)
        if stopped:
            sl_hit = True
            break
        for tk, tv in targets.items():
            if not hit[tk]:
                reached = (bj["h"] >= tv) if direction == "UP" else (bj["l"] <= tv)
                if reached:
                    hit[tk] = True
    rows4.append({**hit, "sl_hit": sl_hit})
n4 = len(rows4)
if n4:
    rate = lambda k: round(sum(1 for r in rows4 if r[k]) / n4, 4)
    results["component_4_rr_ladder"] = {"n_setups": n4, "sl_hit_rate": rate("sl_hit"),
                                        "T1_2R_hit_rate": rate("T1_2R"), "T2_3R_hit_rate": rate("T2_3R"),
                                        "T3_4R_hit_rate": rate("T3_4R")}
    print(f"  n={n4} real structural breakout setups; SL_hit_rate={rate('sl_hit')}, "
         f"T1={rate('T1_2R')}, T2={rate('T2_3R')}, T3={rate('T3_4R')}")
else:
    results["component_4_rr_ladder"] = {"n_setups": 0}

import json
with open("data/research/price_action_engine_spec_2026-09/raw_component_results.json", "w") as f:
    json.dump(results, f, indent=2, default=str)
print("\nSaved raw results to raw_component_results.json")
