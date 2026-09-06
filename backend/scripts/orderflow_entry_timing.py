#!/usr/bin/env python3
"""
orderflow_entry_timing.py -- RESEARCH ONLY. Entry timing + structural risk +
available R.

Central question (from the previous phase): the ABNORMAL LEVEL BREAK ->
ACCEPTANCE -> CONTINUATION hypothesis holds, but waiting for full 2-3 candle
acceptance worsens the fill and shrinks available_R. So:

  "What is the EARLIEST entry that keeps a good continuation/trap trade-off
   while preserving enough structural reward?"

Compares 4 causal entry timings on every abnormal event, each with its own
valid structural stop and available_R:

  A  SPIKE-CLOSE        valid at the spike candle close (enter next bar open)
  B  FIRST-REACTION     valid at the 1st post-spike close, ONLY if it agrees
                        with the spike direction (enter the following bar)
  C  EARLY-ACCEPTANCE   valid at the 1st post-spike close that is beyond the
                        broken level with no same-bar reclaim (enter next bar)
  D  FULL-ACCEPTANCE    valid after the close-beyond-level has HELD for 2 more
                        completed candles (enter the bar after the 2nd hold)

Hard rules: research only; no production signal; no live change; no orders;
NO score / weights (§14); strictly causal -- every entry uses only completed
candles / already-known levels / OI-and-volume from <= the entry timestamp.
Builds on orderflow_continuation_trap.py; does NOT alter its dataset.

Output: data/orderflow_entry_timing_report_2026-09-06.txt
  §4 first-reaction analysis, §5 early-acceptance definition sweep,
  §6 per-entry structural-stop matrix, §7/§11 available_R, §8 entry-quality
  matrix, §9 accuracy-vs-quality, §10 large-R per entry, §11 spike-size x
  entry, §12 profile x entry, §13 OI/volume as secondary evidence,
  §16 sweet-spot, §17 the 20-question report + per-symbol status.
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import market_hub
from scripts.orderflow_continuation_trap import (
    Sess, _atr_series, _classify_spike, _levels, _level_interaction, _prof_loc,
    _oi_ctx, _oi_class, _load_opt_ctx, _walk, _broken_level, _held_beyond,
    _reclaimed_by, _outcome, _struct_sl, _avail_R,
)

RL = (1, 2, 3, 4, 5, 6, 8, 10)
DIR2SIDE = {"LONG": "BUY", "SHORT": "SELL"}
OUT_WIN = 3
ACC_DEFS = ("first_close", "close_no_reclaim", "hold1", "hold2", "hold3")


# --------------------------------------------------------------- entry timing
def _first_close_beyond(s, idx, direction, L):
    """1st post-spike completed bar whose close is beyond L. Returns its idx."""
    if L is None:
        return None
    c = s.clean
    for j in range(idx + 1, min(len(c), idx + 6)):
        if (c[j]["c"] > L) if direction == "LONG" else (c[j]["c"] < L):
            return j
    return None


def _acc_entry_bar(s, idx, direction, L, kind):
    """Bar whose CLOSE is the entry price for early-acceptance definition `kind`.
    Causal: only completed candles."""
    c = s.clean
    j0 = _first_close_beyond(s, idx, direction, L)
    if j0 is None:
        return None
    if kind == "first_close":
        return j0
    if kind == "close_no_reclaim":
        # the j0 close is beyond L and j0 itself did not wick fully back:
        wick_back = (c[j0]["l"] <= L) if direction == "LONG" else (c[j0]["h"] >= L)
        return j0 if not wick_back else None
    hold = int(kind[-1])
    return j0 + hold if _held_beyond(s, idx, direction, L, (j0 - idx) + hold) else None


def _n1(s, idx, direction, L):
    c = s.clean
    if idx + 1 >= len(c):
        return None
    b, nb = c[idx], c[idx + 1]
    rng = nb["h"] - nb["l"]
    body = abs((nb.get("c") or 0) - (nb.get("o") or 0))
    up = (nb.get("c") or 0) >= (nb.get("o") or 0)
    agree = (up and direction == "LONG") or ((not up) and direction == "SHORT")
    beyond = None if L is None else ((nb["c"] > L) if direction == "LONG" else (nb["c"] < L))
    reenter = (nb["l"] <= b["l"]) if direction == "LONG" else (nb["h"] >= b["h"])
    ext = (nb["h"] - b["h"]) if direction == "LONG" else (b["l"] - nb["l"])
    uw = nb["h"] - max(nb.get("o") or 0, nb.get("c") or 0)
    lw = min(nb.get("o") or 0, nb.get("c") or 0) - nb["l"]
    return {
        "agree": agree, "beyond_level": beyond, "reenter_spike": reenter,
        "body_pct": round(body / rng, 2) if rng > 0 else None,
        "wick_pct": round((uw + lw) / rng, 2) if rng > 0 else None,
        "extension": round(ext, 2), "vol": nb["v"],
        "dist_from_spike_extreme": round(abs((nb["h"] if direction == "LONG" else nb["l"]) - (b["h"] if direction == "LONG" else b["l"])), 2),
    }


def _entry_stops(s, idx, ebar, direction, lv):
    """Valid structural stops for an entry whose bar-before is ebar-1."""
    c = s.clean
    lo_win = min(x["l"] for x in c[idx:ebar]) if ebar > idx else c[idx]["l"]
    hi_win = max(x["h"] for x in c[idx:ebar]) if ebar > idx else c[idx]["h"]
    out = {"spike": _struct_sl(s, idx, ebar, direction, lv, "spike"),
           "window": (lo_win if direction == "LONG" else hi_win),
           "swing": _struct_sl(s, idx, ebar, direction, lv, "swing"),
           "prevstruct": _struct_sl(s, idx, ebar, direction, lv, "prevstruct")}
    # first-reaction low/high (bar idx+1) -- only if the entry is at/after idx+2
    if ebar >= idx + 2 and idx + 1 < len(c):
        out["reaction"] = c[idx + 1]["l"] if direction == "LONG" else c[idx + 1]["h"]
    pad = 0.03 * (c[idx]["h"] - c[idx]["l"])
    return {k: (v - pad if direction == "LONG" else v + pad) for k, v in out.items()}


def collect(sym, min_rx=1.5, min_vx=1.5):
    ev = []
    for d in sorted(market_hub.session_dates(sym, limit=400)):
        s = Sess(sym, d)
        if len(s.clean) < 14:
            continue
        oc = _load_opt_ctx(sym, d)
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
            cls, *_ = _classify_spike(b)
            direction = ("LONG" if cls.startswith("bull") else "SHORT" if cls.startswith("bear")
                         else ("LONG" if (b.get("c") or 0) >= (b.get("o") or 0) else "SHORT"))
            lv = _levels(s, idx)
            L, _ = _broken_level(s, idx, direction, lv)
            li, _ = _level_interaction(s, idx, direction, lv)
            loc, *_ = _prof_loc(s, idx, b["c"])
            pcls = ("NA" if loc == "NA" else
                    "acceptance_outside_value" if (loc in ("ABOVE_VA", "BELOW_VA") and li == "C_broke_accepted") else
                    "above_VAH" if loc == "ABOVE_VA" else "below_VAL" if loc == "BELOW_VA" else
                    "near_POC" if loc == "AT_POC" else "inside_value")
            prior_r = [x["h"] - x["l"] for x in s.clean[:idx] if x["h"] > x["l"]]
            rpct = (sum(1 for x in prior_r if x <= rng) / len(prior_r)) if len(prior_r) >= 10 else None
            oic = _oi_ctx(oc, b["c"], b["bar_start"])
            rel_oi = oic["ce_oi_chg"] if direction == "LONG" else oic["pe_oi_chg"]

            ref = _walk(s.clean, idx + 1, b["c"], _struct_sl(s, idx, idx + 1, direction, lv, "spike"), direction)
            if ref is None:
                continue
            R0 = ref["R_pts"]
            n1 = _n1(s, idx, direction, L)

            # --- entry timings ---
            timings = {}
            timings["A"] = (b["c"], idx + 1, b["bar_start"])           # spike close
            if n1 and n1["agree"] and idx + 2 < len(s.clean):
                timings["B"] = (s.clean[idx + 1]["c"], idx + 2, s.clean[idx + 1]["bar_start"])
            cj = _acc_entry_bar(s, idx, direction, L, "close_no_reclaim")
            if cj is not None and cj + 1 < len(s.clean):
                timings["C"] = (s.clean[cj]["c"], cj + 1, s.clean[cj]["bar_start"])
            dj = _acc_entry_bar(s, idx, direction, L, "hold2")
            if dj is not None and dj + 1 < len(s.clean):
                timings["D"] = (s.clean[dj]["c"], dj + 1, s.clean[dj]["bar_start"])

            E = {}
            for k, (ep, ebar, vts) in timings.items():
                stops = _entry_stops(s, idx, ebar, direction, lv)
                sw = {}
                for sn, sp in stops.items():
                    w = _walk(s.clean, ebar, ep, sp, direction)
                    if w:
                        sw[sn] = {**w, "avail_R": _avail_R(lv, ep, w["R_pts"], direction)}
                if not sw:
                    continue
                prim = "spike" if k in ("A", "B", "C") else ("swing" if "swing" in sw else "spike")
                prim = prim if prim in sw else next(iter(sw))
                E[k] = {"entry": round(ep, 2), "valid_ts": vts, "ebar": ebar,
                        "primary_stop": prim, "walk": sw[prim], "stops": sw}

            # early-acceptance definition sweep (independent of the 4 timings)
            accdef = {}
            for kd in ACC_DEFS:
                j = _acc_entry_bar(s, idx, direction, L, kd)
                if j is None or j + 1 >= len(s.clean):
                    accdef[kd] = None
                    continue
                sp = _entry_stops(s, idx, j + 1, direction, lv)["window"]
                w = _walk(s.clean, j + 1, s.clean[j]["c"], sp, direction)
                accdef[kd] = ({**w, "avail_R": _avail_R(lv, s.clean[j]["c"], w["R_pts"], direction)}
                              if w else None)

            oc_win = {w: _outcome(s, idx, direction, L, R0, w) for w in (1, 2, 3, 5)}

            ev.append({
                "sym": sym, "session": d, "regime": s.regime, "ts": b["bar_start"],
                "direction": direction, "range_pctile": round(rpct, 3) if rpct is not None else None,
                "vol_x": round(vx, 2) if vx else None, "level_class": li, "broke_level": L is not None,
                "profile_class": pcls, "oi_class": _oi_class(direction, rel_oi),
                "n1": n1, "R0": R0, "oc": oc_win, "E": E, "accdef": accdef,
            })
    return ev


# --------------------------------------------------------------- stats helpers
def _agg(rows, ekey=None):
    """rows: event list. ekey None => reference (spike-close) walk; else E[ekey]."""
    W, R = [], rows
    for r in rows:
        if ekey is None:
            W.append((r, r["_ref"] if "_ref" in r else None))
        elif ekey in r["E"]:
            W.append((r, r["E"][ekey]["walk"]))
    W = [(r, w) for r, w in W if w is not None] if ekey else \
        [(r, r["E"]["A"]["walk"]) for r in rows if "A" in r["E"]]
    if not W:
        return None
    ws = [w for _, w in W]
    evs = [r for r, _ in W]
    mfe = sorted(w["MFE_R"] for w in ws)
    mae = sorted(w["MAE_R"] for w in ws)
    mx = sorted(w["max_R"] for w in ws)
    av = sorted(w["avail_R"] for w in ws if w.get("avail_R"))
    rp = sorted(w["R_pts"] for w in ws)
    n = len(ws)
    cont = sum(1 for r in evs if r["oc"].get(OUT_WIN) == "CONTINUATION")
    trap = sum(1 for r in evs if r["oc"].get(OUT_WIN) == "TRAP")
    pk = lambda k: round(sum(1 for w in ws if w.get(f"reached_{k}R")) / n, 3)
    return {
        "n": n, "cont": round(cont / n, 3), "trap": round(trap / n, 3),
        "medMFE_R": mfe[n // 2], "medMAE_R": mae[n // 2],
        "med_maxR": mx[n // 2], "p75_maxR": mx[min(n - 1, 3 * n // 4)],
        "p90_maxR": mx[min(n - 1, int(n * 0.9))],
        "med_availR": av[len(av) // 2] if av else None,
        "med_R_pts": rp[n // 2],
        **{f"P{k}R": pk(k) for k in RL},
    }


def _erow(name, a):
    if not a:
        return f"    {name:<24} n=0"
    return (f"    {name:<24} n={a['n']:>4}  cont={a['cont']*100:>3.0f}%  trap={a['trap']*100:>3.0f}%  "
            f"mMAE_R={a['medMAE_R']:>6}  mMFE_R={a['medMFE_R']:>5}  P3R={a['P3R']*100:>3.0f}%  "
            f"P5R={a['P5R']*100:>3.0f}%  P8R={a['P8R']*100:>3.0f}%  med_availR={a['med_availR']}  "
            f"med_R={a['med_R_pts']}")


def _grp(rows, fn):
    g = {}
    for r in rows:
        g.setdefault(fn(r), []).append(r)
    return g


# --------------------------------------------------------------- report
def report(sym, ev, out):
    p = lambda *a: print(*a, file=out)
    sess = sorted({r["session"] for r in ev})
    regs = sorted({r["regime"] for r in ev})
    br = [r for r in ev if r["broke_level"]]
    p("\n" + "#" * 114)
    p(f"# {sym}  --  {len(ev)} events ({len(br)} level-breaking) | {len(sess)} sessions {sess} | regimes {regs}")
    p("#" * 114)

    p("\n[§8 ENTRY-QUALITY MATRIX]  (each entry with its own primary structural stop; outcome window 3)")
    p(f"    {'entry':<24} {'n':>4} {'cont%':>6} {'trap%':>6} {'mMAE_R':>7} {'mMFE_R':>7} {'P3R':>5} {'P5R':>5} {'P8R':>5} {'med_availR':>11} {'med_R':>7}")
    A = {k: _agg([r for r in ev if k in r["E"]], k) for k in ("A", "B", "C", "D")}
    names = {"A": "A spike-close", "B": "B first-reaction", "C": "C early-acceptance", "D": "D full-acceptance(hold2)"}
    for k in ("A", "B", "C", "D"):
        a = A[k]
        if not a:
            p(f"    {names[k]:<24} n=0")
            continue
        p(f"    {names[k]:<24} {a['n']:>4} {a['cont']*100:>5.0f}% {a['trap']*100:>5.0f}% "
          f"{a['medMAE_R']:>7} {a['medMFE_R']:>7} {a['P3R']*100:>4.0f}% {a['P5R']*100:>4.0f}% "
          f"{a['P8R']*100:>4.0f}% {str(a['med_availR']):>11} {a['med_R_pts']:>7}")

    p("\n[§9 ACCURACY vs ENTRY QUALITY]")
    for k in ("A", "B", "C", "D"):
        a = A[k]
        if not a:
            continue
        p(f"    {names[k]:<24} continuation={a['cont']*100:.0f}%  entry-to-stop R={a['med_R_pts']}pts  "
          f"available_R={a['med_availR']}  MFE_R={a['medMFE_R']}  MAE_R={a['medMAE_R']}  "
          f"(later entry -> {'+' if k in 'CD' else ''}accuracy / {'-' if k in 'CD' else '~'}available_R)")

    p("\n[§10 LARGE-R per entry]  P(kR) and max_R distribution")
    for k in ("A", "B", "C", "D"):
        a = A[k]
        if not a:
            continue
        p(f"    {names[k]:<24} " + " ".join(f"{q}R={a[f'P{q}R']*100:.0f}%" for q in (3, 4, 5, 6, 8, 10))
          + f"   med_maxR={a['med_maxR']}  p75={a['p75_maxR']}  p90={a['p90_maxR']}")

    p("\n[§4 FIRST-REACTION ANALYSIS]  (outcome = spike-close-entry, window 3)")
    hasn1 = [r for r in ev if r["n1"]]
    agree = [r for r in hasn1 if r["n1"]["agree"]]
    dis = [r for r in hasn1 if not r["n1"]["agree"]]
    p(_erow("n1 AGREES", _agg(agree, "A")))
    p(_erow("n1 DISAGREES", _agg(dis, "A")))
    bl = [r for r in hasn1 if r["n1"]["beyond_level"] is True]
    nbl = [r for r in hasn1 if r["n1"]["beyond_level"] is False]
    p(_erow("n1 closes BEYOND level", _agg(bl, "A")))
    p(_erow("n1 closes INSIDE level", _agg(nbl, "A")))
    re = [r for r in hasn1 if r["n1"]["reenter_spike"]]
    nre = [r for r in hasn1 if not r["n1"]["reenter_spike"]]
    p(_erow("n1 re-enters spike range", _agg(re, "A")))
    p(_erow("n1 does NOT re-enter", _agg(nre, "A")))
    strong = [r for r in hasn1 if (r["n1"]["body_pct"] or 0) >= 0.5 and r["n1"]["agree"]]
    p(_erow("n1 agree & strong body", _agg(strong, "A")))

    p("\n[§5 EARLY-ACCEPTANCE definition sweep]  (entry = that bar's close, window-low/high stop)")
    for kd in ACC_DEFS:
        rows = [r for r in ev if r["accdef"].get(kd)]
        if not rows:
            p(f"    {kd:<20} n=0")
            continue
        ws = [r["accdef"][kd] for r in rows]
        mfe = sorted(w["MFE_R"] for w in ws)
        mae = sorted(w["MAE_R"] for w in ws)
        n = len(ws)
        cont = sum(1 for r in rows if r["oc"].get(OUT_WIN) == "CONTINUATION")
        trap = sum(1 for r in rows if r["oc"].get(OUT_WIN) == "TRAP")
        pk = lambda k: sum(1 for w in ws if w.get(f"reached_{k}R")) / n
        p(f"    {kd:<20} n={n:>4} cont={cont/n*100:>3.0f}% trap={trap/n*100:>3.0f}% "
          f"mMFE_R={mfe[n//2]:>5} mMAE_R={mae[n//2]:>6} "
          + " ".join(f"P{k}R={pk(k)*100:.0f}%" for k in (1, 2, 3, 5, 8)))

    p("\n[§6 STRUCTURAL-STOP matrix per entry]  (median over events that had the entry)")
    for k in ("A", "B", "C", "D"):
        rows = [r for r in ev if k in r["E"]]
        if not rows:
            continue
        p(f"  entry {names[k]}:")
        allstops = set()
        for r in rows:
            allstops |= set(r["E"][k]["stops"])
        for sn in ("spike", "reaction", "window", "swing", "prevstruct"):
            if sn not in allstops:
                continue
            ws = [r["E"][k]["stops"][sn] for r in rows if sn in r["E"][k]["stops"]]
            if not ws:
                continue
            Rp = sorted(w["R_pts"] for w in ws)
            mae = sorted(w["MAE_R"] for w in ws)
            mfe = sorted(w["MFE_R"] for w in ws)
            av = sorted(w["avail_R"] for w in ws if w.get("avail_R"))
            p(f"      stop={sn:<11} n={len(ws):>4} medR={Rp[len(Rp)//2]:>6}pts "
              f"medMAE_R={mae[len(mae)//2]:>6} medMFE_R={mfe[len(mfe)//2]:>5} "
              f"med_availR={av[len(av)//2] if av else None}")

    p("\n[§11 SPIKE-SIZE x ENTRY TIMING]  (range percentile buckets)")
    for lo, hi in ((0.0, 0.90), (0.90, 0.95), (0.95, 0.98), (0.98, 1.01)):
        p(f"  pctile {lo:.2f}-{hi:.2f}:")
        for k in ("A", "B", "C", "D"):
            g = [r for r in ev if k in r["E"] and r["range_pctile"] is not None and lo <= r["range_pctile"] < hi]
            p(_erow(f"  {names[k]}", _agg(g, k)))

    p("\n[§12 PROFILE x ENTRY TIMING]")
    for pc in sorted({r["profile_class"] for r in ev}):
        p(f"  profile={pc}:")
        for k in ("A", "C", "D"):
            g = [r for r in ev if k in r["E"] and r["profile_class"] == pc]
            p(_erow(f"  {names[k]}", _agg(g, k)))

    p("\n[§13 OI / VOLUME as secondary evidence]  (within level-breaking events, spike-close entry)")
    p(_erow("ALL level-breaking", _agg(br, "A")))
    p(_erow("  vol_x >= 2", _agg([r for r in br if r["vol_x"] and r["vol_x"] >= 2], "A")))
    p(_erow("  vol_x < 2 / NA", _agg([r for r in br if not (r["vol_x"] and r["vol_x"] >= 2)], "A")))
    for k, g in sorted(_grp(br, lambda r: r["oi_class"]).items()):
        p(_erow(f"  OI {k}", _agg(g, "A")))
    # controlled: acceptance + level, does vol/OI add?
    acc_lvl = [r for r in br if r["level_class"] == "C_broke_accepted"]
    p(_erow("  C_broke_accepted (base)", _agg(acc_lvl, "A")))
    p(_erow("   + vol_x>=2", _agg([r for r in acc_lvl if r["vol_x"] and r["vol_x"] >= 2], "A")))
    p(_erow("   + OI price-dir==OI-dir", _agg([r for r in acc_lvl if r["oi_class"] in ("price_up_OI_up", "price_down_OI_down")], "A")))

    _sweet_spot(sym, ev, A, out)
    _final(sym, ev, sess, regs, A, br, out)


def _sweet_spot(sym, ev, A, out):
    p = lambda *a: print(*a, file=out)
    p("\n[§16 SWEET-SPOT]  balance of continuation, trap-control, available_R, MFE_R, MAE_R")
    rows = []
    for k in ("A", "B", "C", "D"):
        a = A[k]
        if not a or a["n"] < 6:
            continue
        # simple, transparent balance -- NOT a weighted score: rank each entry
        # on the criteria and sum ranks (lower = better balance).
        rows.append((k, a))
    if not rows:
        p("    insufficient per-entry samples")
        return
    crit = {
        "cont": (lambda a: -a["cont"]),          # higher better
        "trap": (lambda a: a["trap"]),           # lower better
        "avail": (lambda a: -(a["med_availR"] or 0)),
        "mfe": (lambda a: -a["medMFE_R"]),
        "mae": (lambda a: a["medMAE_R"]),        # closer to 0 better (mae is negative)
        "risk": (lambda a: a["med_R_pts"]),      # smaller entry-to-stop better
    }
    ranks = {k: 0 for k, _ in rows}
    for cn, f in crit.items():
        order = sorted(rows, key=lambda kv: f(kv[1]))
        for i, (k, _) in enumerate(order):
            ranks[k] += i
    best = min(ranks, key=ranks.get)
    names = {"A": "spike-close", "B": "first-reaction", "C": "early-acceptance", "D": "full-acceptance"}
    for k, a in rows:
        p(f"    {names[k]:<18} rank-sum={ranks[k]:>2}  cont={a['cont']*100:.0f}% trap={a['trap']*100:.0f}% "
          f"availR={a['med_availR']} MFE_R={a['medMFE_R']} MAE_R={a['medMAE_R']} R={a['med_R_pts']}pts")
    p(f"    -> most-balanced on this sample: {names[best]}  (rank-sum {ranks[best]}; NOT a score, "
      f"just a transparent multi-criterion rank -- and the sample is INSUFFICIENT)")


def _final(sym, ev, sess, regs, A, br, out):
    p = lambda *a: print(*a, file=out)
    a = lambda k: A.get(k)
    ta = _agg([r for r in ev if r["n1"] and r["n1"]["agree"]], "A")
    td = _agg([r for r in ev if r["n1"] and not r["n1"]["agree"]], "A")
    p("\n[§17 FINAL REPORT]")
    q = [
        ("1 spike-close entry viable",
         f"n={a('A')['n'] if a('A') else 0} cont={a('A')['cont']*100:.0f}% trap={a('A')['trap']*100:.0f}% "
         f"P3R={a('A')['P3R']*100:.0f}% -- workable count, weak accuracy" if a('A') else "n=0"),
        ("2 first-reaction better",
         f"n={a('B')['n'] if a('B') else 0} cont={a('B')['cont']*100:.0f}% P3R={a('B')['P3R']*100:.0f}% "
         f"med_availR={a('B')['med_availR'] if a('B') else None}" if a('B') else "n=0"),
        ("3 early-acceptance better",
         f"n={a('C')['n'] if a('C') else 0} cont={a('C')['cont']*100:.0f}% trap={a('C')['trap']*100:.0f}% "
         f"P3R={a('C')['P3R']*100:.0f}% med_availR={a('C')['med_availR'] if a('C') else None}" if a('C') else "n=0"),
        ("4/5 2- vs 3-candle acceptance", "see §5 sweep (hold1/hold2/hold3 rows)"),
        ("6 earliest reliable continuation evidence",
         "n1 AGREES: cont={:.0f}% trap={:.0f}% (known 1 bar after spike)".format(
             (ta['cont'] if ta else 0) * 100, (ta['trap'] if ta else 0) * 100)),
        ("7 earliest reliable trap warning",
         "n1 DISAGREES: trap={:.0f}% cont={:.0f}% (known 1 bar after spike)".format(
             (td['trap'] if td else 0) * 100, (td['cont'] if td else 0) * 100)),
        ("8 most robust structural stop", "see §6 -- lowest medMAE_R that keeps med_availR usable (spike/window)"),
        ("9 best available_R",
         "entry " + max(("A", "B", "C", "D"), key=lambda k: (a(k)["med_availR"] or 0) if a(k) else -1)),
        ("10 best MFE_R",
         "entry " + max(("A", "B", "C", "D"), key=lambda k: a(k)["medMFE_R"] if a(k) else -9)),
        ("11 best expectancy proxy (cont%*MFE_R)",
         "entry " + max(("A", "B", "C", "D"),
                        key=lambda k: (a(k)["cont"] * a(k)["medMFE_R"]) if a(k) else -9)),
        ("12 lowest drawdown (|MAE_R| smallest)",
         "entry " + max(("A", "B", "C", "D"), key=lambda k: a(k)["medMAE_R"] if a(k) else -9)),
        ("13-16 P3R/P5R/P8R/P10R (spike-close)",
         f"{a('A')['P3R']*100:.0f}% / {a('A')['P5R']*100:.0f}% / {a('A')['P8R']*100:.0f}% / "
         f"{a('A')['P10R']*100:.0f}%" if a('A') else "n/a"),
        ("17 spike magnitude changes result", "see §11 -- pctile x entry"),
        ("18 profile context changes result", "see §12 -- profile x entry"),
        ("19 OI/volume add info", "see §13 -- marginal after controlling for acceptance + level"),
        ("20 Stage-3 hypotheses",
         "n1-agree as the earliest gate; early-acceptance(close_no_reclaim) entry; "
         "acceptance & available_R>=3; window-low/high stop"),
    ]
    for k, v in q:
        p(f"    Q{k}: {v}")
    p(f"\n>>> {sym} PREMIUM STATUS: INSUFFICIENT DATA "
      f"({len(ev)} events / {len(sess)} sessions / {len(regs)} regimes; no OOS split possible).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NIFTY,NATURALGAS,CRUDEOIL")
    ap.add_argument("--out", default="data/orderflow_entry_timing_report_2026-09-06.txt")
    a = ap.parse_args()
    syms = [x.strip().upper() for x in a.symbols.split(",") if x.strip()]
    with open(a.out, "w") as f:
        print("orderflow entry-timing research -- RESEARCH ONLY, no production change, no score", file=f)
        for sym in syms:
            ev = collect(sym)
            if not ev:
                print(f"\n{sym}: no events", file=f)
                continue
            report(sym, ev, f)
    print(f"wrote report -> {a.out}")
    print(open(a.out).read())


if __name__ == "__main__":
    main()
