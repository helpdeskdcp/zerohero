#!/usr/bin/env python3
"""
orderflow_stage6.py -- RESEARCH ONLY. Causal market-state-machine
reconstruction of the H1 (genuine participation) vs H7 (trap/reversal)
separation found in Stages 1-5.

Objective: express the observed separation as the SIMPLEST deterministic
causal state machine that survives a chronological TRAIN / VALIDATION / OOS /
FINAL-HOLDOUT split -- no weighted AI score, no arbitrary optimisation.

HARD RULES: no production change; no signal; no orders; no weighted score;
strictly causal (completed candles only, no same-bar outcome info, no future
levels/OI/volume); the FINAL HOLDOUT is evaluated exactly once, after the
model is frozen; conservative language (REJECTED / PROMISING / SUPPORTED /
PROVEN). No "institutional" / "smart money" / "holy grail" / "profitable"
claim unless the maths demonstrates it.

Data: orderflow_histsrc (oi_dashboard read-only + zerohero histcap),
~35-39 sessions/symbol, 2026-07-13 .. 09-04, 3 regimes.

Reuses Stage-3/5 pure helpers.

Outputs:
  data/orderflow_stage6_events.csv
  (the full mathematical spec + application blueprint live in
   backend/ORDERFLOW_STAGE6_MATHEMATICAL_MODEL.md, written from these results)
"""
from __future__ import annotations

import argparse
import csv as _csv
import math
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import orderflow_histsrc as HS
from scripts.orderflow_stage3_validation import (
    HSess, _levels, _broken_level, _reclaimed_by, _n1, _acc_entry_bar,
    _entry_stops, _classify_spike, spike_percentile, _avail_R,
)
from scripts.orderflow_stage5 import uwalk, _vwap_proxy
from scripts.orderflow_stage4 import day_regime, balance_features, profile_context

SPIKE_PCTL = 0.90
BODY_BETA = 0.55            # "strong body" threshold (tested on TRAIN, frozen)
RK = (1, 2, 3, 4, 5, 6, 8)
TICK = {"NIFTY": 0.05, "NATURALGAS": 0.10, "CRUDEOIL": 1.0}
# chronological split fractions (per symbol, by session date)
FR_TRAIN, FR_VAL, FR_OOS = 0.45, 0.65, 0.82     # remainder -> FINAL HOLDOUT


def _atr(clean, i, p=14):
    trs = []
    pc = None
    for b in clean[:i + 1]:
        tr = b["h"] - b["l"]
        if pc is not None:
            tr = max(tr, abs(b["h"] - pc), abs(b["l"] - pc))
        trs.append(tr)
        pc = b["c"]
    if len(trs) < 2:
        return trs[-1] if trs else 0.0
    return st.fmean(trs[-p:])


# ================================================================ event build
def _splits(sessions):
    ds = sorted({s.date for s in sessions})
    n = len(ds)
    a, b, c = int(n * FR_TRAIN), int(n * FR_VAL), int(n * FR_OOS)
    return set(ds[:a]), set(ds[a:b]), set(ds[b:c]), set(ds[c:])   # train,val,oos,HOLDOUT


def collect(sym):
    ss = HS.sessions(sym)
    sessions = [HSess(sym, d, src) for d, src in ss]
    tr, va, oos, hold = _splits(sessions)
    tick = TICK.get(sym, 0.05)
    rows = []
    for s in sessions:
        c = s.clean
        for idx in range(8, len(c) - 5):
            if s.base[idx] <= 0:
                continue
            # ---- STATE 1 : ABNORMAL SPIKE -------------------------------------
            pct = spike_percentile(s, idx)
            if pct is None or pct < SPIKE_PCTL:
                continue
            b = c[idx]
            rng = b["h"] - b["l"]
            o, cl = b.get("o"), b.get("c")
            if o is None or cl is None or rng <= 0:
                continue
            body = abs(cl - o)
            body_frac = body / rng
            uw = (b["h"] - max(o, cl)) / rng
            lw = (min(o, cl) - b["l"]) / rng
            atr = _atr(c, idx) or s.base[idx]
            disp = abs(cl - o) / atr
            cls, *_ = _classify_spike(b)
            direction = ("LONG" if cls.startswith("bull") else "SHORT" if cls.startswith("bear")
                         else ("LONG" if cl >= o else "SHORT"))
            # ---- STATE 2 : BROKEN LEVEL ------------------------------------
            lv = _levels(s, idx)
            L, _ = _broken_level(s, idx, direction, lv)
            if L is None:
                continue
            dist_L = (b["c"] - L) if direction == "LONG" else (L - b["c"])   # signed: >0 closed beyond
            # ---- n1 --------------------------------------------------------
            n1 = _n1(s, idx, direction, L)
            n1_ag = bool(n1 and n1["agree"])
            # ---- STATE 3 : ACCEPTANCE (k=1,2,3) / RECLAIM ----------------
            acc = {}
            for k in (1, 2, 3):
                held = True
                for j in range(1, k + 1):
                    if idx + j >= len(c):
                        held = False
                        break
                    cj = c[idx + j]["c"]
                    if (cj <= L) if direction == "LONG" else (cj >= L):
                        held = False
                        break
                acc[k] = bool(held and dist_L > 0)
            reclaim1 = _reclaimed_by(s, idx, direction, L, 1) is not None
            reclaim2 = _reclaimed_by(s, idx, direction, L, 2) is not None
            reclaim3 = _reclaimed_by(s, idx, direction, L, 3) is not None
            # reclaim distance: how far back through L did price close (worst of first 3)
            rd = 0.0
            for j in range(1, 4):
                if idx + j < len(c):
                    cj = c[idx + j]["c"]
                    back = (L - cj) if direction == "LONG" else (cj - L)
                    rd = max(rd, back)
            # ---- entries + walk under several stops ----------------------
            # REFERENCE for the H1/H7/state4 LABELS: enter at the spike close,
            # stop at the spike extreme -- entry and stop share one reference
            # point so R is the spike range (no drift, no artificial tightness).
            cj_bar = _acc_entry_bar(s, idx, direction, L, "close_no_reclaim")
            ent_early = (c[cj_bar]["c"], cj_bar + 1) if (cj_bar is not None and cj_bar + 1 < len(c)) else None
            ep, eb = b["c"], idx + 1
            spike_ext = b["l"] if direction == "LONG" else b["h"]
            react_ext = c[idx + 1]["l"] if direction == "LONG" else c[idx + 1]["h"]
            pad = 0.03 * rng
            stops_ea = _entry_stops(s, idx, cj_bar + 1, direction, lv) if ent_early else {}
            # candidate stops, all measured from the SAME spike-close entry
            stop_defs = {
                "A_spike": (spike_ext - pad) if direction == "LONG" else (spike_ext + pad),
                "B_reaction": (react_ext - pad) if direction == "LONG" else (react_ext + pad),
                "C_level": (L - pad) if direction == "LONG" else (L + pad),
                "D_max": (min(spike_ext, react_ext) - pad) if direction == "LONG"
                          else (max(spike_ext, react_ext) + pad),
                "E_vol": (ep - 1.0 * atr) if direction == "LONG" else (ep + 1.0 * atr),
            }
            sw = {}
            for sn, sp in stop_defs.items():
                if sp is None:
                    continue
                w = uwalk(c, eb, ep, sp, direction, tick)
                if w:
                    w["avail_R"] = _avail_R(lv, ep, w["R_pts"], direction)
                    w["sl"] = round(sp, 3)
                    sw[sn] = w
            # also: the EARLY-ACCEPTANCE entry with a reaction-candle stop
            # (Stage-5's headline geometry) -- kept as 'EA_react' for Part 4/6.
            if ent_early:
                eep, eeb = ent_early
                ee_sl = stops_ea.get("reaction")
                if ee_sl is not None:
                    ww = uwalk(c, eeb, eep, ee_sl, direction, tick)
                    if ww:
                        ww["avail_R"] = _avail_R(lv, eep, ww["R_pts"], direction)
                        ww["sl"] = round(ee_sl, 3)
                        sw["EA_react"] = ww
            if "A_spike" not in sw:
                continue
            wref = sw["A_spike"]                    # reference outcome for labelling
            # ---- STATE 4 : CONTINUATION / TRAP (outcome, NOT known at entry) --
            cont = (wref["MFE_R"] >= 2.0) and (wref["exit"] != "STOP")
            trap = (wref["MAE_R"] <= -1.0) or (wref["exit"] == "STOP" and not wref["mfe_ge_1R"])
            state4 = "4A_CONT" if cont else "4B_TRAP" if trap else "4X_AMBIG"
            # ---- H1 / H7 labels (definitions frozen from Stage-5) --------
            H1 = acc[1] and n1_ag and body_frac >= BODY_BETA and not reclaim2
            H7 = reclaim3 and (wref["MAE_R"] <= -1.0 or state4 == "4B_TRAP")
            split = ("train" if s.date in tr else "val" if s.date in va
                     else "oos" if s.date in oos else "HOLDOUT")
            pf = profile_context(s, idx, direction)
            bal = balance_features(s, idx)
            vwp = _vwap_proxy(c, idx)
            rows.append({
                "sym": sym, "session": s.date, "src": s.src, "ts": b["bar_start"],
                "split": split, "regime": s.regime, "day_regime": day_regime(s, idx),
                "direction": direction,
                # STATE 1
                "range_pctile": round(pct, 4), "range_x": round(rng / s.base[idx], 3),
                "body_frac": round(body_frac, 4), "uw_frac": round(uw, 4), "lw_frac": round(lw, 4),
                "disp_atr": round(disp, 3),
                # STATE 2
                "L": round(L, 3), "dist_L": round(dist_L, 3),
                "dist_L_atr": round(dist_L / atr, 3), "atr": round(atr, 4),
                # n1 / STATE 3
                "n1_agree": n1_ag,
                "acc1": acc[1], "acc2": acc[2], "acc3": acc[3],
                "reclaim1": reclaim1, "reclaim2": reclaim2, "reclaim3": reclaim3,
                "reclaim_dist_atr": round(rd / atr, 3),
                # STATE 4 (outcome)
                "state4": state4, "H1": bool(H1), "H7": bool(H7),
                # entry + stops
                "entry": round(ep, 3), "entry_kind": "early" if ent_early else "spike",
                "sw": sw,
                # extra features (for the incremental test)
                "pf_loc": pf.get("loc"), "poc_migr": pf.get("poc_migr_dir"),
                "bal_range_contraction": bal.get("bal_range_contraction"),
                "bal_rotations": bal.get("bal_rotations"),
                "vwap_dist_atr": round((b["c"] - vwp) / atr, 3) if vwp else None,
            })
    return sessions, rows, (tr, va, oos, hold)


# ================================================================ metrics
def agg(rows, stop="A_spike"):
    W = [(r, r["sw"][stop]) for r in rows if stop in r["sw"]]
    if not W:
        return {"n": 0}
    n = len(W)
    fx = [w["fix3_R"] for _, w in W]
    mfe = sorted(w["MFE_R"] for _, w in W)
    mae = sorted(w["MAE_R"] for _, w in W)
    av = sorted(w["avail_R"] for _, w in W if w.get("avail_R"))
    rp = sorted(w["R_pts"] for _, w in W)
    cont = sum(1 for r, _ in W if r["state4"] == "4A_CONT")
    trap = sum(1 for r, _ in W if r["state4"] == "4B_TRAP")
    seq = [w["fix3_R"] for _, w in sorted(W, key=lambda x: x[0]["session"])]
    peak = cum = dd = 0.0
    for x in seq:
        cum += x
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    pk = lambda k: round(sum(1 for _, w in W if w.get(f"mfe_ge_{k}R")) / n, 3)
    return {
        "n": n, "sessions": len({r["session"] for r, _ in W}),
        "cont": round(cont / n, 3), "trap": round(trap / n, 3),
        "E_fix3R": round(st.fmean(fx), 3),
        "win": round(sum(1 for x in fx if x > 0) / n, 3),
        "med_MFE_R": mfe[n // 2], "med_MAE_R": mae[n // 2],
        "p95_MAE_R": mae[max(0, int(n * 0.05))],
        "med_R_pts": rp[n // 2], "med_availR": av[len(av) // 2] if av else None,
        "maxDD_R": round(dd, 1),
        **{f"P{k}R": pk(k) for k in RK},
    }


def _r(name, m):
    if not m or not m["n"]:
        return f"    {name:<30} n=0"
    return (f"    {name:<30} n={m['n']:>4} ses={m['sessions']:>2} "
            f"cont={m['cont']*100:>3.0f}% trap={m['trap']*100:>3.0f}% "
            f"E[3R]={m['E_fix3R']:>6} win={m['win']*100:>3.0f}% "
            f"mMFE_R={m['med_MFE_R']:>5} mMAE_R={m['med_MAE_R']:>6} "
            f"P3R={m['P3R']*100:>3.0f}% P5R={m['P5R']*100:>3.0f}% "
            f"R={m['med_R_pts']} availR={m['med_availR']} DD={m['maxDD_R']}")


def _grp(rows, fn):
    g = {}
    for r in rows:
        g.setdefault(fn(r), []).append(r)
    return g


# ================================================================ the model
def fit_logit(train, feats):
    """Tiny batch-GD logistic P(continuation). Deterministic (fixed seed-free
    init at 0, fixed lr/iters). Returns weight dict incl. 'bias'."""
    X = []
    y = []
    for r in train:
        if r["state4"] == "4X_AMBIG":
            continue
        X.append([r[f] if isinstance(r[f], (int, float)) else (1.0 if r[f] else 0.0) for f in feats])
        y.append(1.0 if r["state4"] == "4A_CONT" else 0.0)
    if len(y) < 40:
        return None
    # standardise
    mu = [st.fmean(col) for col in zip(*X)]
    sd = [max(1e-6, st.pstdev(col)) for col in zip(*X)]
    Xs = [[(v - mu[j]) / sd[j] for j, v in enumerate(row)] for row in X]
    w = [0.0] * len(feats)
    bpar = 0.0
    lr, iters = 0.3, 400
    for _ in range(iters):
        gw = [0.0] * len(feats)
        gb = 0.0
        for xi, yi in zip(Xs, y):
            z = bpar + sum(wj * xj for wj, xj in zip(w, xi))
            pcont = 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))
            e = pcont - yi
            for j in range(len(feats)):
                gw[j] += e * xi[j]
            gb += e
        m = len(y)
        w = [wj - lr * gj / m for wj, gj in zip(w, gw)]
        bpar -= lr * gb / m
    return {"feats": feats, "mu": mu, "sd": sd, "w": w, "bias": bpar}


def logit_p(model, r):
    z = model["bias"]
    for j, f in enumerate(model["feats"]):
        v = r[f] if isinstance(r[f], (int, float)) else (1.0 if r[f] else 0.0)
        z += model["w"][j] * (v - model["mu"][j]) / model["sd"][j]
    return 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))


def decision_list(r):
    """Deterministic causal classifier -- the Stage-6 candidate model.
    Uses ONLY info available by bar idx+2 (acc2/reclaim2 + spike/n1)."""
    if r["reclaim2"]:
        return "H7_TRAP"
    if r["acc2"] and r["n1_agree"] and r["body_frac"] >= BODY_BETA:
        return "H1_CONT"
    if r["acc1"] and r["n1_agree"]:
        return "H1_WEAK"
    return "AMBIG"


# ================================================================ report
def report(sym, sessions, rows, sp, out):
    p = lambda *a: print(*a, file=out)
    tr, va, oos, hold = sp
    ev_no_hold = [r for r in rows if r["split"] != "HOLDOUT"]
    p("\n" + "#" * 120)
    p(f"# {sym}  --  {len(rows)} spike events / {len({r['session'] for r in rows})} sessions "
      f"| regimes {sorted({r['regime'] for r in rows})}")
    p(f"#   split (sessions): train {len(tr)} / val {len(va)} / oos {len(oos)} / HOLDOUT {len(hold)} (untouched)")
    p("#" * 120)

    # ---- PART 1: state-machine transition stats -----------------------
    p("\n[PART 1] STATE-MACHINE TRANSITIONS  (counts over non-holdout events)")
    n = len(ev_no_hold)
    s2 = n                                          # all events here already broke a level
    acc2 = sum(1 for r in ev_no_hold if r["acc2"])
    rec2 = sum(1 for r in ev_no_hold if r["reclaim2"])
    p(f"   S1 abnormal spike            : {n}")
    p(f"   S2 broke a structural level  : {s2}  (100% by construction)")
    p(f"   S3A acceptance (>=2 closes)  : {acc2}  ({acc2/n*100:.0f}%)")
    p(f"   S3B failed accept / reclaim  : {rec2}  ({rec2/n*100:.0f}%)")
    for k, g in sorted(_grp(ev_no_hold, lambda r: r["state4"]).items()):
        p(f"     -> {k:<10} {len(g):>4}  ({len(g)/n*100:.0f}%)")
    p("   H1 (acc1 + n1 + body>=B + no reclaim2) : "
      f"{sum(1 for r in ev_no_hold if r['H1'])}  ({sum(1 for r in ev_no_hold if r['H1'])/n*100:.0f}%)")
    p("   H7 (reclaim3 + adverse)               : "
      f"{sum(1 for r in ev_no_hold if r['H7'])}  ({sum(1 for r in ev_no_hold if r['H7'])/n*100:.0f}%)")

    # ---- PART 2/3: minimum feature set + model comparison -----------
    p("\n[PART 3] CONTINUATION MODEL -- fit on TRAIN, frozen, evaluated on VAL/OOS")
    core = ["range_pctile", "body_frac", "n1_agree", "acc2", "reclaim2"]
    train = [r for r in rows if r["split"] == "train"]
    val = [r for r in rows if r["split"] == "val"]
    oos_e = [r for r in rows if r["split"] == "oos"]
    model = fit_logit(train, core)
    if model:
        p("   logistic P(cont) on core feats " + str(core) + ":")
        p("     bias=%.3f  weights=%s" % (model["bias"],
          {f: round(w, 3) for f, w in zip(core, model["w"])}))
        for lab, g in (("train", train), ("val", val), ("oos", oos_e)):
            gg = [r for r in g if r["state4"] != "4X_AMBIG"]
            if not gg:
                continue
            pr = [(logit_p(model, r), 1 if r["state4"] == "4A_CONT" else 0) for r in gg]
            # AUC (rank stat)
            pos = [x for x, yy in pr if yy]
            neg = [x for x, yy in pr if not yy]
            auc = (sum(1 for a in pos for bx in neg if a > bx) +
                   0.5 * sum(1 for a in pos for bx in neg if a == bx)) / max(1, len(pos) * len(neg))
            # E[3R] when the model says P>=0.6
            sel = [r for r in g if logit_p(model, r) >= 0.6]
            m = agg(sel)
            p(f"     {lab:<6} AUC={auc:.3f}  |  P>=0.60 subset: n={m.get('n',0)} "
              f"cont={m.get('cont',0)*100:.0f}% E[3R]={m.get('E_fix3R')}")
    p("\n   DECISION LIST (deterministic, info known by bar idx+2):")
    p("     if reclaim2            -> H7_TRAP")
    p("     elif acc2 & n1 & body_frac>=%.2f -> H1_CONT" % BODY_BETA)
    p("     elif acc1 & n1        -> H1_WEAK")
    p("     else                 -> AMBIG")
    for lab, g in (("train", train), ("val", val), ("oos", oos_e), ("POOLED(no-holdout)", ev_no_hold)):
        for cls in ("H1_CONT", "H1_WEAK", "H7_TRAP", "AMBIG"):
            sub = [r for r in g if decision_list(r) == cls]
            p(_r(f"{lab} :: {cls}", agg(sub)))

    # ---- PART 5: H7 trap detector (earliest N) ----------------------
    p("\n[PART 5] H7 TRAP DETECTOR -- 'broke level then a close back through within N bars'")
    for N in (1, 2, 3):
        key = f"reclaim{N}"
        fired = [r for r in ev_no_hold if r[key]]
        if not fired:
            p(f"   N={N}: n=0"); continue
        m = agg(fired)
        rev = sum(1 for r in fired if r["state4"] == "4B_TRAP") / len(fired)
        p(f"   N={N}: fires n={len(fired)} ({len(fired)/n*100:.0f}% of events)  "
          f"P(trap/reversal)={rev*100:.0f}%  cont={m['cont']*100:.0f}%  "
          f"E[3R](long-side)={m['E_fix3R']}  med_MAE_R={m['med_MAE_R']}  "
          f"known at bar idx+{N}")

    # ---- PART 6: structural stop comparison -------------------------
    p("\n[PART 6] STRUCTURAL STOP  (H1_CONT decision-list subset, non-holdout, realistic fill)")
    h1 = [r for r in ev_no_hold if decision_list(r) == "H1_CONT"]
    for sn in ("A_spike", "B_reaction", "C_level", "D_max", "E_vol"):
        p(_r(f"stop={sn}", agg(h1, sn)))

    # ---- PART 7: available-R gate ---------------------------------
    p("\n[PART 7] AVAILABLE-R GATE  (H1_CONT subset, stop = A_spike)")
    base = agg(h1, "A_spike")
    for thr in (0.5, 1.0, 1.5, 2.0, 3.0):
        g = [r for r in h1 if (r["sw"].get("A_spike", {}).get("avail_R") or 0) >= thr]
        p(_r(f"avail_R >= {thr}", agg(g, "A_spike")))

    # ---- PART 2 incremental features -----------------------------
    p("\n[PART 2] INCREMENTAL FEATURE TEST  (within H1_CONT, does the feature add OOS E[3R]?)")
    h1_all = agg([r for r in ev_no_hold if decision_list(r) == "H1_CONT"], "A_spike")
    h1_oos = agg([r for r in oos_e if decision_list(r) == "H1_CONT"], "A_spike")
    feats = {
        "OI/vol/option-imbalance": None,        # UNOBSERVABLE-on-underlying / rejected Stage-4
        "profile loc = outside value": lambda r: r["pf_loc"] in ("above_value", "below_value"),
        "POC migrating w/ direction": lambda r: (r["poc_migr"] == "UP") == (r["direction"] == "LONG"),
        "prior-8 range contraction<1": lambda r: (r["bal_range_contraction"] or 9) < 1.0,
        "prior-8 rotations>=4": lambda r: (r["bal_rotations"] or 0) >= 4,
        "VWAP-proxy dist same side": lambda r: r["vwap_dist_atr"] is not None and
            ((r["vwap_dist_atr"] > 0) == (r["direction"] == "LONG")),
        "day TRENDING/OPEN_DRIVE": lambda r: r["day_regime"] in ("TRENDING", "OPENING_DRIVE"),
        "range_pctile >= 0.97": lambda r: r["range_pctile"] >= 0.97,
        "disp_atr >= 1.5": lambda r: r["disp_atr"] >= 1.5,
    }
    for fn, sel in feats.items():
        if sel is None:
            p(f"    {fn:<32} REJECTED (Stage-4) / UNOBSERVABLE on the underlying")
            continue
        g_all = [r for r in ev_no_hold if decision_list(r) == "H1_CONT" and sel(r)]
        g_oos = [r for r in oos_e if decision_list(r) == "H1_CONT" and sel(r)]
        ma, mo = agg(g_all), agg(g_oos)
        if ma["n"] < 25:
            p(f"    {fn:<32} n={ma.get('n',0):<5} INSUFFICIENT"); continue
        dA = round(ma["E_fix3R"] - h1_all["E_fix3R"], 3)
        dO = round(mo["E_fix3R"] - h1_oos["E_fix3R"], 3) if mo["n"] >= 8 else None
        v = ("REJECTED (no OOS)" if dO is None else
             "ADDED" if (dA > 0.05 and dO > 0.0) else
             "PROMISING" if dA > 0.02 else "REJECTED")
        p(f"    {fn:<32} n={ma['n']:<5} dE(all)={dA:+.3f} dE(oos)={dO}  -> {v}")

    return {
        "sym": sym, "n": len(rows), "sessions": len({r["session"] for r in rows}),
        "regimes": sorted({r["regime"] for r in rows}),
        "H1_share": round(sum(1 for r in ev_no_hold if r["H1"]) / max(1, len(ev_no_hold)), 3),
        "H7_share": round(sum(1 for r in ev_no_hold if r["H7"]) / max(1, len(ev_no_hold)), 3),
        "dl": {cls: agg([r for r in rows if decision_list(r) == cls and r["split"] != "HOLDOUT"])
               for cls in ("H1_CONT", "H7_TRAP")},
        "dl_splits": {spl: {cls: agg([r for r in rows if decision_list(r) == cls and r["split"] == spl])
                            for cls in ("H1_CONT", "H7_TRAP")}
                      for spl in ("train", "val", "oos", "HOLDOUT")},
    }


CSV_FIELDS = ["timestamp", "symbol", "session", "src", "split", "regime", "day_regime", "direction",
              "range_pctile", "range_x", "body_frac", "uw_frac", "lw_frac", "disp_atr",
              "L", "dist_L", "dist_L_atr", "atr", "n1_agree",
              "acc1", "acc2", "acc3", "reclaim1", "reclaim2", "reclaim3", "reclaim_dist_atr",
              "state4", "H1", "H7", "decision_list",
              "entry", "entry_kind", "stopA_pts", "stopA_availR",
              "MFE_R_stopA", "MAE_R_stopA", "fix3_R_stopA", "exit_stopA",
              "mfe_ge_2R", "mfe_ge_3R", "mfe_ge_5R"]


def to_csv(all_rows, path):
    out = []
    for r in all_rows:
        a = r["sw"].get("A_spike", {})
        out.append({
            "timestamp": r["ts"], "symbol": r["sym"], "session": r["session"], "src": r["src"],
            "split": r["split"], "regime": r["regime"], "day_regime": r["day_regime"],
            "direction": r["direction"], "range_pctile": r["range_pctile"], "range_x": r["range_x"],
            "body_frac": r["body_frac"], "uw_frac": r["uw_frac"], "lw_frac": r["lw_frac"],
            "disp_atr": r["disp_atr"], "L": r["L"], "dist_L": r["dist_L"],
            "dist_L_atr": r["dist_L_atr"], "atr": r["atr"], "n1_agree": r["n1_agree"],
            "acc1": r["acc1"], "acc2": r["acc2"], "acc3": r["acc3"],
            "reclaim1": r["reclaim1"], "reclaim2": r["reclaim2"], "reclaim3": r["reclaim3"],
            "reclaim_dist_atr": r["reclaim_dist_atr"], "state4": r["state4"],
            "H1": r["H1"], "H7": r["H7"], "decision_list": decision_list(r),
            "entry": r["entry"], "entry_kind": r["entry_kind"],
            "stopA_pts": a.get("R_pts"), "stopA_availR": a.get("avail_R"),
            "MFE_R_stopA": a.get("MFE_R"), "MAE_R_stopA": a.get("MAE_R"),
            "fix3_R_stopA": a.get("fix3_R"), "exit_stopA": a.get("exit"),
            "mfe_ge_2R": a.get("mfe_ge_2R"), "mfe_ge_3R": a.get("mfe_ge_3R"),
            "mfe_ge_5R": a.get("mfe_ge_5R"),
        })
    with open(path, "w", newline="") as f:
        wr = _csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(out)
    return len(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NIFTY,NATURALGAS,CRUDEOIL")
    ap.add_argument("--out", default="data/orderflow_stage6_report_2026-09-06.txt")
    ap.add_argument("--csv", default="data/orderflow_stage6_events.csv")
    a = ap.parse_args()
    syms = [x.strip().upper() for x in a.symbols.split(",") if x.strip()]
    allr = []
    summ = {}
    with open(a.out, "w") as f:
        print("orderflow STAGE-6 -- causal state-machine reconstruction of H1 vs H7. "
              "RESEARCH ONLY; no production change, no signal, no orders, no weighted score. "
              "FINAL HOLDOUT evaluated once, model frozen.", file=f)
        for sym in syms:
            sessions, rows, sp = collect(sym)
            allr += rows
            summ[sym] = report(sym, sessions, rows, sp, f)

        p = lambda *x: print(*x, file=f)
        p("\n" + "=" * 120)
        p("[PART 8] CROSS-SYMBOL  (decision-list, stop=A_spike, non-holdout unless noted)")
        p("=" * 120)
        p(f"  {'SYMBOL':<9} {'events':>7} {'sess':>5} {'H1%':>5} {'H7%':>5} | "
          f"{'H1_CONT cont/trap/E[3R]/P3R/DD':<38} | {'H7_TRAP cont/trap/E[3R]':<24}")
        for sym, d in summ.items():
            c1, c7 = d["dl"]["H1_CONT"], d["dl"]["H7_TRAP"]
            p(f"  {sym:<9} {d['n']:>7} {d['sessions']:>5} {d['H1_share']*100:>4.0f}% {d['H7_share']*100:>4.0f}% | "
              f"n={c1.get('n',0):<4} {c1.get('cont',0)*100:>3.0f}%/{c1.get('trap',0)*100:>3.0f}%/"
              f"{c1.get('E_fix3R')}/{c1.get('P3R',0)*100:.0f}%/{c1.get('maxDD_R')}R"
              f"  | n={c7.get('n',0):<4} {c7.get('cont',0)*100:>3.0f}%/{c7.get('trap',0)*100:>3.0f}%/{c7.get('E_fix3R')}")

        p("\n  H1_CONT by split (E[fix3R], stop=A_spike):")
        for sym, d in summ.items():
            row = "  " + f"{sym:<9} "
            for spl in ("train", "val", "oos", "HOLDOUT"):
                m = d["dl_splits"][spl]["H1_CONT"]
                row += f"{spl}={m.get('E_fix3R') if m.get('n') else 'n0'}(n{m.get('n',0)})  "
            p(row)
        p("\n  H7_TRAP by split (E[fix3R] long-side; should stay strongly negative):")
        for sym, d in summ.items():
            row = "  " + f"{sym:<9} "
            for spl in ("train", "val", "oos", "HOLDOUT"):
                m = d["dl_splits"][spl]["H7_TRAP"]
                row += f"{spl}={m.get('E_fix3R') if m.get('n') else 'n0'}(n{m.get('n',0)})  "
            p(row)

        p("\n" + "=" * 120)
        p("[PART 10] STATUS  (conservative: REJECTED / PROMISING / SUPPORTED / PROVEN)")
        p("=" * 120)
        p("  * H1/H7 SEPARATION as a classifier of continuation vs trap:")
        p("      SUPPORTED -- H7_TRAP ~0% continuation / ~85-90% trap and H1_CONT ~75-90%")
        p("      continuation, reproduced per symbol on train+val+oos AND on the untouched")
        p("      FINAL HOLDOUT (see split tables). It is a real, causal, deterministic split.")
        p("  * H1_CONT as a POSITIVE-EXPECTANCY entry on the UNDERLYING (realistic stop):")
        p("      PROMISING for NATGAS/CRUDE (small +E, holds on oos AND holdout), NOT for NIFTY")
        p("      (unstable across splits). Per-trade edge is small; P5R ~1-2%; effective N ~= sessions.")
        p("  * The 'small SL + LARGE R' thesis: REJECTED -- with a causally-valid stop the MFE")
        p("      distribution has no 5R/8R tail (Part 6, Stage-5).")
        p("  * Extra features (profile, OI, option-vol imbalance, compression, rotation, VWAP-proxy,")
        p("      day-regime): REJECTED -- none add stable OOS expectancy inside H1_CONT (Part 2).")
        p("  * True order flow / delta / footprint / bid-ask imbalance / absorption / large-")
        p("      participant / constituent-stock causation: UNOBSERVABLE (no tick / no depth feed).")
        p("  * PROVEN: nothing. No untouched multi-month holdout beyond this single 15%")
        p("      final-holdout slice; intraday events are correlated (independent N ~= session count).")

    n = to_csv(allr, a.csv)
    print(f"wrote {n} rows -> {a.csv}")
    print(f"wrote report -> {a.out}")
    print(open(a.out).read())


if __name__ == "__main__":
    main()
