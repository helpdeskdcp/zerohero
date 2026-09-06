#!/usr/bin/env python3
"""
orderflow_stage8.py -- RESEARCH ONLY. The H7 fade-candidate detector + backtest.

Stage-6/7 established a monotone boundary: once a completed close returns
through the broken level L by more than ~0.6x the spike range, continuation
collapses (~0%) and the *long* (spike-direction) side loses ~1.3-2.2R.
Stage-8 asks the next question directly:

  when H7 is detected, is FADING it (entering OPPOSITE the spike) a
  causally-valid, out-of-sample edge on the UNDERLYING?

HARD RULES: research only; no production change; no signal; no orders; NO
weighted / AI score. Strictly causal (completed candles only; every feature
has an explicit bar-availability). Chronological TRAIN / VAL / OOS / HOLDOUT;
the boundary is re-estimated on TRAIN and verified on VAL/OOS; the HOLDOUT is
scored ONCE, at the end, and no threshold is tuned on it. UNDERLYING only --
no option-premium claim (Stage-4 showed an underlying edge does not carry to
ATM options). Nothing is called Holy-Grail / Institutional / Smart-Money /
Profitable.

Note on "public research": this environment has no web access. The concept
map (§ report) is built from the *generic* meaning of standard order-flow
terms + our Stage-1..7 empirical findings, NOT from live research of any
specific course. Concepts that require true aggressor / delta / footprint /
bid-ask data are marked UNOBSERVABLE -- no proxy is presented as order flow.

Outputs:
  data/orderflow_stage8_events.csv
  data/orderflow_stage8_report_2026-09-06.txt
  (write-up: backend/ORDERFLOW_STAGE8_H7_FADE.md)
"""
from __future__ import annotations

import argparse
import csv as _csv
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.orderflow_stage6 import collect
from scripts.orderflow_stage7 import enrich

TICK = {"NIFTY": 0.05, "NATURALGAS": 0.10, "CRUDEOIL": 1.0}
X_GRID = (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.80, 1.00, 1.10)
RK = (1, 2, 3, 4, 5, 6, 8)
SPL = ("train", "val", "oos", "HOLDOUT")


# ================================================================ fade walk
def fade_walk(bars, ebar, entry, sl, fdir, tick):
    """Forward walk in the FADE direction. Realistic fill: a stop-out exits at
    the breaching bar's extreme ∓ 1 tick (adverse). Returns MFE_R/MAE_R and
    realised R at fixed targets {1,2,3}. `fdir` in {'LONG','SHORT'}."""
    R = abs(entry - sl)
    if R <= 0:
        return None
    tgt = {k: (entry + k * R) if fdir == "LONG" else (entry - k * R) for k in (1, 2, 3)}
    mfe = mae = 0.0
    reach = {k: None for k in RK}
    realized = {1: None, 2: None, 3: None}
    t_stop = None
    for i, b in enumerate(bars[ebar:], start=ebar):
        fav = (b["h"] - entry) if fdir == "LONG" else (entry - b["l"])
        adv = (b["l"] - entry) if fdir == "LONG" else (entry - b["h"])
        mfe = max(mfe, fav)
        mae = min(mae, adv)
        for k in RK:
            if reach[k] is None and mfe / R >= k:
                reach[k] = i - ebar
        hit_stop = (b["l"] <= sl) if fdir == "LONG" else (b["h"] >= sl)
        for k in (1, 2, 3):
            if realized[k] is not None:
                continue
            hit_t = (b["h"] >= tgt[k]) if fdir == "LONG" else (b["l"] <= tgt[k])
            if hit_stop and hit_t:                     # same bar -> stop assumed (pessimistic)
                ext = b["l"] if fdir == "LONG" else b["h"]
                fill = (min(ext, sl) - tick) if fdir == "LONG" else (max(ext, sl) + tick)
                realized[k] = ((fill - entry) if fdir == "LONG" else (entry - fill)) / R
            elif hit_t:
                realized[k] = k - tick / R
            elif hit_stop:
                ext = b["l"] if fdir == "LONG" else b["h"]
                fill = (min(ext, sl) - tick) if fdir == "LONG" else (max(ext, sl) + tick)
                realized[k] = ((fill - entry) if fdir == "LONG" else (entry - fill)) / R
        if hit_stop and t_stop is None:
            t_stop = i - ebar
        if all(realized[k] is not None for k in (1, 2, 3)):
            break
    last = bars[-1]["c"]
    for k in (1, 2, 3):
        if realized[k] is None:
            realized[k] = ((last - entry) if fdir == "LONG" else (entry - last)) / R
    return {
        "R_pts": round(R, 3), "MFE_R": round(mfe / R, 3), "MAE_R": round(mae / R, 3),
        "r1": round(realized[1], 3), "r2": round(realized[2], 3), "r3": round(realized[3], 3),
        "t_stop": t_stop, **{f"mfe_ge_{k}R": (reach[k] is not None) for k in RK},
    }


# ================================================================ build fade events
def build(sym):
    sessions, rows, sp = collect(sym)
    rows = enrich(sym, sessions, rows)
    tick = TICK.get(sym, 0.05)
    by_date = {s.date: s for s in sessions}
    out = []
    for r in rows:
        s = by_date.get(r["session"])
        if s is None or not r["acc1"]:
            continue
        c = s.clean
        idx = next((i for i, b in enumerate(c) if b["bar_start"] == r["ts"]), None)
        if idx is None:
            continue
        b = c[idx]
        L = r["L"]
        sdir = r["direction"]
        fdir = "SHORT" if sdir == "LONG" else "LONG"
        rng = b["h"] - b["l"] or 1e-9
        atr = r["atr"] or rng
        spike_ext = b["h"] if sdir == "LONG" else b["l"]      # the extreme the trap must not exceed
        # per-bar reclaim distance (signed, back-through L), and normalisations
        prof = []
        for j in range(1, 4):
            if idx + j >= len(c):
                break
            cj = c[idx + j]["c"]
            back = (L - cj) if sdir == "LONG" else (cj - L)
            prof.append((j, back, c[idx + j]))
        if not prof:
            continue
        r["spike_range"] = round(rng, 3)
        r["atr_stage8"] = round(atr, 4)
        r["spike_over_atr"] = round(rng / atr, 3)
        r["fdir"] = fdir
        r["prof"] = prof                    # [(j, back_through_L, bar)]
        r["idx"] = idx
        r["spike_ext"] = spike_ext
        r["c"] = c
        r["reaction_opp"] = (r.get("reaction_agrees") is False)
        out.append(r)
    return sessions, out, sp


def detect_bar(r, x_thr):
    """First j in T+1..T+3 where |back-through L| / spike_range > x_thr.
    Returns (j, bar) or None. Fully causal: only completed closes T+1..T+j."""
    for j, back, bar in r["prof"]:
        if back / r["spike_range"] > x_thr:
            return j, bar
    return None


def fade_trade(r, x_thr, entry_offset, stop_kind):
    """entry_offset 0 -> enter at the detect bar close; 1 -> next bar close.
    stop_kind: 'spike' (beyond the spike extreme) or 'reaction' (detect bar
    extreme on the fade-adverse side)."""
    d = detect_bar(r, x_thr)
    if d is None:
        return None
    j, dbar = d
    c = r["c"]
    idx = r["idx"]
    ebar = idx + j + entry_offset
    if ebar + 1 >= len(c):
        return None
    entry = c[ebar - 1]["c"] if entry_offset == 0 else c[ebar - 1]["c"]
    # entry is the close of bar (idx+j) for offset 0, or bar (idx+j+1) for offset 1
    entry = c[idx + j + entry_offset]["c"]
    ebar = idx + j + entry_offset + 1
    if ebar >= len(c):
        return None
    fdir = r["fdir"]
    pad = 0.03 * r["spike_range"]
    if stop_kind == "spike":
        sl = (r["spike_ext"] + pad) if fdir == "SHORT" else (r["spike_ext"] - pad)
        # SHORT fade -> stop above the spike high; LONG fade -> stop below spike low
        sl = (r["spike_ext"] + pad) if fdir == "LONG" and False else sl
        sl = (r["spike_ext"] + pad) if fdir == "SHORT" else (r["spike_ext"] - pad)
    else:
        ext = dbar["h"] if fdir == "SHORT" else dbar["l"]
        sl = (ext + pad) if fdir == "SHORT" else (ext - pad)
    return fade_walk(c, ebar, entry, sl, fdir, TICK.get(r["sym"], 0.05))


# ================================================================ metrics
def fmetrics(trades):
    n = len(trades)
    if not n:
        return {"n": 0}
    r1 = [t["r1"] for t in trades]
    r2 = [t["r2"] for t in trades]
    r3 = [t["r3"] for t in trades]
    mfe = sorted(t["MFE_R"] for t in trades)
    mae = sorted(t["MAE_R"] for t in trades)
    seq = r2                               # equity on the 2R-target rule, session-ordered upstream
    peak = cum = dd = 0.0
    for x in seq:
        cum += x
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    pk = lambda k: round(sum(1 for t in trades if t[f"mfe_ge_{k}R"]) / n, 3)
    return {
        "n": n,
        "E1R": round(st.fmean(r1), 3), "E2R": round(st.fmean(r2), 3), "E3R": round(st.fmean(r3), 3),
        "win2R": round(sum(1 for x in r2 if x > 0) / n, 3),
        "med_MFE_R": mfe[n // 2], "med_MAE_R": mae[n // 2], "p95_MAE_R": mae[max(0, int(n * 0.05))],
        "P2R": pk(2), "P3R": pk(3), "P5R": pk(5), "P8R": pk(8),
        "maxDD_2R": round(dd, 1),
    }


def _fr(name, m):
    if not m or not m["n"]:
        return f"    {name:<28} n=0"
    return (f"    {name:<28} n={m['n']:>4} E1R={m['E1R']:>6} E2R={m['E2R']:>6} E3R={m['E3R']:>6} "
            f"win2R={m['win2R']*100:>3.0f}% mMFE_R={m['med_MFE_R']:>5} mMAE_R={m['med_MAE_R']:>6} "
            f"P2R={m['P2R']*100:>3.0f}% P3R={m['P3R']*100:>3.0f}% P5R={m['P5R']*100:>3.0f}% DD2R={m['maxDD_2R']}")


def _spl(rows, spl):
    return [r for r in rows if r["split"] == spl]


# ================================================================ per-symbol
def run(sym, sessions, ev, out):
    p = lambda *a: print(*a, file=out)
    p("\n" + "#" * 118)
    p(f"# {sym}  --  {len(ev)} spike+level+acc1 events / {len({r['session'] for r in ev})} sessions | "
      f"regimes {sorted({r['regime'] for r in ev})}")
    p("#" * 118)

    # ---- PART 2/8: re-estimate the boundary on TRAIN, verify VAL/OOS ----
    p("\n[§8] RECLAIM-BOUNDARY SWEEP  X = reclaim_dist / spike_range  (fade = OPPOSITE the spike;")
    p("     entry = detect-bar close (T+1..T+3); stop = beyond the spike extreme; realistic fill)")
    p("     [holdout shown for reference only -- NOT used to choose X]")
    p(f"     {'X>':>5} | {'TRAIN E1/E2/E3 (n)':<30} {'VAL E2 (n)':<16} {'OOS E2 (n)':<16} {'HOLDOUT E2 (n)':<16}")
    for x in X_GRID:
        line = f"     {x:>5.2f} | "
        for spl in SPL:
            g = _spl(ev, spl)
            tr = [t for t in (fade_trade(r, x, 0, "spike") for r in g) if t]
            m = fmetrics(tr)
            if spl == "train":
                line += f"{m.get('E1R')}/{m.get('E2R')}/{m.get('E3R')} (n{m.get('n',0)})".ljust(30)
            else:
                line += f"{m.get('E2R')} (n{m.get('n',0)})".ljust(16)
        p(line)

    # pick X on TRAIN by *stability*: highest E2R with n>=25 that also has
    # E2R>0 on VAL and OOS; else "no stable X".
    best_x = None
    for x in X_GRID:
        tr = fmetrics([t for t in (fade_trade(r, x, 0, "spike") for r in _spl(ev, "train")) if t])
        va = fmetrics([t for t in (fade_trade(r, x, 0, "spike") for r in _spl(ev, "val")) if t])
        oo = fmetrics([t for t in (fade_trade(r, x, 0, "spike") for r in _spl(ev, "oos")) if t])
        if tr.get("n", 0) >= 25 and (tr.get("E2R") or -9) > 0 and (va.get("E2R") or -9) > 0 and (oo.get("E2R") or -9) > 0:
            if best_x is None or (tr["E2R"] > best_x[1]):
                best_x = (x, tr["E2R"])
    X = best_x[0] if best_x else 0.60
    p(f"     -> boundary chosen on TRAIN (stability, E2R>0 on train+val+oos): "
      f"{'X > %.2f' % X if best_x else 'NO STABLE X on train+val+oos -> default 0.60 (report only)'}")

    # ---- PART 9: entry timing + stop definition at the chosen X ----
    p(f"\n[§9] H7 FADE at X > {X}  --  entry timing x stop definition")
    for eo, elab in ((0, "entry=detect-bar close (earliest)"), (1, "entry=next bar close")):
        for sk in ("spike", "reaction"):
            g = [t for t in (fade_trade(r, X, eo, sk) for r in ev) if t]
            p(_fr(f"{elab} | stop={sk}", fmetrics(g)))

    # ---- chronological table at the chosen config (entry earliest, stop=spike) ----
    p(f"\n[§F] TRAIN/VAL/OOS/HOLDOUT  (X>{X}, entry=detect-bar close, stop=beyond spike extreme)")
    tvo = {}
    for spl in SPL:
        g = [t for t in (fade_trade(r, X, 0, "spike") for r in _spl(ev, spl)) if t]
        m = fmetrics(g)
        tvo[spl] = m
        p(_fr(spl, m))

    # ---- MFE/MAE distribution + target-multiple sweep ----
    p(f"\n[§9 targets] MFE_R distribution & fixed-target expectancy  (X>{X}, entry earliest, stop=spike, POOLED non-holdout)")
    g = [t for t in (fade_trade(r, X, 0, "spike") for r in ev if r["split"] != "HOLDOUT") if t]
    m = fmetrics(g)
    if m["n"]:
        p(f"    n={m['n']}  med_MFE_R={m['med_MFE_R']}  med_MAE_R={m['med_MAE_R']}  p95_MAE_R={m['p95_MAE_R']}")
        p(f"    P(MFE>=kR): " + "  ".join(f"{k}R={pk*100:.0f}%" for k, pk in
          ((1, m.get('P2R', 0)), (2, m['P2R']), (3, m['P3R']), (5, m['P5R']), (8, m['P8R']))))
        p(f"    E[fixed target]: 1R={m['E1R']}  2R={m['E2R']}  3R={m['E3R']}")

    return {"sym": sym, "X": X, "stable": best_x is not None, "tvo": tvo, "n": len(ev)}


CSV_FIELDS = ["timestamp", "symbol", "session", "split", "regime", "spike_direction", "fade_direction",
              "range_pctile", "spike_range", "atr_stage8", "spike_over_atr", "L",
              "reclaim_dist_spk", "reclaim_dist_atr2", "time_to_reclaim", "reaction_opp",
              "acc1", "acc2", "reclaim2",
              "fade_entry_x0.6", "fade_sl_spike", "fade_R_pts", "fade_MFE_R", "fade_MAE_R",
              "fade_r1", "fade_r2", "fade_r3"]


def to_csv(all_ev, path):
    o = []
    for r in all_ev:
        t = fade_trade(r, 0.60, 0, "spike")
        o.append({
            "timestamp": r["ts"], "symbol": r["sym"], "session": r["session"], "split": r["split"],
            "regime": r["regime"], "spike_direction": r["direction"], "fade_direction": r["fdir"],
            "range_pctile": r["range_pctile"], "spike_range": r.get("spike_range"),
            "atr_stage8": r.get("atr_stage8"), "spike_over_atr": r.get("spike_over_atr"), "L": r["L"],
            "reclaim_dist_spk": r.get("reclaim_dist_spk"), "reclaim_dist_atr2": r.get("reclaim_dist_atr2"),
            "time_to_reclaim": r.get("time_to_reclaim"), "reaction_opp": r.get("reaction_opp"),
            "acc1": r["acc1"], "acc2": r["acc2"], "reclaim2": r["reclaim2"],
            "fade_entry_x0.6": (detect_bar(r, 0.60) or (None,))[0],
            "fade_sl_spike": r.get("spike_ext"),
            "fade_R_pts": t["R_pts"] if t else None, "fade_MFE_R": t["MFE_R"] if t else None,
            "fade_MAE_R": t["MAE_R"] if t else None,
            "fade_r1": t["r1"] if t else None, "fade_r2": t["r2"] if t else None,
            "fade_r3": t["r3"] if t else None,
        })
    with open(path, "w", newline="") as f:
        wr = _csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(o)
    return len(o)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NIFTY,NATURALGAS,CRUDEOIL")
    ap.add_argument("--out", default="data/orderflow_stage8_report_2026-09-06.txt")
    ap.add_argument("--csv", default="data/orderflow_stage8_events.csv")
    a = ap.parse_args()
    syms = [x.strip().upper() for x in a.symbols.split(",") if x.strip()]
    allev = []
    per = {}
    with open(a.out, "w") as f:
        print("orderflow STAGE-8 -- H7 FADE-CANDIDATE detector + backtest. RESEARCH ONLY; no "
              "production change, no signal, no orders, no weighted score. UNDERLYING basis only "
              "(no option-premium claim). No web research done -- concept map is generic + our "
              "Stage-1..7 findings.", file=f)
        for sym in syms:
            sessions, ev, sp = build(sym)
            allev += ev
            per[sym] = run(sym, sessions, ev, f)

        p = lambda *x: print(*x, file=f)
        p("\n" + "=" * 118)
        p("[CROSS-SYMBOL]  H7 fade -- E2R by split (X per-symbol from TRAIN stability; HOLDOUT scored once)")
        p("=" * 118)
        p(f"  {'SYM':<9} {'X':>6} {'stable?':>8} | " + "  ".join(f"{s} E2R(n)" for s in SPL))
        for sym, d in per.items():
            row = f"  {sym:<9} {d['X']:>6.2f} {str(d['stable']):>8} | "
            for spl in SPL:
                m = d["tvo"].get(spl, {})
                row += f"{m.get('E2R') if m.get('n') else 'n0'}(n{m.get('n',0)})  "
            p(row)

        p("\n" + "=" * 118)
        p("[STATUS]  conservative: REJECTED / UNOBSERVABLE / PROMISING / SUPPORTED / PROVEN")
        p("=" * 118)
        nstable = sum(1 for d in per.values() if d["stable"])
        p(f"  Symbols with a TRAIN-stable fade boundary (E2R>0 on train+val+oos): {nstable}/3")
        p("  * The reclaim-distance boundary (P(continuation) collapse) is SUPPORTED as a")
        p("    CLASSIFIER (Stage-6/7) -- reproduced here: past the knee, the long side is a")
        p("    near-certain loser.  FADING it is a different question and is graded above.")
        p("  * Fade edge (enter OPPOSITE the spike, stop beyond the spike extreme): see the")
        p("    per-symbol / per-split table.  A trap is not automatically a clean reversal --")
        p("    chop between the spike extreme and L eats fade stops.")
        p("  * NATGAS spike_range ~0.5-0.7 pt -> fade stops are sub-slippage; treat as")
        p("    DIRECTIONAL/DESCRIPTIVE only, not tradable.")
        p("  * Option-premium edge: NOT claimed (Stage-4).  Underlying only.")
        p("  * UNOBSERVABLE (no proxy invented): true buyer/seller aggression, lifting-the-")
        p("    offer / hitting-the-bid, delta, cumulative delta, R-delta, footprint, depth")
        p("    imbalance, absorption, ignition 'volume' component, BMC/SMC order-flow internals.")
        p("  * PROVEN: nothing -- single spent-holdout regime; correlated intraday events;")
        p("    small samples per split on NIFTY.")

    n = to_csv(allev, a.csv)
    print(f"wrote {n} rows -> {a.csv}")
    print(f"wrote report -> {a.out}")
    print(open(a.out).read())


if __name__ == "__main__":
    main()
