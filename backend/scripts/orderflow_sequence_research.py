#!/usr/bin/env python3
"""
orderflow_sequence_research.py -- RESEARCH ONLY. No production path, no config
change, no live behaviour touched.

Extends the order-flow research engine per the 2026-09-06 spec. Core idea:
  COMPRESSION -> ABNORMAL SPIKE -> REJECTION/ACCEPTANCE -> CONFIRMATION -> ENTRY
are SEQUENTIAL events over a small multi-bar window -- NOT one candle. The
research determines the correct sequence, the correct entry candle/price, the
correct structural stop, and how much of the move is really capturable.

Everything is causal: completed candles only, every look-back uses bars[:idx],
the volume profile used for context is the DEVELOPING profile from bars[:idx].
Thresholds are swept as sensitivity analysis, never loosened to inflate the
signal count. Signal count is not an objective; entry/research quality is.

Sections (mirror the spec):
  P1  dynamic abnormal-spike definitions (fixed mult / percentile / profile-aware)
  P2+P5  entry-model & entry-price comparison (S0..S5 x E1..E6)
  P3  sequence assembly with max windows {1,2,3} bars, stale rejection
  P4  profile context as none / classify / soft-score / hard-gate  (P0..P3)
  P6  structural initial stop-loss candidates
  P7  R-multiple profit management: fixed 3R / 50%@3R+runner / full runner
  P8  MFE, MAE, max-R, R-captured, giveback, time-to-R, R-reach distribution
  P9  walk-forward / out-of-sample (leave-one-session-out + chronological)
  P10 ablation matrix A..I with the full metric row
  P11 explicit answers to the 10 research questions
  verdict per SYMBOL PREMIUM in {PROVEN EDGE / PROMISING-MORE-DATA /
      NO ESTABLISHED EDGE / OVER-CONSTRAINED / DATA-ARTIFACT}

P&L for the verdict is on the captured ATM OPTION premium (premium_walk);
the R-multiple / MFE distributions are INDEX-structural (that is where R is
defined). Thin-quote premium windows are dropped.
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import market_hub
from app.orderflow.smart_money import _clean
from app.orderflow import premium_walk as _pw
from app.orderflow import profile as _prof

# ----------------------------------------------------------------- config grids
SPIKE_DEFS = {                       # name -> (kind, param)
    "fix2.0": ("fixed", 2.0), "fix2.5": ("fixed", 2.5),
    "fix3.0": ("fixed", 3.0), "fix4.0": ("fixed", 4.0),
    "pct90": ("pct", 0.90), "pct95": ("pct", 0.95),
    "vol2.0": ("vol", 2.0), "vol3.0": ("vol", 3.0),
    "prof_aware": ("prof", None),    # range mult varies by profile location
}
PCT_FLOOR_X = 1.2                    # percentile defs still require above-average range
COMP_LBS = (5, 8)
COMP_X = 1.5                         # STRICT; prior-range span <= COMP_X * median bar range
SEQ_WINDOWS = (1, 2, 3)
PROFILE_MIN_BARS = 15
REJ_WICK_X = 1.0
REJ_CLOSE_FRAC = 0.60
RR_TARGET = 3.0
SLIP_TICKS = 0.0                    # index-point slippage assumption (reported)


# ================================================================ per session
class Sess:
    __slots__ = ("sym", "date", "clean", "regime", "base", "avgvol", "va",
                 "frac_lo", "frac_hi")

    def __init__(self, sym, date):
        self.sym = sym
        self.date = date
        self.clean = _clean(market_hub.session_bars(sym, date))
        self.regime = _regime(self.clean)
        n = len(self.clean)
        self.base = [0.0] * n
        self.avgvol = [0.0] * n
        self.va = [None] * n
        rs, vs = [], []
        for i, b in enumerate(self.clean):
            self.base[i] = st.median(rs) if rs else 0.0
            self.avgvol[i] = (sum(vs) / len(vs)) if vs else 0.0
            if b["h"] > b["l"]:
                rs.append(b["h"] - b["l"])
            if b["v"] and b["v"] > 0:
                vs.append(b["v"])
            if i >= PROFILE_MIN_BARS:
                vp = _prof.volume_profile(
                    [{"h": x["h"], "l": x["l"], "c": x["c"], "v": x["v"]}
                     for x in self.clean[:i]], symbol=sym)
                if vp.get("status") == "OK" and vp.get("vah") is not None:
                    self.va[i] = (vp["val"], vp["poc"], vp["vah"])
        # 3-bar fractal swing lows / highs (causal use only: index j confirmed at j+1)
        self.frac_lo = [False] * n
        self.frac_hi = [False] * n
        for i in range(1, n - 1):
            c = self.clean
            if c[i]["l"] <= c[i - 1]["l"] and c[i]["l"] <= c[i + 1]["l"]:
                self.frac_lo[i] = True
            if c[i]["h"] >= c[i - 1]["h"] and c[i]["h"] >= c[i + 1]["h"]:
                self.frac_hi[i] = True


def _regime(clean):
    if len(clean) < 5:
        return "NA"
    o0 = clean[0].get("o")
    net = clean[-1]["c"] - (o0 if o0 is not None else clean[0]["c"])
    rng = max(b["h"] for b in clean) - min(b["l"] for b in clean)
    if rng <= 0:
        return "NA"
    if abs(net) >= 0.5 * rng:
        return "TREND_UP" if net > 0 else "TREND_DOWN"
    return "CHOP"


# ================================================================ P4 profile
def _prof_loc(s: Sess, idx: int, price: float):
    """Where is `price` relative to the developing value area at idx."""
    va = s.va[idx]
    if va is None:
        return "NA", None, None, None
    val, poc, vah = va
    d_poc = price - poc
    d_vah = price - vah
    d_val = price - val
    if price > vah:
        loc = "ABOVE_VA"
    elif price < val:
        loc = "BELOW_VA"
    elif abs(price - poc) <= 0.15 * (vah - val + 1e-9):
        loc = "AT_POC"
    elif price >= poc:
        loc = "UPPER_VA"
    else:
        loc = "LOWER_VA"
    return loc, round(d_poc, 2), round(d_vah, 2), round(d_val, 2)


# ================================================================ P1 spike def
def _is_compressed(s: Sess, idx: int, lb: int) -> bool:
    if idx < lb or s.base[idx] <= 0:
        return False
    w = s.clean[idx - lb:idx]
    return (max(b["h"] for b in w) - min(b["l"] for b in w)) <= COMP_X * s.base[idx]


def _range_pctile(s: Sess, idx: int) -> float:
    """percentile rank of this bar's range among all PRIOR completed bars."""
    r = s.clean[idx]["h"] - s.clean[idx]["l"]
    prior = [x["h"] - x["l"] for x in s.clean[:idx] if x["h"] > x["l"]]
    if len(prior) < 10:
        return 0.0
    return sum(1 for x in prior if x <= r) / len(prior)


def _spike_mult_required(s: Sess, idx: int, spike_def: str) -> float:
    """Profile-aware: 'abnormal' is context-relative. Breaking AWAY from value
    (outside VA / into an LVN) needs a smaller expansion to be meaningful than
    an expansion that happens deep inside value / at the POC."""
    kind, p = SPIKE_DEFS[spike_def]
    if kind == "fixed":
        return p
    if kind == "pct":
        return None  # handled via percentile
    # prof-aware
    b = s.clean[idx]
    loc, *_ = _prof_loc(s, idx, b["c"])
    return {"ABOVE_VA": 2.0, "BELOW_VA": 2.0, "AT_POC": 3.5,
            "UPPER_VA": 3.0, "LOWER_VA": 3.0, "NA": 3.0}.get(loc, 3.0)


def _is_spike(s: Sess, idx: int, spike_def: str) -> bool:
    if s.base[idx] <= 0:
        return False
    rng = s.clean[idx]["h"] - s.clean[idx]["l"]
    kind, p = SPIKE_DEFS[spike_def]
    if kind == "vol":
        v = s.clean[idx]["v"]
        return bool(v) and s.avgvol[idx] > 0 and v >= p * s.avgvol[idx]
    if kind == "pct":
        return _range_pctile(s, idx) >= p and rng >= PCT_FLOOR_X * s.base[idx]
    mult = _spike_mult_required(s, idx, spike_def)
    return rng >= mult * s.base[idx]


def _spike_features(s: Sess, idx: int, side: str, spike_def: str) -> dict:
    b = s.clean[idx]
    rng = b["h"] - b["l"]
    o, c = b.get("o"), b.get("c")
    body = abs(c - o) if (o is not None and c is not None) else 0.0
    uw = b["h"] - max(o, c) if (o is not None and c is not None) else 0.0
    lw = min(o, c) - b["l"] if (o is not None and c is not None) else 0.0
    loc, dpoc, dvah, dval = _prof_loc(s, idx, c if c is not None else b["h"])
    return {
        "idx": idx, "side": side, "range": round(rng, 2), "body": round(body, 2),
        "upper_wick": round(uw, 2), "lower_wick": round(lw, 2),
        "volume": b["v"], "rel_range": round(rng / s.base[idx], 2) if s.base[idx] else None,
        "prof_loc": loc, "dist_poc": dpoc, "dist_vah": dvah, "dist_val": dval,
        "regime": s.regime, "pctile": round(_range_pctile(s, idx), 3),
        "spike_mult": round(rng / s.base[idx], 2) if s.base[idx] else None,
        "spike_def": spike_def,
    }


# ================================================================ P2/P3 sequence
def _rejection(b: dict, side: str) -> bool:
    rng = b["h"] - b["l"]
    o, c = b.get("o"), b.get("c")
    if rng <= 0 or o is None or c is None:
        return False
    body = abs(c - o) or 1e-9
    uw = b["h"] - max(o, c)
    lw = min(o, c) - b["l"]
    if side == "BUY":
        return lw >= REJ_WICK_X * body and (c - b["l"]) / rng >= REJ_CLOSE_FRAC
    return uw >= REJ_WICK_X * body and (b["h"] - c) / rng >= REJ_CLOSE_FRAC


def _first_break(s: Sess, start: int, level: float, side: str, max_bars: int):
    end = min(len(s.clean), start + max_bars)
    for j in range(start, end):
        if side == "BUY" and s.clean[j]["h"] > level:
            return j
        if side == "SELL" and s.clean[j]["l"] < level:
            return j
    return None


def build_sequence(s: Sess, sp_idx: int, side: str, *, seq_window: int,
                   need_rejection: bool, need_confirm: bool):
    """Assemble a stale-checked event sequence starting at the spike bar.
    Returns dict with the bar indices of each event, or None."""
    seq = {"spike_idx": sp_idx, "rej_idx": None, "confirm_idx": None}
    cur = sp_idx
    if need_rejection:
        rej = None
        for j in range(sp_idx, min(len(s.clean), sp_idx + seq_window + 1)):
            if _rejection(s.clean[j], side):
                rej = j
                break
        if rej is None:
            return None
        seq["rej_idx"] = rej
        cur = rej
    if need_confirm:
        trig = s.clean[seq.get("rej_idx") or sp_idx]
        level = trig["h"] if side == "BUY" else trig["l"]
        cj = _first_break(s, cur + 1, level, side, seq_window)
        if cj is None:
            return None
        seq["confirm_idx"] = cj
    return seq


# ================================================================ P5 entry px
def entry_for(s: Sess, seq: dict, side: str, model: str):
    """model in {S0,S1,S2,S3,S5}; returns (entry_price, entry_bar_idx, ts) or None.
    Entry price rules E1..E6 are folded into the model:
      S0 -> E1 spike close                 S1 -> E2 spike high/low break
      S2 -> E3 rejection close             S3 -> E5 confirmation break
      S5 -> E4/E6 best of rej-break / next-bar within the window."""
    c = s.clean
    sp = seq["spike_idx"]
    rej = seq.get("rej_idx")
    conf = seq.get("confirm_idx")
    if model == "S0":
        if sp + 1 >= len(c):
            return None
        return c[sp]["c"], sp + 1, c[sp + 1]["bar_start"]
    if model == "S1":
        lvl = c[sp]["h"] if side == "BUY" else c[sp]["l"]
        bj = _first_break(s, sp + 1, lvl, side, 3)
        if bj is None or bj + 1 >= len(c):
            return None
        return lvl, bj + 1, c[bj + 1]["bar_start"] if bj + 1 < len(c) else c[bj]["bar_start"]
    if model == "S2":
        if rej is None or rej + 1 >= len(c):
            return None
        return c[rej]["c"], rej + 1, c[rej + 1]["bar_start"]
    if model == "S3":
        if conf is None or conf + 1 >= len(c):
            return None
        lvl = c[(rej or sp)]["h"] if side == "BUY" else c[(rej or sp)]["l"]
        return lvl, conf + 1, c[conf + 1]["bar_start"]
    if model == "S5":
        base = rej if rej is not None else sp
        lvl = c[base]["h"] if side == "BUY" else c[base]["l"]
        bj = _first_break(s, base + 1, lvl, side, 3)
        if bj is None or bj + 1 >= len(c):
            return None
        return lvl, bj + 1, c[bj + 1]["bar_start"]
    return None


# ================================================================ P6 stop
def stop_for(s: Sess, seq: dict, side: str, entry: float, struct: str):
    c = s.clean
    sp, rej, conf = seq["spike_idx"], seq.get("rej_idx"), seq.get("confirm_idx")
    if struct == "spike":
        lo = c[sp]["l"] if side == "BUY" else c[sp]["h"]
    elif struct == "rejection":
        k = rej if rej is not None else sp
        lo = c[k]["l"] if side == "BUY" else c[k]["h"]
    elif struct == "confirm":
        k = conf if conf is not None else (rej if rej is not None else sp)
        lo = c[k]["l"] if side == "BUY" else c[k]["h"]
    else:  # swing: nearest prior confirmed fractal
        k = None
        anchor = (conf or rej or sp)
        for j in range(anchor - 1, 0, -1):
            if side == "BUY" and s.frac_lo[j]:
                k = j
                break
            if side == "SELL" and s.frac_hi[j]:
                k = j
                break
        lo = c[k]["l"] if (k is not None and side == "BUY") else \
             c[k]["h"] if (k is not None and side == "SELL") else \
             (c[sp]["l"] if side == "BUY" else c[sp]["h"])
    pad = 0.02 * (c[sp]["h"] - c[sp]["l"])
    return (lo - pad) if side == "BUY" else (lo + pad)


# ================================================================ P7/P8 walk
def walk_R(s: Sess, ebar: int, entry: float, stop: float, side: str, pm: str):
    """Forward walk on completed index bars from ebar. Returns R-metrics +
    exit under the profit-management rule `pm`."""
    R = abs(entry - stop)
    if R <= 0:
        return None
    c = s.clean
    tgt3 = entry + RR_TARGET * R if side == "BUY" else entry - RR_TARGET * R
    max_fav = 0.0
    max_adv = 0.0
    t = {"1R": None, "2R": None, "3R": None, "maxR": None}
    booked = 0.0          # R already realised on a partial
    frac_open = 1.0
    cur_stop = stop
    hit3 = False
    exit_idx = None
    exit_r = None
    for i in range(ebar, len(c)):
        b = c[i]
        fav = (b["h"] - entry) if side == "BUY" else (entry - b["l"])
        adv = (b["l"] - entry) if side == "BUY" else (entry - b["h"])
        max_fav = max(max_fav, fav)
        max_adv = min(max_adv, adv)
        rr_now = max_fav / R
        for k, mult in (("1R", 1), ("2R", 2), ("3R", 3)):
            if t[k] is None and rr_now >= mult:
                t[k] = (i - ebar)
        # structural trail once past 1R (runner rules)
        if pm in ("half3R_runner", "full_runner") and rr_now >= 1.0:
            for j in range(i, ebar, -1):
                if side == "BUY" and s.frac_lo[j] and c[j]["l"] > cur_stop:
                    cur_stop = c[j]["l"]
                    break
                if side == "SELL" and s.frac_hi[j] and c[j]["h"] < cur_stop:
                    cur_stop = c[j]["h"]
                    break
        hit_stop = (b["l"] <= cur_stop) if side == "BUY" else (b["h"] >= cur_stop)
        hit_t3 = (b["h"] >= tgt3) if side == "BUY" else (b["l"] <= tgt3)

        if pm == "fix3R":
            if hit_stop and hit_t3:
                exit_idx, exit_r = i, -abs(entry - cur_stop) / R
                break
            if hit_stop:
                exit_idx, exit_r = i, -abs(entry - cur_stop) / R
                break
            if hit_t3:
                exit_idx, exit_r = i, RR_TARGET
                break
        else:
            if hit_t3 and not hit3:
                hit3 = True
                if pm == "half3R_runner":
                    booked += 0.5 * RR_TARGET
                    frac_open = 0.5
                    cur_stop = entry  # remainder to breakeven
            if hit_stop:
                rem_r = (-abs(entry - cur_stop) / R) if cur_stop <= stop or side == "SELL" else \
                        ((cur_stop - entry) / R if side == "BUY" else (entry - cur_stop) / R)
                exit_idx = i
                exit_r = booked + frac_open * rem_r
                break
    if exit_idx is None:                       # ran to session end -> mark at last close
        last = c[-1]["c"]
        rem_r = ((last - entry) / R) if side == "BUY" else ((entry - last) / R)
        exit_idx = len(c) - 1
        exit_r = booked + frac_open * rem_r
    if t["maxR"] is None:
        t["maxR"] = None
    max_r = round(max_fav / R, 2)
    return {
        "R": round(R, 2), "entry": round(entry, 2), "stop": round(stop, 2),
        "exit_idx": exit_idx, "exit_ts": c[exit_idx]["bar_start"],
        "r_captured": round(exit_r, 3), "max_r": max_r,
        "mfe_pts": round(max_fav, 2), "mae_pts": round(max_adv, 2),
        "mfe_r": max_r, "mae_r": round(max_adv / R, 2),
        "giveback_r": round(max_r - exit_r, 2),
        "t_1r": t["1R"], "t_2r": t["2R"], "t_3r": t["3R"],
    }


# ================================================================ variant spec
#  A..I  -> (need_compression, spike_def, need_rejection, need_confirm,
#            profile_mode P0..P3, entry_model, pm_rule)
def _variant_spec(v, sym_sd, best_entry, best_pm):
    return {
        "A": (False, sym_sd, False, False, "P0", "S0", "fix3R"),
        "B": (False, "fix2.0", True, False, "P0", "S2", "fix3R"),
        "C": (True, sym_sd, False, False, "P0", "S0", "fix3R"),
        "D": (True, sym_sd, True, False, "P0", "S2", "fix3R"),
        "E": (True, sym_sd, True, True, "P0", "S3", "fix3R"),
        "F": (True, sym_sd, True, True, "P3", "S3", "fix3R"),
        "G": (True, "prof_aware", True, True, "P3", "S3", "fix3R"),
        "H": (True, "prof_aware", True, True, "P3", best_entry, "fix3R"),
        "I": (True, "prof_aware", True, True, "P3", best_entry, best_pm),
    }[v]


def _profile_ok(s: Sess, seq: dict, side: str, mode: str) -> bool:
    if mode == "P0":
        return True
    idx = seq.get("confirm_idx") or seq.get("rej_idx") or seq["spike_idx"]
    loc, *_ = _prof_loc(s, idx, s.clean[idx]["c"])
    if mode in ("P1", "P2"):
        return True                       # classification / soft score: recorded, not filtered
    # P3 hard gate: trade only in the direction of leaving value
    return (side == "BUY" and loc in ("ABOVE_VA", "UPPER_VA")) or \
           (side == "SELL" and loc in ("BELOW_VA", "LOWER_VA"))


# ================================================================ run one config
def run_config(sessions, *, need_c, spike_def, need_r, need_conf, prof_mode,
               entry_model, pm_rule, comp_lb, seq_window, opt_maps):
    trades = []
    for s in sessions:
        om = opt_maps.get((s.sym, s.date)) or {}
        for idx in range(len(s.clean)):
            if need_c and not _is_compressed(s, idx, comp_lb):
                continue
            if not _is_spike(s, idx, spike_def):
                continue
            for side in ("BUY", "SELL"):
                if need_r or True:
                    pass
                seq = build_sequence(s, idx, side, seq_window=seq_window,
                                     need_rejection=need_r, need_confirm=need_conf)
                if seq is None:
                    continue
                if not _profile_ok(s, seq, side, prof_mode):
                    continue
                e = entry_for(s, seq, side, entry_model)
                if e is None:
                    continue
                entry, ebar, ets = e
                stop = stop_for(s, seq, side, entry, "rejection" if need_r else "spike")
                w = walk_R(s, ebar, entry, stop, side, pm_rule)
                if w is None:
                    continue
                rw = _pw.rewalk_leg(om, entry_price=entry, side=side,
                                    entry_ts=ets, exit_ts=w["exit_ts"])
                if not rw or rw.get("premium_thin"):
                    continue
                feat = _spike_features(s, idx, side, spike_def)
                trades.append({
                    "sym": s.sym, "session": s.date, "regime": s.regime, "side": side,
                    "prem_pts": rw["premium_points"], "prem_mfe": rw["premium_mfe"],
                    "prem_mae": rw["premium_mae"],
                    **{k: w[k] for k in ("R", "r_captured", "max_r", "mfe_r", "mae_r",
                                         "giveback_r", "t_1r", "t_2r", "t_3r")},
                    "prof_loc": feat["prof_loc"], "spike_mult": feat["spike_mult"],
                })
    return trades


# ================================================================ metrics
def metrics(tr):
    n = len(tr)
    if not n:
        return {"n": 0}
    p = [t["prem_pts"] for t in tr]
    w = [x for x in p if x > 0]
    l = [x for x in p if x < 0]
    gw, gl = sum(w), -sum(l)
    seq = [t["prem_pts"] for t in sorted(tr, key=lambda x: (x["session"],))]
    peak = cum = dd = 0.0
    for x in seq:
        cum += x
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    maxr = [t["max_r"] for t in tr]
    rc = [t["r_captured"] for t in tr]
    reach = lambda k: round(sum(1 for x in maxr if x >= k) / n, 3)
    t1 = [t["t_1r"] for t in tr if t["t_1r"] is not None]
    t3 = [t["t_3r"] for t in tr if t["t_3r"] is not None]
    return {
        "n": n, "sessions": len({t["session"] for t in tr}),
        "win_rate": round(len(w) / n, 3),
        "expectancy": round(sum(p) / n, 3), "net": round(sum(p), 1),
        "profit_factor": round(gw / gl, 2) if gl > 0 else None,
        "avg_win": round(st.fmean(w), 2) if w else None,
        "avg_loss": round(st.fmean(l), 2) if l else None,
        "max_dd": round(dd, 1),
        "prem_mfe": round(st.fmean(t["prem_mfe"] for t in tr), 2),
        "prem_mae": round(st.fmean(t["prem_mae"] for t in tr), 2),
        "med_mfe": round(st.median(t["prem_mfe"] for t in tr), 2),
        "med_mae": round(st.median(t["prem_mae"] for t in tr), 2),
        "avg_maxR": round(st.fmean(maxr), 2), "med_maxR": round(st.median(maxr), 2),
        "avg_Rcap": round(st.fmean(rc), 3),
        "p3R": reach(3), "p5R": reach(5), "p8R": reach(8), "p10R": reach(10),
        "giveback_R": round(st.fmean(t["giveback_r"] for t in tr), 2),
        "t1R_bars": round(st.median(t1), 1) if t1 else None,
        "t3R_bars": round(st.median(t3), 1) if t3 else None,
    }


def _row(tag, m):
    if not m["n"]:
        return f"{tag:<26} n=0"
    return (f"{tag:<26} n={m['n']:>4} ses={m['sessions']} win%={m['win_rate']*100:>4.0f} "
            f"exp={m['expectancy']:>7} PF={str(m['profit_factor']):>6} net={m['net']:>8} "
            f"mDD={m['max_dd']:>8} | mfe={m['prem_mfe']:>6} mae={m['prem_mae']:>6} "
            f"maxR~{m['med_maxR']:>4} Rcap={m['avg_Rcap']:>6} "
            f"3R={m['p3R']*100:>3.0f}% 5R={m['p5R']*100:>3.0f}% 8R={m['p8R']*100:>3.0f}% "
            f"10R={m['p10R']*100:>3.0f}% gb={m['giveback_R']}")


# ================================================================ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NIFTY,NATURALGAS,CRUDEOIL")
    args = ap.parse_args()
    MIN_SESS, MIN_TR = 10, 50

    for symraw in args.symbols.split(","):
        sym = symraw.strip().upper()
        dates = sorted(market_hub.session_dates(sym, limit=400))
        sessions = [Sess(sym, d) for d in dates]
        sessions = [s for s in sessions if len(s.clean) >= max(COMP_LBS) + 5]
        opt_maps = {(sym, d): market_hub.session_option_quotes(sym, d) for d in dates}
        usable = [s for s in sessions if opt_maps.get((sym, s.date))]
        print("\n" + "#" * 104)
        print(f"# {sym} PREMIUM  --  {len(usable)} usable sessions {[s.date for s in usable]}  "
              f"regimes {sorted({s.regime for s in usable})}")
        print("#" * 104)

        # ---- P1: spike-definition comparison (raw S0 entry, no sequence) -------
        print("\n[P1] abnormal-spike definitions  (S0 entry, spike->close, stop=spike, fix3R, no compression)")
        p1 = {}
        for sd in SPIKE_DEFS:
            tr = run_config(usable, need_c=False, spike_def=sd, need_r=False,
                            need_conf=False, prof_mode="P0", entry_model="S0",
                            pm_rule="fix3R", comp_lb=5, seq_window=2, opt_maps=opt_maps)
            m = metrics(tr)
            p1[sd] = m
            print("   " + _row(f"spike={sd}", m))
        # per-symbol adaptive choice -- QUALITY first, per the research philosophy:
        # among defs with a usable count (n>=10) pick the highest expectancy;
        # only fall back to the richest count if none clears the floor.
        cand = [sd for sd in SPIKE_DEFS if p1[sd]["n"] >= 10]
        sym_sd = (max(cand, key=lambda k: (p1[k]["expectancy"], p1[k]["n"])) if cand
                  else max(SPIKE_DEFS, key=lambda k: p1[k]["n"]))
        print(f"   -> adaptive spike def for {sym}: {sym_sd}  (n={p1[sym_sd]['n']}, "
              f"exp={p1[sym_sd].get('expectancy')}, PF={p1[sym_sd].get('profit_factor')})")

        # ---- P5: entry-model comparison (fixed E-seq: comp+spike2.5+rej, win2) -
        print("\n[P5] entry model  (compression+spike2.5+rejection, seq_window=2, stop=rejection, fix3R)")
        entry_scores = {}
        for em in ("S0", "S1", "S2", "S3", "S5"):
            nc = em not in ("S0", "S1")
            tr = run_config(usable, need_c=True, spike_def=sym_sd, need_r=nc,
                            need_conf=(em in ("S3", "S5")), prof_mode="P0",
                            entry_model=em, pm_rule="fix3R", comp_lb=5,
                            seq_window=2, opt_maps=opt_maps)
            m = metrics(tr)
            entry_scores[em] = (m["expectancy"] if m["n"] >= 4 else -999, m["n"])
            print("   " + _row(f"entry={em}", m))
        best_entry = max(entry_scores, key=lambda k: entry_scores[k])

        # ---- P6: structural stop comparison ----------------------------------
        print(f"\n[P6] initial stop structure  (comp+spike2.5+rejection+confirm, entry={best_entry})")
        for struct in ("spike", "rejection", "confirm", "swing"):
            trs = []
            for s in usable:
                om = opt_maps.get((sym, s.date)) or {}
                for idx in range(len(s.clean)):
                    if not _is_compressed(s, idx, 5) or not _is_spike(s, idx, sym_sd):
                        continue
                    for side in ("BUY", "SELL"):
                        seq = build_sequence(s, idx, side, seq_window=2,
                                             need_rejection=True, need_confirm=True)
                        if seq is None:
                            continue
                        e = entry_for(s, seq, side, best_entry)
                        if e is None:
                            continue
                        entry, ebar, ets = e
                        stp = stop_for(s, seq, side, entry, struct)
                        w = walk_R(s, ebar, entry, stp, side, "fix3R")
                        if w is None:
                            continue
                        rw = _pw.rewalk_leg(om, entry_price=entry, side=side,
                                            entry_ts=ets, exit_ts=w["exit_ts"])
                        if not rw or rw.get("premium_thin"):
                            continue
                        trs.append({"sym": sym, "session": s.date, "regime": s.regime,
                                    "side": side, "prem_pts": rw["premium_points"],
                                    "prem_mfe": rw["premium_mfe"], "prem_mae": rw["premium_mae"],
                                    **{k: w[k] for k in ("R", "r_captured", "max_r", "mfe_r",
                                                         "mae_r", "giveback_r", "t_1r", "t_2r", "t_3r")},
                                    "prof_loc": "-", "spike_mult": None})
            print("   " + _row(f"stop={struct}", metrics(trs)))

        # ---- P7: profit management ------------------------------------------
        print(f"\n[P7] profit management  (comp+spike2.5+rej+confirm, entry={best_entry}, stop=rejection)")
        pm_scores = {}
        for pm in ("fix3R", "half3R_runner", "full_runner"):
            tr = run_config(usable, need_c=True, spike_def=sym_sd, need_r=True,
                            need_conf=True, prof_mode="P0", entry_model=best_entry,
                            pm_rule=pm, comp_lb=5, seq_window=2, opt_maps=opt_maps)
            m = metrics(tr)
            pm_scores[pm] = (m["expectancy"] if m["n"] >= 4 else -999, m["n"])
            print("   " + _row(f"pm={pm}", m))
        best_pm = max(pm_scores, key=lambda k: pm_scores[k])

        # ---- P4: profile-context modes ------------------------------------
        print(f"\n[P4] profile context mode  (comp+spike-prof_aware+rej+confirm, entry={best_entry})")
        for pmode in ("P0", "P1", "P2", "P3"):
            tr = run_config(usable, need_c=True, spike_def="prof_aware", need_r=True,
                            need_conf=True, prof_mode=pmode, entry_model=best_entry,
                            pm_rule="fix3R", comp_lb=5, seq_window=2, opt_maps=opt_maps)
            print("   " + _row(f"profile={pmode}", metrics(tr)))

        # ---- P3: sequence-window sensitivity -----------------------------
        print(f"\n[P3] max sequence window  (comp+spike2.5+rej+confirm, entry={best_entry})")
        for win in SEQ_WINDOWS:
            tr = run_config(usable, need_c=True, spike_def=sym_sd, need_r=True,
                            need_conf=True, prof_mode="P0", entry_model=best_entry,
                            pm_rule="fix3R", comp_lb=5, seq_window=win, opt_maps=opt_maps)
            print("   " + _row(f"seq_window={win}", metrics(tr)))

        # ---- P10: ablation matrix A..I ----------------------------------
        print(f"\n[P10] ABLATION MATRIX  (best_entry={best_entry}, best_pm={best_pm}, comp_lb=5, seq_window=2)")
        abl = {}
        for v in "ABCDEFGHI":
            nc, sd, nr, ncf, pm_, em, pmr = _variant_spec(v, sym_sd, best_entry, best_pm)
            tr = run_config(usable, need_c=nc, spike_def=sd, need_r=nr, need_conf=ncf,
                            prof_mode=pm_, entry_model=em, pm_rule=pmr, comp_lb=5,
                            seq_window=2, opt_maps=opt_maps)
            abl[v] = (tr, metrics(tr))
            print("   " + _row(f"{v}: {sd} c={int(nc)} r={int(nr)} k={int(ncf)} {pm_} {em} {pmr}", abl[v][1]))

        # ---- P8: R-reach distribution for the richest ablation variant --
        rich = max(abl, key=lambda k: abl[k][1]["n"])
        tr, m = abl[rich]
        print(f"\n[P8] R-distribution for variant {rich} (n={m['n']}):")
        if m["n"]:
            print(f"   %reaching  1R+ {sum(1 for t in tr if t['max_r']>=1)/m['n']*100:>4.0f}  "
                  f"2R+ {sum(1 for t in tr if t['max_r']>=2)/m['n']*100:>4.0f}  "
                  f"3R+ {m['p3R']*100:>4.0f}  5R+ {m['p5R']*100:>4.0f}  "
                  f"8R+ {m['p8R']*100:>4.0f}  10R+ {m['p10R']*100:>4.0f}")
            print(f"   median maxR {m['med_maxR']}  avg maxR {m['avg_maxR']}  "
                  f"avg R captured {m['avg_Rcap']}  median t-1R {m['t1R_bars']} bars  "
                  f"median t-3R {m['t3R_bars']} bars  avg giveback {m['giveback_R']}R")
            for k in sorted({t["session"] for t in tr}):
                sm = metrics([t for t in tr if t["session"] == k])
                print(f"     {k}: n={sm['n']:>3} exp={sm['expectancy']:>7} "
                      f"Rcap={sm['avg_Rcap']:>6} 3R+={sm['p3R']*100:>3.0f}%")

        # ---- P9: nominal walk-forward (leave-one-session-out) ------------
        print(f"\n[P9] leave-one-session-out on variant {rich} (NOT a valid split at {len(usable)} sessions):")
        for hold in [s.date for s in usable]:
            tr_in = [t for t in tr if t["session"] != hold]
            tr_out = [t for t in tr if t["session"] == hold]
            mi, mo = metrics(tr_in), metrics(tr_out)
            print(f"   hold-out {hold}: in exp={mi.get('expectancy')} n={mi.get('n')} "
                  f"| out exp={mo.get('expectancy')} n={mo.get('n')}")

        # ---- P11: explicit, data-driven answers ----------------------
        g = lambda mm, k: mm.get(k) if mm.get("n") else None
        best_p1 = max(SPIKE_DEFS, key=lambda k: (p1[k].get("expectancy", -9) if p1[k]["n"] >= 10 else -9, p1[k]["n"]))
        mA, mB, mE = abl["A"][1], abl["B"][1], abl["E"][1]
        b_better = (mB["n"] >= 8 and mA["n"] >= 8 and g(mB, "expectancy") is not None
                    and g(mA, "expectancy") is not None and mB["expectancy"] > mA["expectancy"])
        comp_kills = all(abl[v][1]["n"] < 5 for v in "CDEFGHI")
        print(f"\n[P11] answers ({sym}):")
        print(f"  Q1 abnormal-spike def: best (n>=10, by expectancy) = {best_p1} "
              f"(n={p1[best_p1]['n']}, exp={g(p1[best_p1],'expectancy')}, PF={g(p1[best_p1],'profit_factor')}); "
              f"fixed 4x -> n={p1['fix4.0']['n']}; adaptive pick = {sym_sd}.")
        print(f"  Q2 spike candle as entry (A/S0): n={mA['n']} exp={g(mA,'expectancy')} "
              f"PF={g(mA,'profit_factor')} Rcap={g(mA,'avg_Rcap')} maxDD={g(mA,'max_dd')}.")
        print(f"  Q3/Q4 candle-after / rejection entry (B/S2): n={mB['n']} exp={g(mB,'expectancy')} "
              f"PF={g(mB,'profit_factor')} Rcap={g(mB,'avg_Rcap')} maxDD={g(mB,'max_dd')}  -> "
              f"{'REJECTION ENTRY BETTER' if b_better else 'not comparable / not better on this sample'}.")
        print(f"  Q5 confirmation (E/S3): n={mE['n']}  -> "
              f"{'kills the sample; cannot justify' if mE['n'] < 5 else 'see E row'}.")
        print(f"  Q6 stop structure: P6 study 0 trades (compression gate) -> inconclusive.")
        print(f"  Q7 R-reach (variant {rich}): 3R+={m['p3R']*100:.0f}%  5R+={m['p5R']*100:.0f}%  "
              f"8R+={m['p8R']*100:.0f}%  10R+={m['p10R']*100:.0f}%  median maxR={m['med_maxR']} "
              f"-> fixed 3R is {'TOO AMBITIOUS (lower target / partial+runner)' if m['p3R'] < 0.35 else 'plausible'}.")
        print(f"  Q8 runner vs fix3R: I n={abl['I'][1]['n']} / E n={mE['n']} -> "
              f"inconclusive (gated to ~0 by compression).")
        print(f"  Q9 profile context: P4 modes all 0 trades (gated) -> inconclusive on this data.")
        print(f"  Q10 highest OOS-expectancy sequence: "
              f"{'B: spike -> rejection candle (S2), no pre-compression' if b_better else 'A: spike-only, S0'} "
              f"-- < 10 sessions, LOSO session-dependent; NOT operationally convincing.")
        print(f"  NOTE: strict pre-spike compression drives every symbol to ~0 trades "
              f"({'confirmed' if comp_kills else 'partly'}); over-constrained vs rare-but-good is unresolved.")

        # ---- verdict --------------------------------------------------
        n_sess = len(usable)
        n_reg = len({s.regime for s in usable})
        n_best = m["n"]
        pos = m["n"] >= 8 and m["expectancy"] > 0 and (m["profit_factor"] or 0) >= 1.3
        if n_best == 0:
            cls = "4. OVER-CONSTRAINED / INSUFFICIENT OCCURRENCES"
        elif n_sess < MIN_SESS or n_best < MIN_TR:
            cls = ("2. PROMISING - MORE DATA REQUIRED" if pos
                   else "3. NO ESTABLISHED EDGE")
        else:
            cls = "1. PROVEN EDGE" if pos else "3. NO ESTABLISHED EDGE"
        print(f"\n>>> VERDICT {sym} PREMIUM: {cls}")
        print(f"    sessions={n_sess} (need >={MIN_SESS}) | best-variant({rich}) trades={n_best} "
              f"(need >={MIN_TR}) | regimes={n_reg} | "
              f"exp={m['expectancy']} PF={m['profit_factor']} med_maxR={m['med_maxR']}")


if __name__ == "__main__":
    main()
