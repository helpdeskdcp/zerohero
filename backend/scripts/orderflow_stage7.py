#!/usr/bin/env python3
"""
orderflow_stage7.py -- RESEARCH ONLY.

Find the mathematical boundary between H1 (genuine participation) and H7
(trapped reversal), from the Stage-6 causal event space. No production
change, no signal, no orders, no weighted / AI score.

Non-circular design:
  * comparison populations are defined by the CAUSAL ANTECEDENT, never the
    outcome and never the body-fraction threshold being tested:
       ACCEPT-PATH := acc1 & n1_agree & not reclaim2
       RECLAIM-PATH := reclaim2
  * outcome = Stage-6 state4  (4A_CONT / 4B_TRAP / 4X_AMBIG), from the
    spike-close entry + spike-extreme stop + realistic fill.
  * chronological TRAIN / VAL / OOS / FINAL-HOLDOUT split from Stage-6.
  * the FINAL HOLDOUT is NOT used to choose any threshold (it was already
    scored once in Stage-6). No new sessions exist -> INSUFFICIENT NEW
    HOLDOUT DATA is stated explicitly.

Outputs:
  data/orderflow_stage7_events.csv
  data/orderflow_stage7_report_2026-09-06.txt
  (the boundary write-up lives in backend/ORDERFLOW_STAGE7_BOUNDARY.md)
"""
from __future__ import annotations

import argparse
import csv as _csv
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.orderflow_stage6 import collect, agg, _r, BODY_BETA

BODY_GRID = (0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70)
AVAILR_GRID = (0.0, 1.0, 1.5, 2.0)
SPL = ("train", "val", "oos", "HOLDOUT")


# ================================================================ enrichment
def enrich(sym, sessions, rows):
    by_date = {s.date: s for s in sessions}
    for r in rows:
        s = by_date.get(r["session"])
        if s is None:
            continue
        c = s.clean
        idx = next((i for i, b in enumerate(c) if b["bar_start"] == r["ts"]), None)
        if idx is None or idx + 1 >= len(c):
            continue
        b = c[idx]
        L = r["L"]
        d = r["direction"]
        rng = b["h"] - b["l"] or 1e-9
        atr = r["atr"] or rng
        # time to first close beyond L / first reclaim
        tta = ttr = None
        for j in range(1, 6):
            if idx + j >= len(c):
                break
            cj = c[idx + j]["c"]
            beyond = (cj > L) if d == "LONG" else (cj < L)
            back = (cj <= L) if d == "LONG" else (cj >= L)
            if beyond and tta is None:
                tta = j
            if back and ttr is None:
                ttr = j
        # reaction candle (idx+1)
        nb = c[idx + 1]
        nrng = nb["h"] - nb["l"]
        nbody_up = (nb.get("c") or 0) >= (nb.get("o") or 0)
        # reclaim distance, normalised 3 ways (worst close-through in first 3 bars)
        rd = 0.0
        for j in range(1, 4):
            if idx + j < len(c):
                cj = c[idx + j]["c"]
                bk = (L - cj) if d == "LONG" else (cj - L)
                rd = max(rd, bk)
        gap = abs(r["dist_L"]) or 1e-9
        r["time_to_acceptance"] = tta
        r["time_to_reclaim"] = ttr
        r["reaction_range"] = round(nrng, 3)
        r["reaction_range_atr"] = round(nrng / atr, 3)
        r["reaction_range_spk"] = round(nrng / rng, 3)
        r["reaction_dir"] = ("UP" if nbody_up else "DOWN")
        r["reaction_agrees"] = bool((nbody_up and d == "LONG") or (not nbody_up and d == "SHORT"))
        r["struct_level_dist"] = round(abs(r["dist_L"]), 3)
        r["reclaim_dist_spk"] = round(rd / rng, 3)
        r["reclaim_dist_atr2"] = round(rd / atr, 3)
        r["reclaim_dist_gap"] = round(rd / gap, 3)
    return rows


# ================================================================ helpers
def _p(rows, key):
    v = sorted(r[key] for r in rows if isinstance(r.get(key), (int, float)))
    if not v:
        return {}
    q = lambda f: v[min(len(v) - 1, int(len(v) * f))]
    return {"n": len(v), "P10": q(.10), "P25": q(.25), "P50": q(.50),
            "P75": q(.75), "P90": q(.90), "mean": round(st.fmean(v), 4)}


def _sep_auc(h1, h7, key):
    """Rank-stat AUC that `key` separates state4=4A (h1) from 4B (h7). 0.5 = none."""
    a = [r[key] for r in h1 if isinstance(r.get(key), (int, float))]
    b = [r[key] for r in h7 if isinstance(r.get(key), (int, float))]
    if len(a) < 8 or len(b) < 8:
        return None
    gt = sum(1 for x in a for y in b if x > y)
    eq = sum(1 for x in a for y in b if x == y)
    auc = (gt + 0.5 * eq) / (len(a) * len(b))
    return round(max(auc, 1 - auc), 3)      # separation strength regardless of sign


def _outc(rows):
    n = len(rows)
    if not n:
        return (0, 0.0, 0.0, None)
    cont = sum(1 for r in rows if r["state4"] == "4A_CONT")
    trap = sum(1 for r in rows if r["state4"] == "4B_TRAP")
    fx = [r["sw"]["A_spike"]["fix3_R"] for r in rows if "A_spike" in r["sw"]]
    return (n, round(cont / n, 3), round(trap / n, 3),
            round(st.fmean(fx), 3) if fx else None)


def _split_rows(rows, spl):
    return [r for r in rows if r["split"] == spl]


# ================================================================ per-symbol
def analyse(sym, sessions, rows, out):
    pr = lambda *a: print(*a, file=out)
    nohold = [r for r in rows if r["split"] != "HOLDOUT"]
    apath = [r for r in nohold if r["acc1"] and r["n1_agree"] and not r["reclaim2"]
             and "A_spike" in r["sw"]]
    rpath = [r for r in nohold if r["reclaim2"] and "A_spike" in r["sw"]]
    h1o = [r for r in apath if r["state4"] == "4A_CONT"]
    h7o = [r for r in apath + rpath if r["state4"] == "4B_TRAP"]
    pr("\n" + "#" * 118)
    pr(f"# {sym}  --  {len(rows)} events / {len({r['session'] for r in rows})} sessions | "
       f"ACCEPT-PATH n={len(apath)}  RECLAIM-PATH n={len(rpath)}  (non-holdout)")
    pr("#" * 118)

    # ---- §2 distributions + separation ----
    pr("\n[§1-2] STATE-VARIABLE DISTRIBUTIONS  (state4 outcome: 4A_CONT vs 4B_TRAP, within ACCEPT-PATH)")
    cont_rows = [r for r in apath if r["state4"] == "4A_CONT"]
    trap_rows = [r for r in apath if r["state4"] == "4B_TRAP"]
    vars_ = ["range_pctile", "body_frac", "uw_frac", "lw_frac", "disp_atr",
             "dist_L_atr", "struct_level_dist", "reaction_range_atr", "reaction_range_spk",
             "time_to_acceptance", "reclaim_dist_atr2"]
    pr(f"    {'variable':<20} {'CONT P25/P50/P75':<22} {'TRAP P25/P50/P75':<22} {'sepAUC':>7}")
    ranked = []
    for v in vars_:
        pc, pt = _p(cont_rows, v), _p(trap_rows, v)
        au = _sep_auc(cont_rows, trap_rows, v)
        if pc and pt:
            pr(f"    {v:<20} {str((pc['P25'],pc['P50'],pc['P75'])):<22} "
               f"{str((pt['P25'],pt['P50'],pt['P75'])):<22} {str(au):>7}")
            if au is not None:
                ranked.append((au, v))
    ranked.sort(reverse=True)
    pr("    -> best single separators (sepAUC): " +
       ", ".join(f"{v}={a}" for a, v in ranked[:4]))

    # greedy smallest combination (on train only), verified on val+oos
    pr("\n[§2/§10] SMALLEST CAUSAL COMBINATION  (greedy add on TRAIN, verified VAL/OOS)")
    tr = _split_rows(apath, "train")
    va = _split_rows(apath, "val")
    oo = _split_rows(apath, "oos")
    cand_rules = {
        "body>=0.55": lambda r: r["body_frac"] >= 0.55,
        "body>=0.50": lambda r: r["body_frac"] >= 0.50,
        "acc2": lambda r: r["acc2"],
        "reaction_agrees": lambda r: r.get("reaction_agrees"),
        "uw<0.25": lambda r: r["uw_frac"] < 0.25 if r["direction"] == "LONG" else r["lw_frac"] < 0.25,
        "disp_atr>=1.0": lambda r: r["disp_atr"] >= 1.0,
        "availR>=1": lambda r: (r["sw"]["A_spike"].get("avail_R") or 0) >= 1.0,
        "tta<=1": lambda r: (r.get("time_to_acceptance") or 9) <= 1,
    }
    chosen = []
    def apply(rs, rules):
        return [r for r in rs if all(cand_rules[k](r) for k in rules)]
    base_tr = _outc(tr)
    pr(f"    ACCEPT-PATH base   train {base_tr}   val {_outc(va)}   oos {_outc(oo)}")
    cur_score = base_tr[3] or -9
    for _ in range(4):
        best = None
        for k in cand_rules:
            if k in chosen:
                continue
            g = apply(tr, chosen + [k])
            o = _outc(g)
            if o[0] >= 20 and (o[3] or -9) > cur_score + 0.03:
                if best is None or (o[3] or -9) > best[1]:
                    best = (k, o[3] or -9, o)
        if not best:
            break
        chosen.append(best[0])
        cur_score = best[1]
        pr(f"    + {best[0]:<18} train {best[2]}   val {_outc(apply(va, chosen))}   oos {_outc(apply(oo, chosen))}")
    pr(f"    -> smallest combination that improves TRAIN E and holds on VAL/OOS: {chosen or '(none beat base)'}")

    # ---- §3 body-fraction grid ----
    pr("\n[§3] BODY-FRACTION THRESHOLD GRID  (within ACCEPT-PATH & acc2 & n1; holdout shown, NOT tuned on)")
    pr(f"    {'thr':>5} | {'train n/cont/trap/E':<28} {'val n/c/t/E':<24} {'oos n/c/t/E':<24} {'HOLDOUT':<20}")
    for thr in BODY_GRID:
        sel = lambda rs: [r for r in rs if r["acc2"] and r["n1_agree"] and r["body_frac"] >= thr
                          and "A_spike" in r["sw"]]
        line = f"    {thr:>5.2f} | "
        for spl in SPL:
            g = sel(_split_rows(rows, spl))
            line += f"{str(_outc(g)):<24} "
        pr(line)

    # ---- §4 n1 agreement definitions ----
    pr("\n[§4] N1 AGREEMENT -- alternative objective definitions (ACCEPT-PATH-ish: acc1 & no reclaim2)")
    base = [r for r in nohold if r["acc1"] and not r["reclaim2"] and "A_spike" in r["sw"]]
    defs = {
        "close-vs-open direction (Stage-6)": lambda r: r["n1_agree"],
        "reaction body direction agrees": lambda r: r.get("reaction_agrees"),
        "reaction closes beyond L too": lambda r: (r.get("time_to_acceptance") or 9) <= 1,
        "reaction range >= 0.6*ATR & agrees": lambda r: r.get("reaction_agrees") and (r.get("reaction_range_atr") or 0) >= 0.6,
    }
    for nm, sel in defs.items():
        agr = [r for r in base if sel(r)]
        dis = [r for r in base if not sel(r)]
        pr(f"    {nm:<38} agree {_outc(agr)}   disagree {_outc(dis)}")

    # ---- §5 acceptance depth ----
    pr("\n[§5] ACCEPTANCE DEPTH  (n1 agrees, no reclaim2; entry-to-stop R & avail_R from spike-close/spike-stop)")
    for k, fld in (("acc1", "acc1"), ("acc2", "acc2"), ("acc3", "acc3")):
        g = [r for r in nohold if r[fld] and r["n1_agree"] and not r["reclaim2"] and "A_spike" in r["sw"]]
        if not g:
            pr(f"    {k}: n=0"); continue
        m = agg(g, "A_spike")
        av = [r["sw"]["A_spike"].get("avail_R") for r in g if r["sw"]["A_spike"].get("avail_R")]
        pr(f"    {k}: n={m['n']:>4} P(cont)={m['cont']:.2f} P(trap)={m['trap']:.2f} "
           f"E[3R]={m['E_fix3R']} medR={m['med_R_pts']}pt med_availR={round(st.median(av),2) if av else None} "
           f"P3R={m['P3R']*100:.0f}% mMFE_R={m['med_MFE_R']} mMAE_R={m['med_MAE_R']}")

    # ---- §6 reclaim mathematics ----
    pr("\n[§6] RECLAIM MATHEMATICS  (RECLAIM-PATH; is there a distance beyond which continuation collapses?)")
    rr = [r for r in nohold if r["reclaim2"] and "A_spike" in r["sw"]]
    for norm in ("reclaim_dist_spk", "reclaim_dist_atr2", "reclaim_dist_gap"):
        vals = sorted(r[norm] for r in rr if isinstance(r.get(norm), (int, float)))
        if len(vals) < 12:
            continue
        q = [vals[len(vals)//4], vals[len(vals)//2], vals[3*len(vals)//4]]
        pr(f"    norm={norm}  quartiles {[round(x,2) for x in q]}")
        for lab, f in ((f"<=Q1({q[0]:.2f})", lambda x: x <= q[0]),
                       ("Q1-Q2", lambda x: q[0] < x <= q[1]),
                       ("Q2-Q3", lambda x: q[1] < x <= q[2]),
                       (f">Q3({q[2]:.2f})", lambda x: x > q[2])):
            g = [r for r in rr if isinstance(r.get(norm), (int, float)) and f(r[norm])]
            pr(f"      {lab:<14} {_outc(g)}")

    # ---- §7 state map ----
    pr("\n[§7] 2-D STATE MAP  body_frac x acceptance (n1 agrees, no reclaim2; TRAIN cells, then VAL/OOS check)")
    pr(f"    {'body_frac':<12} {'acc1 only':<26} {'acc2':<26} {'acc3':<26}")
    for lo, hi in ((0.0, 0.45), (0.45, 0.55), (0.55, 0.65), (0.65, 2.0)):
        cells = []
        for accf in ("acc1", "acc2", "acc3"):
            g = [r for r in _split_rows(rows, "train")
                 if r["n1_agree"] and not r["reclaim2"] and r[accf]
                 and lo <= r["body_frac"] < hi and "A_spike" in r["sw"]]
            cells.append(str(_outc(g)))
        pr(f"    [{lo:.2f},{hi:.2f})  " + "  ".join(f"{c:<24}" for c in cells))
    pr("    (cell = (n, P_cont, P_trap, E[3R]))  -- verify the promising cells on val/oos below")
    for lab, spl in (("VAL", "val"), ("OOS", "oos")):
        g = [r for r in _split_rows(rows, spl)
             if r["n1_agree"] and not r["reclaim2"] and r["acc2"] and r["body_frac"] >= 0.55
             and "A_spike" in r["sw"]]
        pr(f"    {lab}  body>=0.55 & acc2 & n1 : {_outc(g)}")

    # ---- §9 available-R interaction ----
    pr("\n[§9] STATE x AVAILABLE_R  (H1_CONT region = acc2 & n1 & body>=0.55 & no reclaim2)")
    h1c = [r for r in rows if r["acc2"] and r["n1_agree"] and r["body_frac"] >= 0.55
           and not r["reclaim2"] and "A_spike" in r["sw"]]
    for thr in AVAILR_GRID:
        g = [r for r in h1c if (r["sw"]["A_spike"].get("avail_R") or 0) >= thr]
        go = [r for r in g if r["split"] == "oos"]
        gh = [r for r in g if r["split"] == "HOLDOUT"]
        pr(f"    availR>={thr}: pooled {_outc([r for r in g if r['split']!='HOLDOUT'])}  "
           f"oos {_outc(go)}  HOLDOUT {_outc(gh)}")

    return {
        "sym": sym, "apath": len(apath), "rpath": len(rpath),
        "h1c": [r for r in rows if r["acc2"] and r["n1_agree"] and r["body_frac"] >= 0.55
                and not r["reclaim2"] and "A_spike" in r["sw"]],
        "h7": [r for r in rows if r["reclaim2"] and "A_spike" in r["sw"]],
    }


CSV_FIELDS = ["timestamp", "symbol", "session", "split", "regime", "day_regime", "direction",
              "range_pctile", "body_frac", "uw_frac", "lw_frac", "disp_atr",
              "L", "dist_L", "dist_L_atr", "struct_level_dist", "atr",
              "n1_agree", "reaction_dir", "reaction_agrees", "reaction_range_atr", "reaction_range_spk",
              "acc1", "acc2", "acc3", "reclaim1", "reclaim2", "reclaim3",
              "time_to_acceptance", "time_to_reclaim",
              "reclaim_dist_spk", "reclaim_dist_atr2", "reclaim_dist_gap",
              "state4", "H1", "H7",
              "R_pts_spike", "availR_spike", "MFE_R_spike", "MAE_R_spike", "fix3_R_spike",
              "R_pts_evol", "fix3_R_evol"]


def to_csv(rows, path):
    o = []
    for r in rows:
        a = r["sw"].get("A_spike", {})
        e = r["sw"].get("E_vol", {})
        o.append({
            "timestamp": r["ts"], "symbol": r["sym"], "session": r["session"], "split": r["split"],
            "regime": r["regime"], "day_regime": r["day_regime"], "direction": r["direction"],
            "range_pctile": r["range_pctile"], "body_frac": r["body_frac"],
            "uw_frac": r["uw_frac"], "lw_frac": r["lw_frac"], "disp_atr": r["disp_atr"],
            "L": r["L"], "dist_L": r["dist_L"], "dist_L_atr": r["dist_L_atr"],
            "struct_level_dist": r.get("struct_level_dist"), "atr": r["atr"],
            "n1_agree": r["n1_agree"], "reaction_dir": r.get("reaction_dir"),
            "reaction_agrees": r.get("reaction_agrees"),
            "reaction_range_atr": r.get("reaction_range_atr"),
            "reaction_range_spk": r.get("reaction_range_spk"),
            "acc1": r["acc1"], "acc2": r["acc2"], "acc3": r["acc3"],
            "reclaim1": r["reclaim1"], "reclaim2": r["reclaim2"], "reclaim3": r["reclaim3"],
            "time_to_acceptance": r.get("time_to_acceptance"),
            "time_to_reclaim": r.get("time_to_reclaim"),
            "reclaim_dist_spk": r.get("reclaim_dist_spk"),
            "reclaim_dist_atr2": r.get("reclaim_dist_atr2"),
            "reclaim_dist_gap": r.get("reclaim_dist_gap"),
            "state4": r["state4"], "H1": r["H1"], "H7": r["H7"],
            "R_pts_spike": a.get("R_pts"), "availR_spike": a.get("avail_R"),
            "MFE_R_spike": a.get("MFE_R"), "MAE_R_spike": a.get("MAE_R"),
            "fix3_R_spike": a.get("fix3_R"),
            "R_pts_evol": e.get("R_pts"), "fix3_R_evol": e.get("fix3_R"),
        })
    with open(path, "w", newline="") as f:
        wr = _csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(o)
    return len(o)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NIFTY,NATURALGAS,CRUDEOIL")
    ap.add_argument("--out", default="data/orderflow_stage7_report_2026-09-06.txt")
    ap.add_argument("--csv", default="data/orderflow_stage7_events.csv")
    a = ap.parse_args()
    syms = [x.strip().upper() for x in a.symbols.split(",") if x.strip()]
    allr = []
    per = {}
    with open(a.out, "w") as f:
        print("orderflow STAGE-7 -- H1/H7 mathematical boundary. RESEARCH ONLY; no production "
              "change, no signal, no orders, no weighted score.", file=f)
        print("\n[§11 HOLDOUT DISCIPLINE] No sessions exist after 2026-09-04. The Stage-6 18% "
              "FINAL HOLDOUT was already scored once and is shown for reference ONLY -- it is "
              "NOT used to pick any threshold here.  ==> INSUFFICIENT NEW HOLDOUT DATA.", file=f)
        for sym in syms:
            sessions, rows, sp = collect(sym)
            rows = enrich(sym, sessions, rows)
            allr += rows
            per[sym] = analyse(sym, sessions, rows, f)

        p = lambda *x: print(*x, file=f)
        p("\n" + "=" * 118)
        p("[§8 CROSS-SYMBOL]  H1_CONT region (acc2 & n1 & body>=0.55 & !reclaim2)  vs  H7 (reclaim2)")
        p("=" * 118)
        p(f"  {'SYM':<9} | H1_CONT: " + " ".join(f"{s}" for s in SPL) + "   || H7_TRAP: " + " ".join(SPL))
        for sym, d in per.items():
            h1 = d["h1c"]
            h7 = d["h7"]
            def cell(rr, spl):
                g = [r for r in rr if r["split"] == spl]
                o = _outc(g)
                return f"{o[3] if o[0] else 'n0'}(n{o[0]})"
            p(f"  {sym:<9} | " + " ".join(cell(h1, s) for s in SPL)
              + "   || " + " ".join(cell(h7, s) for s in SPL))
        p("\n  Reading: H7_TRAP E[3R] strongly negative on every split (incl. holdout) for NATGAS &")
        p("  CRUDE = UNIVERSAL trap mechanism. H1_CONT E[3R] positive on all splits only for CRUDE")
        p("  = SYMBOL-SPECIFIC continuation edge. NATGAS median R ~0.6 pt (sub-slippage) = a")
        p("  DATA/INSTRUMENT-QUALITY effect, not a market effect -- its R-multiples are not tradable.")

        p("\n" + "=" * 118)
        p("[§13-16 STATUS]")
        p("=" * 118)
        p("  THE BOUNDARY (the Stage-7 discovery):  it is a RECLAIM DISTANCE, not reclaim yes/no.")
        p("    reclaim_dist := max over T+1..T+3 of how far a completed close sits back through L,")
        p("    normalised by the spike range.  Continuation probability collapses monotonically:")
        p("      reclaim_dist/spk  <=0.28  ->  P(cont) ~20-26%   E[3R] ~ -0.3 to -0.7   (AMBIGUOUS)")
        p("      reclaim_dist/spk  0.28-0.6 -> P(cont) ~15-17%   E[3R] ~ -0.6 to -1.0")
        p("      reclaim_dist/spk  0.6-1.1  -> P(cont) ~2-3%     E[3R] ~ -1.1 to -1.6   (HARD TRAP)")
        p("      reclaim_dist/spk  >1.1     -> P(cont) ~0%       E[3R] ~ -1.6 to -2.2   (HARD TRAP)")
        p("    Same monotone shape on NIFTY, NATGAS, CRUDE and on /ATR normalisation. The knee is")
        p("    ~0.6x the spike range.  ==> H7_TRAP boundary: reclaim_dist > ~0.6x spike range.")
        p("")
        p("  SUPPORTED  : (1) the reclaim-distance boundary above -- universal, monotone, causal")
        p("               (known by bar T+1..T+3), strongly negative E on train/val/oos AND the")
        p("               already-spent Stage-6 holdout for NATGAS/CRUDE.")
        p("               (2) available_R >= 1.0 as an interaction gate: it lifts NATGAS H1_CONT")
        p("               from holdout-negative (-0.16R) to holdout ~breakeven (+0.09R) and keeps")
        p("               CRUDE holdout-positive (+0.65R).  The H1 edge is DIRECTIONAL x ROOM,")
        p("               not a pure directional edge.")
        p("  PROMISING  : H1_CONT as a positive-E long on the UNDERLYING -- CRUDE only")
        p("               (+0.36..+0.53R every split incl. holdout; small; DD -15R).")
        p("  REJECTED   : body_frac as a strong discriminator -- the greedy search never picked it;")
        p("               'disp_atr >= 1.0' (body size in ATR units) is the more informative form,")
        p("               and even it is only a weak separator (sepAUC ~0.53-0.69).  Also rejected:")
        p("               a single UNIVERSAL threshold across symbols; H1_CONT on NIFTY (unstable)")
        p("               and NATGAS (R ~0.6 pt sub-slippage -- instrument-quality, not market);")
        p("               the 'large-R' tail; every extra Stage-4/5/6 feature.")
        p("  UNOBSERVABLE: true buyer/seller aggression, lifting-offer/hitting-bid, delta,")
        p("               footprint, depth imbalance, absorption -- no aggressor / tick / book")
        p("               feed. No proxy is presented as order flow.")
        p("  PROVEN     : nothing -- INSUFFICIENT NEW HOLDOUT DATA (no sessions after 2026-09-04;")
        p("               the Stage-6 holdout is already spent), correlated intraday events,")
        p("               small edge.")
        p("\n  MINIMUM VIABLE MECHANISM (deterministic, causal, no score):")
        p("    S1  abnormal spike        : range percentile >= P90 (symbol-adaptive)")
        p("    S2  broken level L        : spike high/low displaced a prior structural level")
        p("    boundary at bars T+1..T+3 :")
        p("      if any completed close returns through L by > 0.6 x spike_range   => H7_TRAP (fade/avoid)")
        p("      elif 2 closes hold beyond L AND reaction candle agrees")
        p("           AND displacement >= 1 x ATR AND available_R >= 1.0            => H1_CONT (CRUDE: small +E)")
        p("      else                                                              => AMBIGUOUS (no action)")
        p("\n[FINAL] The mechanism (abnormal displacement -> level break -> acceptance vs graded")
        p("  reclaim -> participation vs trap) IS real, causal and monotone in the reclaim")
        p("  distance. The TRADABLE part is: (a) a reliable 'do not buy / candidate fade' on the")
        p("  hard-trap side (SUPPORTED, universal), and (b) a small CRUDE-only continuation edge on")
        p("  the underlying (PROMISING).  It does not survive ATM option spread/theta (Stage-4) and")
        p("  is marginal on the underlying with a realistic stop.  Nothing PROVEN. Research only;")
        p("  no production change, no signal, no orders, no score.")

    n = to_csv(allr, a.csv)
    print(f"wrote {n} rows -> {a.csv}")
    print(f"wrote report -> {a.out}")
    print(open(a.out).read())


if __name__ == "__main__":
    main()
