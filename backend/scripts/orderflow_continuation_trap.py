#!/usr/bin/env python3
"""
orderflow_continuation_trap.py -- RESEARCH ONLY. Continuation vs Trap/Reversal.

Builds on the Event-Anatomy layer (does NOT replace its dataset). Question:
can abnormal spikes be separated into
  A) CONTINUATION   vs   B) TRAP / REVERSAL
and which observable conditions distinguish them?

Hard rules (unchanged): research only; no production pattern; no live change;
no orders; no final Buy/Sell rule; no weighted score; no institutional-
identity claims. Strictly causal -- completed candles only, every look-back
from bars[:idx], profile/OI/volume never read from the future.

Reuses helpers from orderflow_event_anatomy.py / _sequence_research.py /
_spike_ledger.py. Outputs:
  data/orderflow_continuation_trap_report_<date>.txt   -- the §13 matrix,
      §3 entry-timing table, §4 acceptance-hold sweep, §9 spike-size buckets,
      §12 runner study, H1..H8 tests, the 15-question report + per-symbol status.
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import market_hub
from scripts.orderflow_event_anatomy import (
    Sess, _prof_loc, _rejection, _atr_series, _classify_spike, _levels,
    _level_interaction, _walk, _oi_ctx, _oi_class, _load_opt_ctx,
)

RL = (1, 2, 3, 4, 5, 6, 8)
HOLDS = (1, 2, 3, 4, 5)
OUT_WINDOWS = (1, 2, 3, 5)
DIR2SIDE = {"LONG": "BUY", "SHORT": "SELL"}


# --------------------------------------------------------------- level price
def _broken_level(s: Sess, idx: int, direction: str, lv: dict):
    b = s.clean[idx]
    if direction == "LONG":
        refs = [lv[k] for k in ("prev_hi", "swing_hi", "vah", "sess_hi")
                if lv.get(k) is not None and lv[k] <= b["h"] + 1e-9]
        return (max(refs), "LONG") if refs else (None, "LONG")
    refs = [lv[k] for k in ("prev_lo", "swing_lo", "val", "sess_lo")
            if lv.get(k) is not None and lv[k] >= b["l"] - 1e-9]
    return (min(refs), "SHORT") if refs else (None, "SHORT")


def _reclaimed_by(s: Sess, idx: int, direction: str, L, w: int):
    """First offset (1..w) at which a completed candle CLOSES back through L."""
    if L is None:
        return None
    c = s.clean
    for k in range(1, w + 1):
        j = idx + k
        if j >= len(c):
            return None
        if (c[j]["c"] < L) if direction == "LONG" else (c[j]["c"] > L):
            return k
    return None


def _held_beyond(s: Sess, idx: int, direction: str, L, w: int) -> bool:
    if L is None:
        return False
    c = s.clean
    for k in range(1, w + 1):
        j = idx + k
        if j >= len(c):
            return False
        if (c[j]["c"] <= L) if direction == "LONG" else (c[j]["c"] >= L):
            return False
    return True


def _extended(s: Sess, idx: int, direction: str, w: int) -> bool:
    c = s.clean
    b = c[idx]
    for k in range(1, w + 1):
        j = idx + k
        if j >= len(c):
            break
        if (c[j]["h"] > b["h"]) if direction == "LONG" else (c[j]["l"] < b["l"]):
            return True
    return False


def _opp_displaced(s: Sess, idx: int, direction: str, w: int, R: float) -> bool:
    """Opposite move >= 0.6R developed within w candles (trap tell)."""
    if not R:
        return False
    c = s.clean
    b = c[idx]
    for k in range(1, w + 1):
        j = idx + k
        if j >= len(c):
            break
        opp = (b["c"] - c[j]["l"]) if direction == "LONG" else (c[j]["h"] - b["c"])
        if opp >= 0.6 * R:
            return True
    return False


def _outcome(s: Sess, idx: int, direction: str, L, R: float, w: int) -> str:
    """CONTINUATION / TRAP / NEUTRAL at window w (completed candles only)."""
    b = s.clean[idx]
    closed_beyond = (b["c"] > L) if (L is not None and direction == "LONG") else \
                    (b["c"] < L) if (L is not None and direction == "SHORT") else None
    reclaim = _reclaimed_by(s, idx, direction, L, w)
    if L is not None:
        if closed_beyond and _held_beyond(s, idx, direction, L, w) and _extended(s, idx, direction, w):
            return "CONTINUATION"
        if reclaim is not None and _opp_displaced(s, idx, direction, w, R):
            return "TRAP"
        return "NEUTRAL"
    # open space: pure price action
    if _extended(s, idx, direction, w) and not _opp_displaced(s, idx, direction, w, R):
        return "CONTINUATION"
    if _opp_displaced(s, idx, direction, w, R):
        return "TRAP"
    return "NEUTRAL"


# --------------------------------------------------------------- entries
def _rej_bar(s: Sess, idx: int, side: str):
    for j in range(idx, min(len(s.clean), idx + 3)):
        if _rejection(s.clean[j], side):
            return j
    return None


def _accept_bar(s: Sess, idx: int, direction: str, L, hold: int):
    """Bar offset at which acceptance is confirmed: a close beyond L followed
    by `hold` completed closes that stay beyond L. Returns the bar INDEX of
    that last hold candle (entry would be its close), or None."""
    if L is None:
        return None
    c = s.clean
    for start in range(idx, min(len(c), idx + 4)):
        beyond = (c[start]["c"] > L) if direction == "LONG" else (c[start]["c"] < L)
        if not beyond:
            continue
        ok = True
        for k in range(1, hold + 1):
            j = start + k
            if j >= len(c):
                ok = False
                break
            if (c[j]["c"] <= L) if direction == "LONG" else (c[j]["c"] >= L):
                ok = False
                break
        if ok:
            return start + hold
    return None


def _struct_sl(s: Sess, idx: int, ebar: int, direction: str, lv: dict, kind: str):
    """Structural SL for an entry whose bar-before is `ebar-1`."""
    c = s.clean
    lo_win = min(x["l"] for x in c[idx:ebar]) if ebar > idx else c[idx]["l"]
    hi_win = max(x["h"] for x in c[idx:ebar]) if ebar > idx else c[idx]["h"]
    if kind == "spike":
        v = c[idx]["l"] if direction == "LONG" else c[idx]["h"]
    elif kind == "window":                      # low/high of spike..entry-1
        v = lo_win if direction == "LONG" else hi_win
    elif kind == "swing":                       # nearest fractal at/<=entry-1
        k = None
        for j in range(ebar - 1, 0, -1):
            if direction == "LONG" and s.frac_lo[j]:
                k = j
                break
            if direction == "SHORT" and s.frac_hi[j]:
                k = j
                break
        v = (c[k]["l"] if direction == "LONG" else c[k]["h"]) if k is not None else \
            (lo_win if direction == "LONG" else hi_win)
    else:                                       # prev structural (before spike)
        v = lv["swing_lo"] if direction == "LONG" else lv["swing_hi"]
        if v is None:
            v = c[idx]["l"] if direction == "LONG" else c[idx]["h"]
    pad = 0.03 * (c[idx]["h"] - c[idx]["l"])
    return (v - pad) if direction == "LONG" else (v + pad)


def _avail_R(lv: dict, entry: float, R: float, direction: str):
    if not R:
        return None
    if direction == "LONG":
        opp = [lv[k] for k in ("vah", "swing_hi", "sess_hi") if lv.get(k) and lv[k] > entry]
    else:
        opp = [lv[k] for k in ("val", "swing_lo", "sess_lo") if lv.get(k) and lv[k] < entry]
    return round(abs(min(opp, key=lambda v: abs(v - entry)) - entry) / R, 2) if opp else None


# --------------------------------------------------------------- runner (§12)
def _runner(s: Sess, ebar: int, entry: float, sl: float, direction: str, mode: str):
    """Only meaningful when 3R is reached. Returns final_R / max_R / giveback."""
    R = abs(entry - sl)
    if R <= 0:
        return None
    c = s.clean
    t3 = entry + 3 * R if direction == "LONG" else entry - 3 * R
    max_fav = 0.0
    hit3 = False
    booked = 0.0
    frac = 1.0
    cur = sl
    for i in range(ebar, len(c)):
        b = c[i]
        fav = (b["h"] - entry) if direction == "LONG" else (entry - b["l"])
        max_fav = max(max_fav, fav)
        if not hit3 and ((b["h"] >= t3) if direction == "LONG" else (b["l"] <= t3)):
            hit3 = True
            if mode == "fix3R":
                return {"final_R": 3.0, "max_R": round(max_fav / R, 2),
                        "giveback_R": round(max_fav / R - 3.0, 2)}
            if mode == "half":
                booked, frac, cur = 1.5, 0.5, entry           # remainder to breakeven
        if hit3 and mode in ("half", "full"):
            for j in range(i, ebar, -1):
                if direction == "LONG" and s.frac_lo[j] and c[j]["l"] > cur:
                    cur = c[j]["l"]
                    break
                if direction == "SHORT" and s.frac_hi[j] and c[j]["h"] < cur:
                    cur = c[j]["h"]
                    break
            hit_stop = (b["l"] <= cur) if direction == "LONG" else (b["h"] >= cur)
            if hit_stop:
                rem = ((cur - entry) / R) if direction == "LONG" else ((entry - cur) / R)
                fr = booked + frac * rem
                return {"final_R": round(fr, 2), "max_R": round(max_fav / R, 2),
                        "giveback_R": round(max_fav / R - fr, 2)}
    last = c[-1]["c"]
    rem = ((last - entry) / R) if direction == "LONG" else ((entry - last) / R)
    fr = booked + frac * rem
    return {"final_R": round(fr, 2), "max_R": round(max_fav / R, 2),
            "giveback_R": round(max_fav / R - fr, 2)}


# --------------------------------------------------------------- collect
def collect(sym, min_rx=1.5, min_vx=1.5):
    ev = []
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
            if rx < min_rx and not (vx and vx >= min_vx):
                continue
            cls, body_pct, uw, lw = _classify_spike(b)
            direction = ("LONG" if cls.startswith("bull") else "SHORT" if cls.startswith("bear")
                         else ("LONG" if (b.get("c") or 0) >= (b.get("o") or 0) else "SHORT"))
            side = DIR2SIDE[direction]
            lv = _levels(s, idx)
            L, _ = _broken_level(s, idx, direction, lv)
            li, _dbl = _level_interaction(s, idx, direction, lv)
            loc, dpoc, dvah, dval = _prof_loc(s, idx, b["c"])
            pcls = ("NA" if loc == "NA" else
                    "acceptance_outside_value" if (loc in ("ABOVE_VA", "BELOW_VA") and li == "C_broke_accepted") else
                    "above_VAH" if loc == "ABOVE_VA" else "below_VAL" if loc == "BELOW_VA" else
                    "near_POC" if loc == "AT_POC" else "inside_value")
            prior_r = [x["h"] - x["l"] for x in s.clean[:idx] if x["h"] > x["l"]]
            rpct = (sum(1 for x in prior_r if x <= rng) / len(prior_r)) if len(prior_r) >= 10 else None
            oic = _oi_ctx(opt_ctx, b["c"], b["bar_start"])
            rel_oi = oic["ce_oi_chg"] if direction == "LONG" else oic["pe_oi_chg"]
            oi_cls = _oi_class(direction, rel_oi)

            # reference walk: spike-close entry, spike SL
            e0 = b["c"]
            sl0 = _struct_sl(s, idx, idx + 1, direction, lv, "spike")
            w0 = _walk(s.clean, idx + 1, e0, sl0, direction)
            if w0 is None:
                continue
            R0 = w0["R_pts"]
            n1_agree = None
            if idx + 1 < len(s.clean):
                nb = s.clean[idx + 1]
                up = (nb.get("c") or 0) >= (nb.get("o") or 0)
                n1_agree = (up and direction == "LONG") or ((not up) and direction == "SHORT")

            # entry-timing models
            entries = {}
            # A spike-close
            entries["A_spike"] = (e0, idx + 1, sl0)
            # B first reaction (close of idx+1), enter idx+2
            if idx + 2 < len(s.clean):
                eb = s.clean[idx + 1]["c"]
                entries["B_reaction"] = (eb, idx + 2,
                                         _struct_sl(s, idx, idx + 2, direction, lv, "window"))
            # C rejection candle
            rj = _rej_bar(s, idx, side)
            if rj is not None and rj + 1 < len(s.clean):
                entries["C_rejection"] = (s.clean[rj]["c"], rj + 1,
                                          _struct_sl(s, idx, rj + 1, direction, lv, "window"))
            # D acceptance-confirmation (hold=2)
            ab = _accept_bar(s, idx, direction, L, 2)
            if ab is not None and ab + 1 < len(s.clean):
                entries["D_acceptconf"] = (s.clean[ab]["c"], ab + 1,
                                           _struct_sl(s, idx, ab + 1, direction, lv, "window"))

            ent_walks = {}
            for k, (ep, ebar, slp) in entries.items():
                w = _walk(s.clean, ebar, ep, slp, direction)
                if w:
                    ent_walks[k] = {**w, "entry": round(ep, 2),
                                    "avail_R": _avail_R(lv, ep, w["R_pts"], direction)}

            # structural-stop matrix on the spike-close entry
            stops = {}
            for kind in ("spike", "window", "swing", "prevstruct"):
                slp = _struct_sl(s, idx, idx + 1, direction, lv, kind)
                w = _walk(s.clean, idx + 1, e0, slp, direction)
                if w:
                    stops[kind] = {"R_pts": w["R_pts"], "MAE_R": w["MAE_R"],
                                   "MFE_R": w["MFE_R"], "max_R": w["max_R"],
                                   "avail_R": _avail_R(lv, e0, w["R_pts"], direction)}

            # acceptance labels per hold
            acc = {}
            for h in HOLDS:
                if L is None:
                    acc[h] = "NA"
                else:
                    beyond = (b["c"] > L) if direction == "LONG" else (b["c"] < L)
                    if not beyond:
                        acc[h] = "REJECT"
                    else:
                        acc[h] = "ACCEPT" if _held_beyond(s, idx, direction, L, h) else "REJECT"

            # outcome per window
            oc = {w: _outcome(s, idx, direction, L, R0, w) for w in OUT_WINDOWS}

            ev.append({
                "sym": sym, "session": d, "regime": s.regime, "ts": b["bar_start"],
                "direction": direction, "spike_class": cls, "range_x": round(rx, 2),
                "range_pctile": round(rpct, 3) if rpct is not None else None,
                "vol_x": round(vx, 2) if vx else None, "n1_agree": n1_agree,
                "level_class": li, "broke_level": L is not None,
                "profile_class": pcls, "oi_class": oi_cls,
                "ref": w0, "R0": R0, "avail_R0": _avail_R(lv, e0, R0, direction),
                "entries": ent_walks, "stops": stops, "acc": acc, "oc": oc,
                "spike_low": b["l"], "spike_high": b["h"], "sl0": sl0, "e0": e0,
                "ebar0": idx + 1, "L": L,
            })
    return ev


# --------------------------------------------------------------- aggregation
def _pk(rows, k, walk="ref"):
    v = [r for r in rows if (r[walk] if walk == "ref" else r["entries"].get(walk, {})).get(f"reached_{k}R")]
    return len(v) / len(rows) if rows else 0.0


def _dist(rows, walk="ref", win=3):
    if not rows:
        return None
    W = [(r[walk] if walk == "ref" else r["entries"][walk]) for r in rows
         if walk == "ref" or walk in r["entries"]]
    if not W:
        return None
    mfe = sorted(x["MFE_R"] for x in W)
    mae = sorted(x["MAE_R"] for x in W)
    cont = sum(1 for r in rows if r["oc"].get(win) == "CONTINUATION")
    trap = sum(1 for r in rows if r["oc"].get(win) == "TRAP")
    n = len(W)
    return {
        "n": n, "cont": round(cont / len(rows), 3), "trap": round(trap / len(rows), 3),
        "medMFE_R": mfe[len(mfe) // 2], "medMAE_R": mae[len(mae) // 2],
        "P3R": round(sum(1 for x in W if x["reached_3R"]) / n, 3),
        "P5R": round(sum(1 for x in W if x["reached_5R"]) / n, 3),
        "P8R": round(sum(1 for x in W if x["reached_8R"]) / n, 3),
    }


def _row(name, d):
    if not d:
        return f"    {name:<30} n=0"
    return (f"    {name:<30} n={d['n']:>4}  cont={d['cont']*100:>3.0f}%  trap={d['trap']*100:>3.0f}%  "
            f"medMFE_R={d['medMFE_R']:>5}  medMAE_R={d['medMAE_R']:>6}  "
            f"P3R={d['P3R']*100:>3.0f}%  P5R={d['P5R']*100:>3.0f}%  P8R={d['P8R']*100:>3.0f}%")


def _grp(rows, fn):
    g = {}
    for r in rows:
        g.setdefault(fn(r), []).append(r)
    return g


def report(sym, ev, out):
    p = lambda *a: print(*a, file=out)
    sess = sorted({r["session"] for r in ev})
    regs = sorted({r["regime"] for r in ev})
    p("\n" + "#" * 112)
    p(f"# {sym}  --  {len(ev)} abnormal events | {len(sess)} sessions {sess} | regimes {regs}")
    p("#" * 112)
    base = _dist(ev)
    p("\n[BASELINE] spike-close entry, spike SL, outcome window = 3 candles")
    p(_row("ALL", base))

    p("\n[§2 outcome classification vs window]  (level-breaking events)")
    br = [r for r in ev if r["broke_level"]]
    for w in OUT_WINDOWS:
        c = sum(1 for r in br if r["oc"][w] == "CONTINUATION")
        t = sum(1 for r in br if r["oc"][w] == "TRAP")
        nu = len(br) - c - t
        p(f"    window={w}: n={len(br)}  CONTINUATION={c} ({100*c/len(br):.0f}%)  "
          f"TRAP={t} ({100*t/len(br):.0f}%)  NEUTRAL={nu} ({100*nu/len(br):.0f}%)")

    p("\n[§3 entry timing]  A spike-close / B first-reaction / C rejection / D acceptance-confirm")
    for em in ("A_spike", "B_reaction", "C_rejection", "D_acceptconf"):
        rows = [r for r in ev if em in r["entries"]]
        p(_row(em, _dist(rows, walk=em)))
        if rows:
            Rp = [r["entries"][em]["R_pts"] for r in rows]
            av = [r["entries"][em]["avail_R"] for r in rows if r["entries"][em]["avail_R"]]
            p(f"        median R={st.median(Rp):.1f}pts  median available_R="
              f"{st.median(av) if av else None}  "
              + "  ".join(f"P{k}R={_pk(rows,k,em)*100:.0f}%" for k in RL))

    p("\n[§4 acceptance hold sweep]  (spike-close-entry forward dist conditioned on acceptance[hold])")
    for h in HOLDS:
        acc = [r for r in br if r["acc"][h] == "ACCEPT"]
        rej = [r for r in br if r["acc"][h] == "REJECT"]
        p(f"  hold={h}:")
        p(_row(f"  ACCEPT (n={len(acc)})", _dist(acc)))
        p(_row(f"  REJECT (n={len(rej)})", _dist(rej)))

    p("\n[§5 level interaction]  BREAK/SWEEP/RECLAIM/ACCEPT/FAILED")
    for k, g in sorted(_grp(ev, lambda r: r["level_class"]).items()):
        p(_row(str(k), _dist(g)))

    p("\n[§6 profile context]")
    for k, g in sorted(_grp(ev, lambda r: r["profile_class"]).items()):
        p(_row(str(k), _dist(g)))

    p("\n[§7 OI + price behaviour]")
    for k, g in sorted(_grp(ev, lambda r: r["oi_class"]).items()):
        p(_row(str(k), _dist(g)))

    p("\n[§8 volume, controlling for spike magnitude]  (within range_x 1.5-2.5 only)")
    ctrl = [r for r in ev if r["range_x"] and 1.5 <= r["range_x"] < 2.5]
    for lab, f in (("vol_x<1.5/NA", lambda r: not (r["vol_x"] and r["vol_x"] >= 1.5)),
                   ("vol_x 1.5-2", lambda r: r["vol_x"] and 1.5 <= r["vol_x"] < 2),
                   ("vol_x >=2", lambda r: r["vol_x"] and r["vol_x"] >= 2)):
        p(_row(lab, _dist([r for r in ctrl if f(r)])))

    p("\n[§9 spike-size percentile buckets]")
    for lo, hi in ((0.0, 0.90), (0.90, 0.95), (0.95, 0.98), (0.98, 0.99), (0.99, 1.01)):
        g = [r for r in ev if r["range_pctile"] is not None and lo <= r["range_pctile"] < hi]
        p(_row(f"pctile {lo:.2f}-{hi:.2f}", _dist(g)))

    p("\n[§10 structural stop matrix]  (spike-close entry)")
    for kind in ("spike", "window", "swing", "prevstruct"):
        rows = [r["stops"][kind] for r in ev if kind in r["stops"]]
        if not rows:
            continue
        Rp = sorted(x["R_pts"] for x in rows)
        mae = sorted(x["MAE_R"] for x in rows)
        mfe = sorted(x["MFE_R"] for x in rows)
        av = sorted(x["avail_R"] for x in rows if x["avail_R"])
        p(f"    stop={kind:<11} n={len(rows):>4}  medR={Rp[len(Rp)//2]:>5}pts  "
          f"medMAE_R={mae[len(mae)//2]:>6}  medMFE_R={mfe[len(mfe)//2]:>5}  "
          f"med_available_R={av[len(av)//2] if av else None}")

    p("\n[§11 available reward]  headline (spike SL)")
    av = sorted(r["avail_R0"] for r in ev if r["avail_R0"])
    if av:
        p(f"    available_R: p25={av[len(av)//4]}  median={av[len(av)//2]}  p75={av[3*len(av)//4]}")
        for lab, f in (("avail_R < 2", lambda r: (r["avail_R0"] or 0) < 2),
                       ("avail_R 2-5", lambda r: 2 <= (r["avail_R0"] or 0) < 5),
                       ("avail_R >= 5", lambda r: (r["avail_R0"] or 0) >= 5)):
            p(_row(lab, _dist([r for r in ev if r["avail_R0"] is not None and f(r)])))

    p("\n[§12 runner study]  events where spike-close entry reached 3R")
    r3 = [r for r in ev if r["ref"]["reached_3R"]]
    p(f"    n(3R-reaching) = {len(r3)}")
    for mode, lab in (("fix3R", "A fixed 3R"), ("half", "B 50%@3R + runner"), ("full", "C full runner")):
        fin = []
        for r in r3:
            rr = _runner_from_event(r, mode)
            if rr:
                fin.append(rr)
        if fin:
            fr = [x["final_R"] for x in fin]
            gb = [x["giveback_R"] for x in fin]
            mx = [x["max_R"] for x in fin]
            p(f"    {lab:<20} n={len(fin):>3}  E[final_R]={st.fmean(fr):.2f}  "
              f"med final_R={st.median(fr):.2f}  med max_R={st.median(mx):.2f}  "
              f"med giveback_R={st.median(gb):.2f}")

    p("\n[§13 CONTINUATION vs TRAP matrix]  (spike-close entry, window=3)")
    p(f"    {'bucket':<30} {'n':>4} {'cont%':>6} {'trap%':>6} {'mMFE_R':>7} {'mMAE_R':>7} {'P3R':>5} {'P5R':>5} {'P8R':>5}")
    def mrow(name, g):
        d = _dist(g)
        if not d:
            p(f"    {name:<30} {'n=0':>4}")
            return
        p(f"    {name:<30} {d['n']:>4} {d['cont']*100:>5.0f}% {d['trap']*100:>5.0f}% "
          f"{d['medMFE_R']:>7} {d['medMAE_R']:>7} {d['P3R']*100:>4.0f}% {d['P5R']*100:>4.0f}% {d['P8R']*100:>4.0f}%")
    mrow("ALL", ev)
    mrow("acceptance hold>=2", [r for r in br if r["acc"][2] == "ACCEPT"])
    mrow("failed acceptance", [r for r in br if r["acc"][2] == "REJECT"])
    mrow("level C_broke_accepted", [r for r in ev if r["level_class"] == "C_broke_accepted"])
    mrow("level B_swept_returned", [r for r in ev if r["level_class"] == "B_swept_returned"])
    mrow("pctile >=0.98", [r for r in ev if (r["range_pctile"] or 0) >= 0.98])
    mrow("pctile 0.90-0.95", [r for r in ev if r["range_pctile"] is not None and 0.90 <= r["range_pctile"] < 0.95])
    mrow("n1 agrees w/ spike", [r for r in ev if r["n1_agree"]])
    mrow("n1 disagrees", [r for r in ev if r["n1_agree"] is False])
    mrow("vol_x >= 2", [r for r in ev if r["vol_x"] and r["vol_x"] >= 2])
    mrow("profile acc-outside-value", [r for r in ev if r["profile_class"] == "acceptance_outside_value"])
    mrow("OI price_up_OI_up", [r for r in ev if r["oi_class"] == "price_up_OI_up"])
    mrow("accept>=2 & C_broke_accepted", [r for r in br if r["acc"][2] == "ACCEPT" and r["level_class"] == "C_broke_accepted"])
    mrow("accept>=2 & avail_R>=3", [r for r in br if r["acc"][2] == "ACCEPT" and (r["avail_R0"] or 0) >= 3])

    _hypotheses(sym, ev, br, p)
    _final(sym, ev, sess, regs, p)


def _runner_from_event(r, mode):
    # rebuild a Sess-free runner from stored walk? need bars -> recompute via ref walk data
    # we re-derive using the reference entry/stop and the session bars.
    return _RUNNER_CACHE.get((r["sym"], r["session"], r["ts"], mode))


_RUNNER_CACHE = {}


def _prime_runner_cache(sym, ev):
    for d in sorted({r["session"] for r in ev}):
        s = Sess(sym, d)
        for r in [x for x in ev if x["session"] == d and x["ref"]["reached_3R"]]:
            for mode in ("fix3R", "half", "full"):
                _RUNNER_CACHE[(sym, d, r["ts"], mode)] = _runner(
                    s, r["ebar0"], r["e0"], r["sl0"], r["direction"], mode)


def _hypotheses(sym, ev, br, p):
    p("\n[H1..H8 hypothesis tests]  (effect vs the ALL baseline; n in parens)")
    base = _dist(ev)
    acc = _dist([r for r in br if r["acc"][2] == "ACCEPT"])
    fail = _dist([r for r in br if r["acc"][2] == "REJECT"])
    big = _dist([r for r in ev if (r["range_pctile"] or 0) >= 0.98])
    mod = _dist([r for r in ev if r["range_pctile"] is not None and 0.90 <= r["range_pctile"] < 0.95])
    lvl = _dist([r for r in ev if r["level_class"] in ("C_broke_accepted", "A_broke")])
    vol = _dist([r for r in ev if r["vol_x"] and r["vol_x"] >= 2])
    dd = lambda a, b, key: None if not (a and b) else round(a[key] - b[key], 3)
    p(f"  H1 spike+acceptance > spike alone (cont%):        "
      f"acc={acc['cont'] if acc else None} vs all={base['cont']}  Δ={dd(acc, base, 'cont')}  "
      f"[{'SUPPORTED' if acc and acc['cont'] - base['cont'] > 0.10 else 'not supported'}]  (n={acc['n'] if acc else 0})")
    p(f"  H2 failed acceptance -> higher reversal (trap%):   "
      f"fail={fail['trap'] if fail else None} vs all={base['trap']}  Δ={dd(fail, base, 'trap')}  "
      f"[{'SUPPORTED' if fail and fail['trap'] - base['trap'] > 0.10 else 'not supported'}]  (n={fail['n'] if fail else 0})")
    dA = _dist([r for r in ev if "D_acceptconf" in r["entries"]], walk="D_acceptconf")
    aA = _dist([r for r in ev if "A_spike" in r["entries"]], walk="A_spike")
    p(f"  H3 post-spike reaction info > magnitude:           "
      f"acc-conf entry P3R={dA['P3R'] if dA else None} vs big-spike P3R={big['P3R'] if big else None}  "
      f"[{'SUPPORTED' if dA and big and dA['P3R'] > big['P3R'] else 'inconclusive'}]")
    p(f"  H4 level interaction > raw volume (P3R spread):     "
      f"level(C/A) P3R={lvl['P3R'] if lvl else None} vs vol>=2 P3R={vol['P3R'] if vol else None} vs all={base['P3R']}  "
      f"[{'level > volume' if lvl and vol and (lvl['P3R']-base['P3R']) > (vol['P3R']-base['P3R']) else 'inconclusive'}]")
    pc = {k: _dist(g)['cont'] for k, g in _grp(ev, lambda r: r['profile_class']).items() if _dist(g)}
    spread = round(max(pc.values()) - min(pc.values()), 3) if pc else None
    p(f"  H5 profile location changes distribution:          cont% spread across classes = {spread}  "
      f"[{'material' if spread and spread > 0.20 else 'weak'}]")
    p(f"  H6 extremely large spikes = exhaustion:            "
      f"pctile>=.98 cont={big['cont'] if big else None} trap={big['trap'] if big else None}  vs  "
      f"pctile .90-.95 cont={mod['cont'] if mod else None} trap={mod['trap'] if mod else None}  "
      f"[{'SUPPORTED' if big and mod and big['cont'] < mod['cont'] and big['trap'] >= mod['trap'] else 'not supported'}]")
    hi = _dist([r for r in ev if (r["avail_R0"] or 0) >= 5])
    lo = _dist([r for r in ev if r["avail_R0"] is not None and (r["avail_R0"] or 0) < 2])
    p(f"  H7 available space determines large-R feasibility:  "
      f"avail_R>=5 P5R={hi['P5R'] if hi else None} vs avail_R<2 P5R={lo['P5R'] if lo else None}  "
      f"[{'SUPPORTED' if hi and lo and hi['P5R'] > lo['P5R'] else 'inconclusive'}]")
    ents = {em: _dist([r for r in ev if em in r["entries"]], walk=em)
            for em in ("A_spike", "B_reaction", "C_rejection", "D_acceptconf")}
    best = max((e for e in ents.items() if e[1]), key=lambda kv: kv[1]["P3R"], default=(None, None))
    p(f"  H8 best entry is not the earliest:                 best-P3R entry = {best[0]} "
      f"(P3R={best[1]['P3R'] if best[1] else None}) vs A_spike P3R={ents['A_spike']['P3R'] if ents['A_spike'] else None}  "
      f"[{'SUPPORTED' if best[0] not in (None, 'A_spike') else 'not supported'}]")


def _final(sym, ev, sess, regs, p):
    p("\n[§16 FINAL REPORT]")
    base = _dist(ev)
    br = [r for r in ev if r["broke_level"]]
    acc = _dist([r for r in br if r["acc"][2] == "ACCEPT"])
    fail = _dist([r for r in br if r["acc"][2] == "REJECT"])
    q = [
        ("1 What separates continuation from trap",
         f"acceptance (close beyond level held >=2 candles) vs failed acceptance: "
         f"cont {int((acc['cont'] if acc else 0)*100)}% vs {int((fail['cont'] if fail else 0)*100)}%, "
         f"trap {int((acc['trap'] if acc else 0)*100)}% vs {int((fail['trap'] if fail else 0)*100)}%"),
        ("2 Is acceptance genuinely useful",
         f"yes on this sample (P3R {int((acc['P3R'] if acc else 0)*100)}% vs baseline {int(base['P3R']*100)}%) -- HYPOTHESIS"),
        ("3 After failed acceptance",
         f"trap rate {int((fail['trap'] if fail else 0)*100)}%, medMFE_R {fail['medMFE_R'] if fail else None} -- reversal-leaning"),
        ("4/5 spike-close vs reaction entry", "see §3 table -- compare P3R per model"),
        ("6 most robust structural stop", "see §10 -- lowest medMAE_R with usable available_R"),
        ("7 does profile change outcomes", "see §6/H5 -- spread across classes"),
        ("8 does OI add info", "see §7 -- compare bucket P3R vs baseline"),
        ("9 does volume add info", "see §8 -- controlled for magnitude"),
        ("10 spike magnitude non-linear", "see §9/H6 -- pctile buckets"),
        ("11 realistic available_R", "see §11 -- median available_R and its effect on P5R"),
        ("12-14 P(3R)/P(5R)/P(8R)",
         f"{int(base['P3R']*100)}% / {int(base['P5R']*100)}% / {int(base['P8R']*100)}% (all events); "
         f"conditioned on acceptance: {int((acc['P3R'] if acc else 0)*100)}% / "
         f"{int((acc['P5R'] if acc else 0)*100)}% / {int((acc['P8R'] if acc else 0)*100)}%"),
        ("15 combos for Stage-2", "acceptance(hold>=2); acceptance & C_broke_accepted; "
         "acceptance & available_R>=3 -- see §13"),
    ]
    for k, v in q:
        p(f"    Q{k}: {v}")
    p(f"\n>>> {sym} PREMIUM STATUS: INSUFFICIENT DATA")
    p(f"    {len(ev)} events / {len(sess)} sessions / {len(regs)} regimes. "
      f"Need >=10 independent sessions, multiple regimes, and a train/val/OOS split. "
      f"The acceptance-vs-failed-acceptance separation is the one hypothesis carried to Stage-2.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NIFTY,NATURALGAS,CRUDEOIL")
    ap.add_argument("--out", default=f"data/orderflow_continuation_trap_report_2026-09-06.txt")
    a = ap.parse_args()
    syms = [x.strip().upper() for x in a.symbols.split(",") if x.strip()]
    with open(a.out, "w") as f:
        print(f"orderflow continuation-vs-trap research  --  RESEARCH ONLY, no production change", file=f)
        for sym in syms:
            ev = collect(sym)
            if not ev:
                print(f"\n{sym}: no events", file=f)
                continue
            _prime_runner_cache(sym, ev)
            report(sym, ev, f)
    print(f"wrote report -> {a.out}")
    print(open(a.out).read())


if __name__ == "__main__":
    main()
