#!/usr/bin/env python3
"""
orderflow_event_anatomy.py -- RESEARCH ONLY. Event-anatomy layer.

Per the 2026-09-06 spec: reconstruct, for every abnormal price event,
  PRE-EVENT -> SPIKE -> IMMEDIATE REACTION -> ACCEPTANCE/REJECTION ->
  STRUCTURE -> CONTINUATION/REVERSAL -> MFE/MAE
and measure -- via distributions and conditional probabilities, NOT a score --
which observable footprint is most consistently associated with large
directional moves.

Hard rules honoured:
  * research only; no production change; no live pattern; no orders; nothing
    is wired to the engine / API / dashboard / config.
  * abnormality thresholds are RESEARCH FEATURES, not gates -- every candidate
    bar above a permissive floor is recorded with all raw measurements.
  * compression is a CONTINUOUS feature, never mandatory.
  * strictly causal: only completed candles, only info available at the event
    timestamp; the CSV separates KNOWN-AT-ENTRY columns from OUTCOME columns.
  * NO manual weights, NO "footprint score".
  * 4 captured sessions -> classification is INSUFFICIENT DATA, not "no edge".

Outputs:
  data/orderflow_event_anatomy.csv   -- one row per abnormal event (§15)
  stdout report                       -- §§2,5,6,7,8,9,10,12,13 + the 16 Qs

Usage: python backend/scripts/orderflow_event_anatomy.py [--symbols ...]
       [--min-range-x 1.5] [--min-vol-x 1.5] [--csv PATH]
"""
from __future__ import annotations

import argparse
import csv as _csv
import statistics as st
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import market_hub
from scripts.orderflow_sequence_research import Sess, _prof_loc, _rejection
from scripts.orderflow_spike_ledger import _load_opt_ctx, _asof, _dt

R_LEVELS = (1, 2, 3, 4, 5, 6, 8, 10)
PRE_LOOKBACKS = (3, 5, 8, 10)
POST_BARS = 5


# ================================================================ ATR (causal)
def _atr_series(clean, period=14):
    n = len(clean)
    atr = [0.0] * n
    trs = []
    prev_c = None
    run = None
    for i, b in enumerate(clean):
        tr = b["h"] - b["l"]
        if prev_c is not None:
            tr = max(tr, abs(b["h"] - prev_c), abs(b["l"] - prev_c))
        trs.append(tr)
        if i + 1 < period:
            run = st.fmean(trs)
        elif i + 1 == period:
            run = st.fmean(trs[:period])
        else:
            run = (run * (period - 1) + tr) / period
        atr[i] = run or 0.0
        prev_c = b["c"]
    return atr


# ================================================================ pre-event
def _pre_features(s, atr, idx):
    out = {}
    for lb in PRE_LOOKBACKS:
        if idx < lb:
            for k in ("avg_rng", "med_rng", "rng_contraction", "vol_contraction",
                      "atr_compression"):
                out[f"pre{lb}_{k}"] = None
            continue
        w = s.clean[idx - lb:idx]
        rr = [x["h"] - x["l"] for x in w if x["h"] > x["l"]]
        vv = [x["v"] for x in w if x["v"]]
        span = max(x["h"] for x in w) - min(x["l"] for x in w)
        base = s.base[idx] or 1e-9
        out[f"pre{lb}_avg_rng"] = round(st.fmean(rr), 2) if rr else None
        out[f"pre{lb}_med_rng"] = round(st.median(rr), 2) if rr else None
        # contraction: window span vs lb * baseline single-bar range (small = coiled)
        out[f"pre{lb}_rng_contraction"] = round(span / (lb * base), 3)
        # volume contraction: window mean vol vs causal avg vol (small = drying up)
        out[f"pre{lb}_vol_contraction"] = (round(st.fmean(vv) / s.avgvol[idx], 3)
                                           if vv and s.avgvol[idx] > 0 else None)
        out[f"pre{lb}_atr_compression"] = (round((st.fmean(rr) / atr[idx]), 3)
                                           if rr and atr[idx] > 0 else None)
    # distance to prior swing / profile (causal)
    fr_hi = next((s.clean[j]["h"] for j in range(idx - 2, 0, -1) if s.frac_hi[j]), None)
    fr_lo = next((s.clean[j]["l"] for j in range(idx - 2, 0, -1) if s.frac_lo[j]), None)
    c = s.clean[idx]["c"]
    out["dist_prev_swing_hi"] = round(fr_hi - c, 2) if fr_hi is not None else None
    out["dist_prev_swing_lo"] = round(c - fr_lo, 2) if fr_lo is not None else None
    return out


# ================================================================ spike anatomy
def _classify_spike(b):
    rng = b["h"] - b["l"]
    o, c = b.get("o"), b.get("c")
    if rng <= 0 or o is None or c is None:
        return "neutral", 0.0, 0.0, 0.0
    body = abs(c - o)
    uw = b["h"] - max(o, c)
    lw = min(o, c) - b["l"]
    body_pct = body / rng
    up = c > o
    if lw >= 1.0 * (body or 1e-9) and (c - b["l"]) / rng >= 0.6:
        cl = "bullish_rejection"
    elif uw >= 1.0 * (body or 1e-9) and (b["h"] - c) / rng >= 0.6:
        cl = "bearish_rejection"
    elif body_pct >= 0.55 and up:
        cl = "bullish_expansion"
    elif body_pct >= 0.55 and not up:
        cl = "bearish_expansion"
    else:
        cl = "neutral"
    return cl, round(body_pct, 3), round(uw, 2), round(lw, 2)


# ================================================================ liquidity / levels
def _levels(s, idx):
    va = s.va[idx]
    fr_hi = next((s.clean[j]["h"] for j in range(idx - 2, 0, -1) if s.frac_hi[j]), None)
    fr_lo = next((s.clean[j]["l"] for j in range(idx - 2, 0, -1) if s.frac_lo[j]), None)
    sess_hi = max(x["h"] for x in s.clean[:idx]) if idx else None
    sess_lo = min(x["l"] for x in s.clean[:idx]) if idx else None
    prev_hi = s.clean[idx - 1]["h"] if idx else None
    prev_lo = s.clean[idx - 1]["l"] if idx else None
    return {
        "vah": va[2] if va else None, "poc": va[1] if va else None,
        "val": va[0] if va else None, "swing_hi": fr_hi, "swing_lo": fr_lo,
        "sess_hi": sess_hi, "sess_lo": sess_lo, "prev_hi": prev_hi, "prev_lo": prev_lo,
    }


def _level_interaction(s, idx, direction, lv):
    """A broke / B swept+returned / C broke+accepted / D failed-immediately /
    E open-space, judged against the nearest opposing level in `direction`."""
    b = s.clean[idx]
    c = s.clean
    if direction == "LONG":
        refs = [lv[k] for k in ("prev_hi", "swing_hi", "vah", "sess_hi") if lv.get(k) is not None]
        refs = [r for r in refs if r <= b["h"] + 1e-9]
        if not refs:
            return "E_open_space", None
        L = max(refs)
        broke = b["h"] > L
        closed_above = b["c"] > L
        nb = c[idx + 1] if idx + 1 < len(c) else None
        back = (nb is not None and nb["c"] < L)
        cont = (nb is not None and nb["h"] > b["h"])
    else:
        refs = [lv[k] for k in ("prev_lo", "swing_lo", "val", "sess_lo") if lv.get(k) is not None]
        refs = [r for r in refs if r >= b["l"] - 1e-9]
        if not refs:
            return "E_open_space", None
        L = min(refs)
        broke = b["l"] < L
        closed_above = b["c"] < L
        nb = c[idx + 1] if idx + 1 < len(c) else None
        back = (nb is not None and nb["c"] > L)
        cont = (nb is not None and nb["l"] < b["l"])
    if not broke:
        return "E_open_space", round(abs(L - b["c"]), 2)
    if broke and closed_above and cont:
        return "C_broke_accepted", round(abs(L - b["c"]), 2)
    if broke and back:
        return "B_swept_returned", round(abs(L - b["c"]), 2)
    if broke and not closed_above:
        return "D_failed_immediately", round(abs(L - b["c"]), 2)
    return "A_broke", round(abs(L - b["c"]), 2)


# ================================================================ OI context
def _oi_ctx(opt_ctx, ref_price, bar_ts):
    res = {}
    for ot in ("CE", "PE"):
        strikes = sorted({k[0] for k in opt_ctx if k[1] == ot and opt_ctx[k]})
        if not strikes:
            res[ot] = (None, None, None)
            continue
        k = (min(strikes, key=lambda x: abs(x - ref_price)), ot)
        ser = opt_ctx[k]
        oi_now = _asof(ser, bar_ts, 2)
        prev_ts = (_dt(bar_ts) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        oi_prev = _asof(ser, prev_ts, 2)
        d = (oi_now - oi_prev) if (oi_now is not None and oi_prev is not None) else None
        res[ot] = (k[0], oi_now, d)
    ce_k, ce_oi, ce_d = res["CE"]
    pe_k, pe_oi, pe_d = res["PE"]
    return {"ce_strike": ce_k, "pe_strike": pe_k, "ce_oi": ce_oi, "pe_oi": pe_oi,
            "ce_oi_chg": ce_d, "pe_oi_chg": pe_d}


def _oi_class(price_dir, rel_oi_chg):
    if rel_oi_chg is None:
        return "ambiguous"
    if abs(rel_oi_chg) < 1e-6:
        return "ambiguous"
    up = price_dir == "LONG"
    oi_up = rel_oi_chg > 0
    return f"price_{'up' if up else 'down'}_OI_{'up' if oi_up else 'down'}"


# ================================================================ post-spike + walk
def _post_features(s, idx, direction):
    c = s.clean
    b = c[idx]
    feats = {}
    ext_hi = b["h"]
    ext_lo = b["l"]
    ret_into = False
    broke_extreme = False
    for k in range(1, POST_BARS + 1):
        j = idx + k
        if j >= len(c):
            feats[f"n{k}_dir"] = None
            continue
        nb = c[j]
        ext_hi = max(ext_hi, nb["h"])
        ext_lo = min(ext_lo, nb["l"])
        up = (nb.get("c") or 0) >= (nb.get("o") or 0)
        feats[f"n{k}_dir"] = "UP" if up else "DOWN"
        if direction == "LONG":
            if nb["l"] <= b["l"]:
                ret_into = True
            if nb["h"] > b["h"]:
                broke_extreme = True
        else:
            if nb["h"] >= b["h"]:
                ret_into = True
            if nb["l"] < b["l"]:
                broke_extreme = True
    feats["post_returned_into_spike"] = ret_into
    feats["post_broke_spike_extreme"] = broke_extreme
    feats["post_ext_up"] = round(ext_hi - b["c"], 2)
    feats["post_ext_dn"] = round(b["c"] - ext_lo, 2)
    # structure within POST_BARS
    formed = None
    for k in range(1, POST_BARS + 1):
        j = idx + k
        if j >= len(c) - 1:
            break
        if direction == "LONG" and s.frac_lo[j] and c[j]["l"] > b["l"]:
            formed = k
            break
        if direction == "SHORT" and s.frac_hi[j] and c[j]["h"] < b["h"]:
            formed = k
            break
    feats["structure_bar"] = formed
    return feats


def _acceptance(s, idx, direction, level_price, window):
    if level_price is None:
        return "NA"
    c = s.clean
    beyond = 0
    for k in range(1, window + 1):
        j = idx + k
        if j >= len(c):
            break
        ok = (c[j]["c"] > level_price) if direction == "LONG" else (c[j]["c"] < level_price)
        beyond += 1 if ok else 0
    if beyond == window:
        return "ACCEPTANCE"
    if beyond == 0:
        return "REJECTION"
    return "MIXED"


def _walk(clean, ebar, entry, stop, direction):
    """MFE/MAE (pts + R), max_R, and the first bar-offset each R level is
    reached BEFORE the stop is hit. Returns None if R<=0."""
    R = abs(entry - stop)
    if R <= 0:
        return None
    mfe = mae = 0.0
    reached = {k: None for k in R_LEVELS}
    stopped = None
    for i in range(ebar, len(clean)):
        b = clean[i]
        fav = (b["h"] - entry) if direction == "LONG" else (entry - b["l"])
        adv = (b["l"] - entry) if direction == "LONG" else (entry - b["h"])
        hit_stop = (b["l"] <= stop) if direction == "LONG" else (b["h"] >= stop)
        if stopped is None:
            mfe = max(mfe, fav)
            mae = min(mae, adv)
            for k in R_LEVELS:
                if reached[k] is None and fav / R >= k:
                    reached[k] = i - ebar
            if hit_stop:
                stopped = i - ebar
    final = ((clean[-1]["c"] - entry) if direction == "LONG"
             else (entry - clean[-1]["c"]))
    return {
        "R_pts": round(R, 2), "MFE": round(mfe, 2), "MAE": round(mae, 2),
        "MFE_R": round(mfe / R, 2), "MAE_R": round(mae / R, 2),
        "max_R": round(mfe / R, 2), "final_R": round(final / R, 2),
        "stop_bar": stopped,
        **{f"r{k}_bar": reached[k] for k in R_LEVELS},
        **{f"reached_{k}R": reached[k] is not None for k in R_LEVELS},
    }


def _available_R(lv, entry, R, direction):
    if not R:
        return None
    if direction == "LONG":
        opp = [lv[k] for k in ("vah", "swing_hi", "sess_hi") if lv.get(k) and lv[k] > entry]
    else:
        opp = [lv[k] for k in ("val", "swing_lo", "sess_lo") if lv.get(k) and lv[k] < entry]
    if not opp:
        return None
    return round(abs(min(opp, key=lambda v: abs(v - entry)) - entry) / R, 2)


# ================================================================ build events
KNOWN_COLS = [
    "timestamp", "symbol", "session", "regime", "event_direction",
    "spike_class", "open", "high", "low", "close", "range", "body", "body_pct",
    "upper_wick", "lower_wick", "wick_body_ratio", "range_x", "range_pctile",
    "range_atr", "displacement_x",
    "volume", "vol_x", "vol_pctile", "vol_accel",
    "ce_strike", "pe_strike", "ce_oi", "pe_oi", "ce_oi_chg", "pe_oi_chg",
    "oi_class", "rel_oi_chg",
    "profile_loc", "profile_class", "dist_poc", "dist_vah", "dist_val",
    "dist_sess_hi", "dist_sess_lo", "dist_prev_swing_hi", "dist_prev_swing_lo",
    "level_interaction", "dist_broken_level",
    "pre5_rng_contraction", "pre8_rng_contraction", "pre5_vol_contraction",
    "pre5_atr_compression", "pre10_rng_contraction",
    "entry_spike", "entry_rej", "sl_spike", "sl_rej", "sl_prevstruct",
    "R_spike_sl", "R_rej_sl", "avail_R_spike_sl", "avail_R_rej_sl",
]
OUTCOME_COLS = [
    "accept_1", "accept_2", "accept_3",
    "post_returned_into_spike", "post_broke_spike_extreme", "structure_bar",
    "n1_dir", "n2_dir", "n3_dir",
    "MFE", "MAE", "MFE_R", "MAE_R", "max_R", "final_R", "stop_bar",
    "reached_1R", "reached_2R", "reached_3R", "reached_4R", "reached_5R",
    "reached_6R", "reached_8R", "reached_10R",
    "outcome_class",
]
EVENT_FIELDS = KNOWN_COLS + OUTCOME_COLS


def build_events(symbols, min_range_x, min_vol_x):
    rows = []
    for sym in symbols:
        for d in sorted(market_hub.session_dates(sym, limit=400)):
            s = Sess(sym, d)
            if len(s.clean) < 14:
                continue
            opt_ctx = _load_opt_ctx(sym, d)
            atr = _atr_series(s.clean)
            for idx in range(4, len(s.clean) - 1):
                if s.base[idx] <= 0:
                    continue
                b = s.clean[idx]
                rng = b["h"] - b["l"]
                rx = rng / s.base[idx]
                vx = (b["v"] / s.avgvol[idx]) if (s.avgvol[idx] > 0 and b["v"]) else None
                if rx < min_range_x and not (vx and vx >= min_vol_x):
                    continue
                cls, body_pct, uw, lw = _classify_spike(b)
                direction = ("LONG" if cls in ("bullish_expansion", "bullish_rejection")
                             else "SHORT" if cls in ("bearish_expansion", "bearish_rejection")
                             else ("LONG" if (b.get("c") or 0) >= (b.get("o") or 0) else "SHORT"))
                lv = _levels(s, idx)
                li, dbl = _level_interaction(s, idx, direction, lv)
                loc, dpoc, dvah, dval = _prof_loc(s, idx, b["c"])
                # profile_class
                if loc == "NA":
                    pcls = "NA"
                elif loc == "ABOVE_VA":
                    pcls = "acceptance_outside_value" if li == "C_broke_accepted" else "above_VAH"
                elif loc == "BELOW_VA":
                    pcls = "acceptance_outside_value" if li == "C_broke_accepted" else "below_VAL"
                elif loc == "AT_POC":
                    pcls = "near_POC"
                else:
                    pcls = "inside_value"
                pre = _pre_features(s, atr, idx)
                oic = _oi_ctx(opt_ctx, b["c"], b["bar_start"])
                rel_oi = oic["ce_oi_chg"] if direction == "LONG" else oic["pe_oi_chg"]
                prior_ranges = [x["h"] - x["l"] for x in s.clean[:idx] if x["h"] > x["l"]]
                prior_vols = [x["v"] for x in s.clean[:idx] if x["v"]]
                rpct = (round(sum(1 for x in prior_ranges if x <= rng) / len(prior_ranges), 3)
                        if len(prior_ranges) >= 10 else None)
                vpct = (round(sum(1 for x in prior_vols if x <= b["v"]) / len(prior_vols), 3)
                        if (b["v"] and len(prior_vols) >= 10) else None)
                vaccel = (round(b["v"] / s.clean[idx - 1]["v"], 2)
                          if (b["v"] and s.clean[idx - 1]["v"]) else None)
                disp = abs((b.get("c") or 0) - (b.get("o") or 0)) / (atr[idx] or 1e-9)

                # candidate entries / stops
                e_spike = b["c"]
                sl_spike = b["l"] if direction == "LONG" else b["h"]
                rej_j = next((j for j in range(idx, min(len(s.clean), idx + 3))
                              if _rejection(s.clean[j], direction.replace("LONG", "BUY").replace("SHORT", "SELL"))), None)
                e_rej = s.clean[rej_j]["c"] if rej_j is not None else None
                sl_rej = (s.clean[rej_j]["l"] if direction == "LONG" else s.clean[rej_j]["h"]) if rej_j is not None else None
                sl_prev = lv["swing_lo"] if direction == "LONG" else lv["swing_hi"]

                w_spike = _walk(s.clean, idx + 1, e_spike, sl_spike, direction)
                w_rej = (_walk(s.clean, rej_j + 1, e_rej, sl_rej, direction)
                         if (rej_j is not None and rej_j + 1 < len(s.clean)) else None)
                headline = w_spike or w_rej
                if headline is None:
                    continue

                # outcome classification
                if headline["reached_2R"] and not (headline["stop_bar"] is not None
                                                   and headline["r2_bar"] is not None
                                                   and headline["stop_bar"] < headline["r2_bar"]):
                    oc = "CONTINUATION"
                elif headline["MAE_R"] <= -1.0:
                    oc = "REVERSAL"
                else:
                    oc = "CHOP"

                row = {
                    "timestamp": b["bar_start"], "symbol": sym, "session": d,
                    "regime": s.regime, "event_direction": direction, "spike_class": cls,
                    "open": b.get("o"), "high": b["h"], "low": b["l"], "close": b["c"],
                    "range": round(rng, 2), "body": round(abs((b.get("c") or 0) - (b.get("o") or 0)), 2),
                    "body_pct": body_pct, "upper_wick": uw, "lower_wick": lw,
                    "wick_body_ratio": round((uw + lw) / (abs((b.get("c") or 0) - (b.get("o") or 0)) or 1e-9), 2),
                    "range_x": round(rx, 2), "range_pctile": rpct,
                    "range_atr": round(rng / atr[idx], 2) if atr[idx] > 0 else None,
                    "displacement_x": round(disp, 2),
                    "volume": b["v"], "vol_x": round(vx, 2) if vx else None,
                    "vol_pctile": vpct, "vol_accel": vaccel,
                    "ce_strike": oic["ce_strike"], "pe_strike": oic["pe_strike"],
                    "ce_oi": oic["ce_oi"], "pe_oi": oic["pe_oi"],
                    "ce_oi_chg": oic["ce_oi_chg"], "pe_oi_chg": oic["pe_oi_chg"],
                    "oi_class": _oi_class(direction, rel_oi), "rel_oi_chg": rel_oi,
                    "profile_loc": loc, "profile_class": pcls,
                    "dist_poc": dpoc, "dist_vah": dvah, "dist_val": dval,
                    "dist_sess_hi": round(lv["sess_hi"] - b["c"], 2) if lv["sess_hi"] else None,
                    "dist_sess_lo": round(b["c"] - lv["sess_lo"], 2) if lv["sess_lo"] else None,
                    "dist_prev_swing_hi": pre["dist_prev_swing_hi"],
                    "dist_prev_swing_lo": pre["dist_prev_swing_lo"],
                    "level_interaction": li, "dist_broken_level": dbl,
                    "pre5_rng_contraction": pre["pre5_rng_contraction"],
                    "pre8_rng_contraction": pre["pre8_rng_contraction"],
                    "pre10_rng_contraction": pre["pre10_rng_contraction"],
                    "pre5_vol_contraction": pre["pre5_vol_contraction"],
                    "pre5_atr_compression": pre["pre5_atr_compression"],
                    "entry_spike": round(e_spike, 2),
                    "entry_rej": round(e_rej, 2) if e_rej is not None else None,
                    "sl_spike": round(sl_spike, 2),
                    "sl_rej": round(sl_rej, 2) if sl_rej is not None else None,
                    "sl_prevstruct": round(sl_prev, 2) if sl_prev is not None else None,
                    "R_spike_sl": w_spike["R_pts"] if w_spike else None,
                    "R_rej_sl": w_rej["R_pts"] if w_rej else None,
                    "avail_R_spike_sl": _available_R(lv, e_spike, w_spike["R_pts"] if w_spike else None, direction),
                    "avail_R_rej_sl": _available_R(lv, e_rej, w_rej["R_pts"] if w_rej else None, direction) if e_rej else None,
                    # ---- OUTCOME ----
                    "accept_1": _acceptance(s, idx, direction, dbl and b["c"], 1),
                    "accept_2": _acceptance(s, idx, direction, dbl and b["c"], 2),
                    "accept_3": _acceptance(s, idx, direction, dbl and b["c"], 3),
                    "post_returned_into_spike": None, "post_broke_spike_extreme": None,
                    "structure_bar": None, "n1_dir": None, "n2_dir": None, "n3_dir": None,
                    "MFE": headline["MFE"], "MAE": headline["MAE"],
                    "MFE_R": headline["MFE_R"], "MAE_R": headline["MAE_R"],
                    "max_R": headline["max_R"], "final_R": headline["final_R"],
                    "stop_bar": headline["stop_bar"],
                    **{f"reached_{k}R": headline[f"reached_{k}R"] for k in R_LEVELS},
                    "outcome_class": oc,
                }
                pf = _post_features(s, idx, direction)
                row.update({k: pf[k] for k in ("post_returned_into_spike",
                            "post_broke_spike_extreme", "structure_bar",
                            "n1_dir", "n2_dir", "n3_dir")})
                rows.append(row)
    return rows


# ================================================================ analysis
def _p(rows, key):
    v = [r for r in rows if r.get(key)]
    return round(len(v) / len(rows), 3) if rows else None


def _grp(rows, field):
    g = {}
    for r in rows:
        g.setdefault(r.get(field), []).append(r)
    return g


def _line(name, rs):
    if not rs:
        return f"    {name:<26} n=0"
    mfe = sorted(r["MFE_R"] for r in rs)
    mae = sorted(r["MAE_R"] for r in rs)
    return (f"    {name:<26} n={len(rs):>4}  medMFE_R={mfe[len(mfe)//2]:>5}  "
            f"medMAE_R={mae[len(mae)//2]:>6}  P1R={_p(rs,'reached_1R')*100:>3.0f}%  "
            f"P2R={_p(rs,'reached_2R')*100:>3.0f}%  P3R={_p(rs,'reached_3R')*100:>3.0f}%  "
            f"P5R={_p(rs,'reached_5R')*100:>3.0f}%  P8R={_p(rs,'reached_8R')*100:>3.0f}%  "
            f"cont={sum(1 for r in rs if r['outcome_class']=='CONTINUATION')/len(rs)*100:>3.0f}%")


def analyse(rows):
    for sym in sorted({r["symbol"] for r in rows}):
        rs = [r for r in rows if r["symbol"] == sym]
        sess = sorted({r["session"] for r in rs})
        regs = sorted({r["regime"] for r in rs})
        print("\n" + "#" * 110)
        print(f"# {sym}  --  {len(rs)} abnormal events  |  {len(sess)} sessions {sess}  |  regimes {regs}")
        print("#" * 110)

        print("\n[BASELINE] all events (headline = spike-close entry, spike-low/high SL):")
        print(_line("ALL", rs))
        print(f"    median R (spike SL) = {st.median([r['R_spike_sl'] for r in rs if r['R_spike_sl']]):.1f} pts ; "
              f"median available_R = {st.median([r['avail_R_spike_sl'] for r in rs if r['avail_R_spike_sl']] or [0]):.1f}")

        print("\n[Q1/Q2 abnormality] by range_x bucket (research feature, not a gate):")
        for lo, hi in ((1.5, 2.0), (2.0, 2.5), (2.5, 3.0), (3.0, 99)):
            print(_line(f"range_x {lo}-{hi if hi<99 else '+'}",
                        [r for r in rs if r["range_x"] and lo <= r["range_x"] < hi]))
        print("  by vol_x bucket:")
        for lo, hi in ((0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 99)):
            print(_line(f"vol_x {lo}-{hi if hi<99 else '+'}",
                        [r for r in rs if (r["vol_x"] or 0) and lo <= r["vol_x"] < hi]))

        print("\n[Q3 pre-spike compression] by pre5_rng_contraction quartile (lower = more coiled):")
        vals = sorted(r["pre5_rng_contraction"] for r in rs if r["pre5_rng_contraction"] is not None)
        if len(vals) >= 8:
            q = [vals[len(vals)//4], vals[len(vals)//2], vals[3*len(vals)//4]]
            buckets = [("coiled  <=Q1", lambda x: x <= q[0]),
                       ("Q1-Q2", lambda x: q[0] < x <= q[1]),
                       ("Q2-Q3", lambda x: q[1] < x <= q[2]),
                       ("loose  >Q3", lambda x: x > q[2])]
            for name, f in buckets:
                print(_line(name, [r for r in rs if r["pre5_rng_contraction"] is not None
                                   and f(r["pre5_rng_contraction"])]))
        else:
            print("    insufficient events for quartiles")

        print("\n[Q4/Q5 entry] spike-close vs rejection-candle entry (same events, both walked):")
        with_rej = [r for r in rs if r["R_rej_sl"] is not None]
        print(_line("spike-close entry (all)", rs))
        print(_line("rejection entry (subset)", with_rej))

        print("\n[Q6 acceptance vs rejection] level_interaction class:")
        for k, g in sorted(_grp(rs, "level_interaction").items()):
            print(_line(str(k), g))
        print("  acceptance window sensitivity (events that broke a level):")
        broke = [r for r in rs if r["dist_broken_level"] is not None and r["level_interaction"] != "E_open_space"]
        for w in ("accept_1", "accept_2", "accept_3"):
            for cls in ("ACCEPTANCE", "REJECTION"):
                print(_line(f"{w}={cls}", [r for r in broke if r[w] == cls]))

        print("\n[Q7 OI behaviour] forward distribution by OI class:")
        for k, g in sorted(_grp(rs, "oi_class").items()):
            print(_line(str(k), g))

        print("\n[Q8 volume] spike-only vs spike+abnormal-volume (vol_x>=2):")
        print(_line("vol_x < 2 or NA", [r for r in rs if not (r["vol_x"] and r["vol_x"] >= 2)]))
        print(_line("vol_x >= 2", [r for r in rs if r["vol_x"] and r["vol_x"] >= 2]))

        print("\n[Q9 profile location] forward distribution by profile_class:")
        for k, g in sorted(_grp(rs, "profile_class").items()):
            print(_line(str(k), g))

        print("\n[Q10 level interaction] (see Q6 table) + [Q11 structural stop]:")
        # Q11: compare spike-SL vs rejection-SL vs prev-struct SL on the rej subset
        s_sl = [r for r in with_rej if r["R_spike_sl"]]
        r_sl = [r for r in with_rej if r["R_rej_sl"]]
        if s_sl:
            print(f"    spike SL     : medR={st.median([r['R_spike_sl'] for r in s_sl]):.1f}pts "
                  f"medMAE_R={st.median([r['MAE_R'] for r in s_sl]):.2f} "
                  f"medMFE_R={st.median([r['MFE_R'] for r in s_sl]):.2f}")
        if r_sl:
            wr = [r for r in r_sl]
            print(f"    rejection SL : medR={st.median([r['R_rej_sl'] for r in wr]):.1f}pts "
                  f"(MAE_R/MFE_R computed on the rejection walk in the CSV columns)")

        print("\n[Q12-15 R-reach] P(kR before invalidation), all events, headline SL:")
        print("    " + "  ".join(f"{k}R={_p(rs, f'reached_{k}R')*100:.0f}%" for k in R_LEVELS))
        for reg in sorted({r["regime"] for r in rs}):
            g = [r for r in rs if r["regime"] == reg]
            print(f"    regime {reg:<11} " + "  ".join(f"{k}R={_p(g, f'reached_{k}R')*100:.0f}%" for k in (1, 2, 3, 5, 8)))
        for ss in sess:
            g = [r for r in rs if r["session"] == ss]
            print(f"    {ss:<12} n={len(g):>3}  " + "  ".join(f"{k}R={_p(g, f'reached_{k}R')*100:.0f}%" for k in (1, 2, 3, 5)))

        print("\n[Q16 combinations worth a second-stage test]  (effect vs baseline P3R):")
        base_p3 = _p(rs, "reached_3R") or 0
        combos = {
            "vol_x>=2": [r for r in rs if r["vol_x"] and r["vol_x"] >= 2],
            "level C_broke_accepted": [r for r in rs if r["level_interaction"] == "C_broke_accepted"],
            "OI price_up_OI_up": [r for r in rs if r["oi_class"] == "price_up_OI_up"],
            "OI price_down_OI_up": [r for r in rs if r["oi_class"] == "price_down_OI_up"],
            "coiled pre5<=0.6": [r for r in rs if (r["pre5_rng_contraction"] or 9) <= 0.6],
            "outside value (acc)": [r for r in rs if r["profile_class"] == "acceptance_outside_value"],
            "bullish/bearish_expansion": [r for r in rs if r["spike_class"].endswith("expansion")],
            "vol_x>=2 & C_broke_accepted": [r for r in rs if r["vol_x"] and r["vol_x"] >= 2
                                            and r["level_interaction"] == "C_broke_accepted"],
        }
        ranked = []
        for name, g in combos.items():
            if len(g) < 4:
                ranked.append((name, len(g), None, None))
                continue
            p3 = _p(g, "reached_3R")
            ranked.append((name, len(g), p3, round(p3 - base_p3, 3)))
        ranked.sort(key=lambda x: (x[3] is None, -(x[3] or -9)))
        print(f"    baseline P3R = {base_p3*100:.0f}%")
        for name, n, p3, eff in ranked:
            print(f"      {name:<32} n={n:>4}  P3R={('  n/a' if p3 is None else f'{p3*100:>3.0f}%')}  "
                  f"effect={('n/a' if eff is None else f'{eff*100:+.0f}pp')}")

        # ---- classification (§17/§18) ----
        n_sess = len(sess)
        n_ev = len(rs)
        print(f"\n>>> {sym} CLASSIFICATION: INSUFFICIENT DATA")
        print(f"    events={n_ev}, sessions={n_sess} (need >=10 independent sessions, >=50 events); "
              f"regimes={len(regs)}. Conditional tables above are HYPOTHESES for a "
              f"second-stage test once the sample is adequate -- not an edge.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NIFTY,NATURALGAS,CRUDEOIL")
    ap.add_argument("--min-range-x", type=float, default=1.5)
    ap.add_argument("--min-vol-x", type=float, default=1.5)
    ap.add_argument("--csv", default="data/orderflow_event_anatomy.csv")
    a = ap.parse_args()
    syms = [x.strip().upper() for x in a.symbols.split(",") if x.strip()]
    rows = build_events(syms, a.min_range_x, a.min_vol_x)
    outp = Path(a.csv)
    outp.parent.mkdir(parents=True, exist_ok=True)
    with open(outp, "w", newline="") as f:
        wr = _csv.DictWriter(f, fieldnames=EVENT_FIELDS, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)
    print(f"wrote {len(rows)} events -> {outp}  "
          f"(KNOWN-AT-ENTRY cols 1..{len(KNOWN_COLS)}, OUTCOME cols after)")
    analyse(rows)


if __name__ == "__main__":
    main()
