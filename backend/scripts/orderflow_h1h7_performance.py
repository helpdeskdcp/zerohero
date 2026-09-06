#!/usr/bin/env python3
"""
orderflow_h1h7_performance.py -- RESEARCH / EVALUATION ONLY.

Historical out-of-sample performance evaluation of the deployed H1/H7
Structural State Engine (app/orderflow/h1h7_state.py). Does NOT touch
production, broker execution, live_trading, paper_mode, order logic, or the
shadow engine. Adds no BUY/SELL/order/entry_signal logic -- it forward-walks
the UNDERLYING / FUTURES bars (completed candles only, zero look-ahead) to score
each already-classified structural event, then aggregates per state.

Method
------
* Event universe: every eligible bar the engine classifies over the captured
  history it can actually see (`market_hub` -> the zerohero histcap sessions).
* Reference geometry (frozen, = Stage-6 "A_spike" / the geometry the engine's
  own available_R uses): entry = spike close, stop = spike extreme -/+ 3% of the
  spike range, R = |entry - stop|. This is a SCORING reference, not an order.
* Walk: realistic stop fill -- exit at the breaching bar's extreme -/+ 1 tick,
  so a loss can exceed -1R. Track MFE_R, MAE_R, whether +1R/+2R/+3R was reached,
  whether the stop was hit before +1R, and the realised R at a fixed 3R target.
* Direction = the spike direction for EVERY state, uniformly, so the states are
  comparable. For H1_CONT that is the continuation trade; for H7_* it is the
  "buy the break" outcome the AVOID label tells you to skip (its badness is the
  avoidance-quality metric -- fading H7 is REJECTED, Stage-8, and is not walked).
* Baseline = the frozen Stage-6 decision list on the SAME events:
      if reclaim2 -> H7_TRAP
      elif acc2 and n1_agree and body_frac >= 0.55 -> H1_CONT
      elif acc1 and n1_agree -> H1_WEAK
      else -> AMBIG
  i.e. the pre-refinement labeller (reclaim2 binary + body_frac gate) vs the new
  engine (reclaim-distance bands + disp_atr gate).
* Chronological split by session date where it exists.

Outputs
-------
  backend/data/orderflow_h1h7_performance.csv   (one row per eligible event)
  backend/ORDERFLOW_H1H7_PERFORMANCE.md         (the report -- written by hand
                                                 from this script's stdout)
"""
from __future__ import annotations

import argparse
import csv as _csv
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import market_hub
from app.orderflow import h1h7_state as H
from scripts import orderflow_histsrc as HS

RK = (1, 2, 3)
TICK = {"NIFTY": 0.05, "NATURALGAS": 0.10, "CRUDEOIL": 1.0}
SYMBOLS = ["CRUDEOIL", "NIFTY", "NATURALGAS"]

# chronological split fractions by session-date rank (per symbol) -- matches the
# frozen Stage-6 split (FR_TRAIN=0.45, FR_VAL=0.65, FR_OOS=0.82).
FR_TRAIN, FR_VAL, FR_OOS = 0.45, 0.65, 0.82

# market_hub mode has only 3 usable sessions -> a fixed date map
MH_SPLIT = {"2026-09-02": "TRAIN", "2026-09-03": "VALIDATION", "2026-09-04": "HOLDOUT"}


def _regime(clean):
    """Session trend/chop label (verbatim from Stage-3 _regime)."""
    if len(clean) < 6:
        return "NA"
    o0 = clean[0].get("o")
    net = clean[-1]["c"] - (o0 if o0 is not None else clean[0]["c"])
    rng = max(b["h"] for b in clean) - min(b["l"] for b in clean)
    if rng <= 0:
        return "NA"
    if abs(net) >= 0.5 * rng:
        return "TREND_UP" if net > 0 else "TREND_DOWN"
    return "CHOP"


def _sessions_for(source):
    """Yield (sym, date, src, split_tag) chronologically per symbol."""
    for sym in SYMBOLS:
        if source == "market_hub":
            ds = sorted(market_hub.session_dates(sym, limit=120))
            for d in ds:
                yield sym, d, "zerohero", MH_SPLIT.get(d, "OTHER")
        else:  # histsrc -- the full multi-source history (~36-39 sessions/symbol)
            ss = HS.sessions(sym)                    # already chronological
            n = len(ss)
            a, b, c = int(n * FR_TRAIN), int(n * FR_VAL), int(n * FR_OOS)
            for i, (d, src) in enumerate(ss):
                tag = ("TRAIN" if i < a else "VALIDATION" if i < b
                       else "OOS" if i < c else "HOLDOUT")
                yield sym, d, src, tag


def _bars_for(source, sym, d, src):
    if source == "market_hub":
        return market_hub.session_bars(sym, d)
    return HS.session_bars(sym, d, src)


# ---------------------------------------------------------------- realistic walk
def _walk(clean, ebar, entry, sl, direction, tick):
    R = abs(entry - sl)
    if R <= 0 or ebar >= len(clean):
        return None
    t3 = entry + 3 * R if direction == "LONG" else entry - 3 * R
    mfe = mae = 0.0
    reach = {k: False for k in RK}
    realized = None
    exitk = None
    reached_1R_before_bar = False
    for b in clean[ebar:]:
        pre_mfe = mfe
        fav = (b["h"] - entry) if direction == "LONG" else (entry - b["l"])
        adv = (b["l"] - entry) if direction == "LONG" else (entry - b["h"])
        mfe = max(mfe, fav)
        mae = min(mae, adv)
        for k in RK:
            if mfe / R >= k:
                reach[k] = True
        hit_stop = (b["l"] <= sl) if direction == "LONG" else (b["h"] >= sl)
        hit_t3 = (b["h"] >= t3) if direction == "LONG" else (b["l"] <= t3)
        if hit_stop:
            ext = b["l"] if direction == "LONG" else b["h"]
            fill = (min(ext, sl) - tick) if direction == "LONG" else (max(ext, sl) + tick)
            realized = ((fill - entry) if direction == "LONG" else (entry - fill)) / R
            exitk = "SL"
            reached_1R_before_bar = (pre_mfe / R) >= 1.0
            break
        if hit_t3:
            realized = 3.0 - tick / R
            exitk = "T3"
            break
        reached_1R_before_bar = (mfe / R) >= 1.0
    if realized is None:
        last = clean[-1]["c"]
        realized = ((last - entry) if direction == "LONG" else (entry - last)) / R
        exitk = "EOD"
    return {
        "R_pts": round(R, 4), "MFE_R": round(mfe / R, 4), "MAE_R": round(mae / R, 4),
        "fix3_R": round(realized, 4), "exit": exitk,
        "reached_1R": reach[1], "reached_2R": reach[2], "reached_3R": reach[3],
        "sl_first": bool(exitk == "SL" and not reached_1R_before_bar),
    }


def _decision_list(reclaim2, acc2, n1_agree, body_frac, acc1):
    """Frozen Stage-6 baseline labeller (pre Stage-7 refinement)."""
    if reclaim2:
        return "H7_TRAP"
    if acc2 and n1_agree and body_frac is not None and body_frac >= 0.55:
        return "H1_CONT"
    if acc1 and n1_agree:
        return "H1_WEAK"
    return "AMBIG"


# ---------------------------------------------------------------- build events
def collect(source="histsrc"):
    rows = []
    for sym, d, src, split_tag in _sessions_for(source):
        tick = TICK[sym]
        bars = _bars_for(source, sym, d, src)
        clean = H._clean(bars)
        n = len(clean)
        if n < H.START_IDX + H.FWD_WINDOW + 1:
            continue
        regime = _regime(clean)
        base = H._running_base(clean)
        H._FRAC_LO, H._FRAC_HI = H._fractals(clean)
        va = H._va_series(clean)
        for idx in range(H.START_IDX, n - H.FWD_WINDOW):
            sp = H.detect_abnormal_spike(clean, base, idx)
            if not sp["is_spike"]:
                continue
            b = clean[idx]
            direction = sp["direction"]
            rng = H.calculate_spike_range(b)
            atr = H._atr(clean, idx) or base[idx]
            disp_atr = (abs(b["c"] - b["o"]) / atr) if atr else None
            body_frac = (abs(b["c"] - b["o"]) / rng) if rng > 0 else None
            lv = H._levels(clean, idx, va[idx])
            L, _ = H._broken_level(clean, idx, direction, lv)
            ctx = {
                "symbol": sym, "timestamp": b["bar_start"], "is_spike": True,
                "direction": direction, "spike_range": round(rng, 4),
                "range_pctile": sp["range_pctile"], "range_x": sp["range_x"],
                "atr": round(atr, 4) if atr else None,
                "disp_atr": round(disp_atr, 4) if disp_atr is not None else None,
                "body_fraction": round(body_frac, 4) if body_frac is not None else None,
                "broken_level": (round(L, 4) if L is not None else None),
                "broken_level_kind": (H._broken_level_kind(lv, L, direction) if L is not None else None),
            }
            reclaim2 = acc1 = acc2 = None
            if L is not None:
                dist_L = (b["c"] - L) if direction == "LONG" else (L - b["c"])
                acc = H._acceptance(clean, idx, direction, L, dist_L)
                acc1, acc2 = acc[1], acc[2]
                rc = H.calculate_reclaim_distance(clean, idx, direction, L, rng)
                reclaim2 = H._reclaimed_by(clean, idx, direction, L, 2) is not None
                pad = 0.03 * rng
                spike_ext = b["l"] if direction == "LONG" else b["h"]
                stop = (spike_ext - pad) if direction == "LONG" else (spike_ext + pad)
                R_pts = abs(b["c"] - stop)
                ctx.update(
                    acc1=acc1, acc2=acc2,
                    n1_agreement=H.detect_n1_agreement(clean, idx, direction, L),
                    reclaim_distance=rc["reclaim_distance"],
                    reclaim_distance_ratio=rc["reclaim_distance_ratio"],
                    reclaimed_within_3=rc["reclaimed_within_3"],
                    available_R=H.calculate_available_R(lv, b["c"], R_pts, direction),
                )
            else:
                stop = None
                R_pts = None

            ev = H.classify_market_state(ctx)
            base_state = _decision_list(bool(reclaim2), bool(acc2),
                                        bool(ctx.get("n1_agreement")),
                                        ctx.get("body_fraction"), bool(acc1))

            w = None
            if stop is not None:
                w = _walk(clean, idx + 1, b["c"], stop, direction, tick)
            rows.append({
                "timestamp": b["bar_start"], "symbol": sym, "session": d,
                "src": src, "regime": regime, "split": split_tag,
                "new_state": ev["state"], "new_action": ev["action"],
                "new_research_status": ev["research_status"],
                "baseline_state": base_state,
                "spike_direction": direction,
                "entry_ref": round(b["c"], 4),
                "structural_level": ctx["broken_level"],
                "sl_price": round(stop, 4) if stop is not None else None,
                "risk_points": round(R_pts, 4) if R_pts is not None else None,
                "available_R": ctx.get("available_R"),
                "n1_agreement": ctx.get("n1_agreement"),
                "disp_atr": ctx.get("disp_atr"),
                "body_fraction": ctx.get("body_fraction"),
                "reclaim_distance_ratio": ctx.get("reclaim_distance_ratio"),
                "reclaimed_within_3": ctx.get("reclaimed_within_3"),
                "acc1": acc1, "acc2": acc2, "reclaim2": reclaim2,
                "MFE_R": w["MFE_R"] if w else None,
                "MAE_R": w["MAE_R"] if w else None,
                "reached_1R": w["reached_1R"] if w else None,
                "reached_2R": w["reached_2R"] if w else None,
                "reached_3R": w["reached_3R"] if w else None,
                "sl_first": w["sl_first"] if w else None,
                "fix3_R": w["fix3_R"] if w else None,
                "exit": w["exit"] if w else None,
            })
    return rows


# ---------------------------------------------------------------- aggregation
def agg(rows):
    w = [r for r in rows if r["fix3_R"] is not None]
    n = len(w)
    if not n:
        return {"n": 0}
    fx = [r["fix3_R"] for r in w]
    mfe = [r["MFE_R"] for r in w]
    mae = [r["MAE_R"] for r in w]
    wins = [x for x in fx if x > 0]
    losses = [x for x in fx if x < 0]
    gp, gl = sum(wins), -sum(losses)
    # max consecutive losses, chronological
    mcl = cur = 0
    for r in sorted(w, key=lambda r: r["timestamp"]):
        if r["fix3_R"] < 0:
            cur += 1
            mcl = max(mcl, cur)
        elif r["fix3_R"] > 0:
            cur = 0
    # net R + max drawdown on the running equity (chronological)
    cum = peak = dd = 0.0
    for r in sorted(w, key=lambda r: r["timestamp"]):
        cum += r["fix3_R"]
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return {
        "n": n,
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n, 4),
        "avg_R": round(st.fmean(fx), 4),
        "expectancy": round(st.fmean(fx), 4),
        "profit_factor": (round(gp / gl, 3) if gl > 0 else None),
        "max_consec_losses": mcl,
        "net_R": round(sum(fx), 3),
        "max_DD_R": round(dd, 3),
        "MFE_R_med": round(st.median(mfe), 3), "MFE_R_mean": round(st.fmean(mfe), 3),
        "MAE_R_med": round(st.median(mae), 3), "MAE_R_mean": round(st.fmean(mae), 3),
        "p_reach_1R": round(sum(1 for r in w if r["reached_1R"]) / n, 3),
        "p_reach_2R": round(sum(1 for r in w if r["reached_2R"]) / n, 3),
        "p_reach_3R": round(sum(1 for r in w if r["reached_3R"]) / n, 3),
        "p_sl_first": round(sum(1 for r in w if r["sl_first"]) / n, 3),
        "sessions": len({r["session"] for r in w}),
        "regimes": sorted({r.get("regime") for r in w if r.get("regime")}),
    }


def _fmt(name, m):
    if not m or not m["n"]:
        return f"  {name:<26} n=0"
    return (f"  {name:<26} n={m['n']:>4} ses={m['sessions']} "
            f"W/L={m['wins']}/{m['losses']} win%={m['win_rate']*100:>5.1f} "
            f"E[R]={m['expectancy']:>7.3f} PF={m['profit_factor']} "
            f"netR={m['net_R']:>7.2f} maxDD={m['max_DD_R']:>7.2f} "
            f"maxCL={m['max_consec_losses']} "
            f"MFE~{m['MFE_R_med']} MAE~{m['MAE_R_med']} "
            f"P1R={m['p_reach_1R']} P3R={m['p_reach_3R']} PslFirst={m['p_sl_first']}")


NEW_STATES = ["H1_CONT", "H1_CONT_OBSERVE", "H1_CONT_BLOCKED", "H1_WEAK",
              "H7_TRAP", "H7_LEANING_TRAP", "AMBIGUOUS", "SPIKE_NO_LEVEL"]


SPL_ORDER = ("TRAIN", "VALIDATION", "OOS", "HOLDOUT", "OTHER")


def report(rows, out, source):
    p = lambda *a: print(*a, file=out)
    p("=" * 104)
    p("H1/H7 STRUCTURAL STATE ENGINE -- HISTORICAL OUT-OF-SAMPLE PERFORMANCE EVALUATION")
    p(f"source = {source}   RESEARCH / EVALUATION ONLY. No production / broker / live_trading / order change.")
    p("UNDERLYING / FUTURES structural walk only. Option-premium profitability is NOT inferred.")
    p("'win' = realised R at a fixed 3R target with realistic stop fill > 0. Direction = spike")
    p("direction for every state (for H7_* that is the 'buy the break' outcome AVOID tells you")
    p("to skip; fading H7 is REJECTED, Stage-8, and is not walked). Baseline = frozen Stage-6")
    p("decision list on the SAME events.")
    if source == "histsrc":
        p("NB: histsrc mixes zerohero histcap (4 sessions) with oi_dashboard cycles_resampled /")
        p("live_candles (Jul-Aug). cycles_resampled highs/lows are slightly understated (Stage-3")
        p("QC). live_candles carry no volume -- irrelevant, the engine is volume-free. This is the")
        p("SAME dataset the frozen Stage-3..8 research used; its 18% final holdout was already")
        p("scored once by Stage-6, so a positive holdout here is CONFIRMATORY, not fresh OOS.")
    p("=" * 104)

    sess = sorted({(r["symbol"], r["session"]) for r in rows})
    p(f"\nDATA: {len(rows)} eligible events | {len({r['session'] for r in rows})} distinct dates | "
      f"{len(sess)} symbol-sessions")
    for sym in SYMBOLS:
        rr = [r for r in rows if r["symbol"] == sym]
        ds = sorted({r["session"] for r in rr})
        regs = {}
        for r in rr:
            regs[r["regime"]] = regs.get(r["regime"], 0) + 1
        p(f"   {sym:<11} {len(ds)} sessions ({ds[0] if ds else '-'}..{ds[-1] if ds else '-'})  "
          f"events {len(rr)}  regimes {regs}")

    p("\n" + "-" * 104)
    p("[1] NEW ENGINE -- per state (all symbols pooled)")
    p("-" * 104)
    for s in NEW_STATES:
        p(_fmt(s, agg([r for r in rows if r["new_state"] == s])))

    p("\n[1b] NEW ENGINE -- derived buckets")
    p(_fmt("H7_SUPPORTED", agg([r for r in rows if r["new_research_status"] == "SUPPORTED"])))
    p(_fmt("H7_AVOID (action=AVOID)", agg([r for r in rows if r["new_action"] == "AVOID"])))
    p(_fmt("NO_ACTION (action)", agg([r for r in rows if r["new_action"] == "NO_ACTION"])))
    p(_fmt("CONTINUATION_CANDIDATE", agg([r for r in rows if r["new_action"] == "CONTINUATION_CANDIDATE"])))

    p("\n[1c] NEW ENGINE -- H1_CONT by symbol  (the only 'actionable' state)")
    for sym in SYMBOLS:
        p(_fmt(f"H1_CONT {sym}", agg([r for r in rows if r["new_state"] == "H1_CONT" and r["symbol"] == sym])))

    p("\n[1d] NEW ENGINE -- by REGIME")
    for reg in ("TREND_UP", "TREND_DOWN", "CHOP"):
        p(_fmt(f"H1_CONT {reg}", agg([r for r in rows if r["new_state"] == "H1_CONT" and r["regime"] == reg])))
    for reg in ("TREND_UP", "TREND_DOWN", "CHOP"):
        p(_fmt(f"H7_SUPPORTED {reg}", agg([r for r in rows
                if r["new_research_status"] == "SUPPORTED" and r["regime"] == reg])))

    p("\n" + "-" * 104)
    p("[2] BASELINE (frozen Stage-6 decision list) -- per state, SAME events")
    p("-" * 104)
    for s in ("H1_CONT", "H1_WEAK", "H7_TRAP", "AMBIG"):
        p(_fmt(f"baseline {s}", agg([r for r in rows if r["baseline_state"] == s])))

    p("\n" + "-" * 104)
    p("[3] BASELINE vs NEW ENGINE  (same historical period, same events)")
    p("-" * 104)
    cmp_out = {}
    for label, key, new_sel, base_sel in (
        ("H1_CONT (actionable)", "h1_cont", lambda r: r["new_state"] == "H1_CONT",
         lambda r: r["baseline_state"] == "H1_CONT"),
        ("H7_TRAP (avoidance)", "h7_trap", lambda r: r["new_state"] == "H7_TRAP",
         lambda r: r["baseline_state"] == "H7_TRAP"),
    ):
        mn, mb = agg([r for r in rows if new_sel(r)]), agg([r for r in rows if base_sel(r)])
        cmp_out[key] = {"new": mn, "baseline": mb}
        p(f"\n  {label}")
        p(f"    {'':10} {'signals':>8} {'win%':>7} {'E[R]':>8} {'PF':>7} {'netR':>9} {'maxDD':>9} {'maxCL':>6}")
        for nm, m in (("BASELINE", mb), ("NEW", mn)):
            if not m["n"]:
                p(f"    {nm:<10} {'0':>8}")
                continue
            p(f"    {nm:<10} {m['n']:>8} {m['win_rate']*100:>6.1f}% {m['expectancy']:>8.3f} "
              f"{str(m['profit_factor']):>7} {m['net_R']:>9.2f} {m['max_DD_R']:>9.2f} {m['max_consec_losses']:>6}")

    p("\n" + "-" * 104)
    p("[4] CHRONOLOGICAL SPLIT  (per-symbol date rank: TRAIN 0-45% / VALIDATION 45-65% / OOS 65-82% / HOLDOUT 82-100%)")
    p("-" * 104)
    for spl in ("TRAIN", "VALIDATION", "OOS", "HOLDOUT"):
        nse = len({(r["symbol"], r["session"]) for r in rows if r["split"] == spl})
        p(f"  {spl:<11} symbol-sessions={nse}")
    for s in ("H1_CONT", "H7_TRAP"):
        p(f"  new {s}:")
        for spl in ("TRAIN", "VALIDATION", "OOS", "HOLDOUT"):
            p("  " + _fmt(f"  {spl}", agg([r for r in rows if r["new_state"] == s and r["split"] == spl])))
    p("  baseline H1_CONT:")
    for spl in ("TRAIN", "VALIDATION", "OOS", "HOLDOUT"):
        p("  " + _fmt(f"  {spl}", agg([r for r in rows if r["baseline_state"] == "H1_CONT" and r["split"] == spl])))

    p("\n" + "-" * 104)
    p("[5] OPTION-PREMIUM PROFITABILITY")
    p("-" * 104)
    nh1 = sum(1 for r in rows if r["new_state"] == "H1_CONT")
    p("  Real historical option data: ATM premium ticks (zerohero, ~25-30s poll, 4 sessions) +")
    p("  oi_dashboard per-strike CE/PE LTP+OI (~9-20s, Jul-Aug). Stage-4 already re-walked the")
    p("  H1/H7 events on the captured ATM premium and found the index edge does NOT survive the")
    p("  option spread + theta. This evaluation does NOT re-derive an option win rate on top of")
    p(f"  that (it would not change the Stage-4 conclusion, and per-state option N is thin: {nh1} H1_CONT).")
    p("  ==> OPTION-PREMIUM PROFITABILITY: NOT VALIDATED / edge does not survive ATM premium (Stage-4).")
    p("      UNDERLYING / FUTURES structural results are the ones reported above.")

    # ---- verdict against pre-declared criteria ----
    p("\n" + "=" * 104)
    p("[6] VERDICT -- against the pre-declared validation criteria")
    p("=" * 104)
    h1 = agg([r for r in rows if r["new_state"] == "H1_CONT"])
    h1_oos = agg([r for r in rows if r["new_state"] == "H1_CONT" and r["split"] == "OOS"])
    h1_hold = agg([r for r in rows if r["new_state"] == "H1_CONT" and r["split"] == "HOLDOUT"])
    h1_sessions = h1.get("sessions", 0)
    h1_n = h1.get("n", 0)
    h1_regimes = h1.get("regimes", [])
    crit = {
        ">=10 sessions with an H1_CONT event": h1_sessions >= 10,
        ">=50 H1_CONT events": h1_n >= 50,
        ">=2 regimes represented": len(h1_regimes) >= 2,
        "OOS slice >=20 events": h1_oos.get("n", 0) >= 20,
        "OOS E[R] > 0": (h1_oos.get("expectancy") or -9) > 0,
        "HOLDOUT slice >=20 events": h1_hold.get("n", 0) >= 20,
        "HOLDOUT E[R] > 0": (h1_hold.get("expectancy") or -9) > 0,
        "HOLDOUT is FRESH (sessions after the frozen Stage-6 holdout)": False,
    }
    for k, v in crit.items():
        p(f"   [{'PASS' if v else 'FAIL'}] {k}")
    p(f"   (H1_CONT: n={h1_n}, sessions={h1_sessions}, regimes={h1_regimes}; "
      f"OOS n={h1_oos.get('n',0)} E[R]={h1_oos.get('expectancy')}; "
      f"HOLDOUT n={h1_hold.get('n',0)} E[R]={h1_hold.get('expectancy')} -- same spent slice as Stage-6)")
    all_pass = all(crit.values())
    sample_ok = h1_sessions >= 10 and h1_n >= 50 and len(h1_regimes) >= 2
    signs_ok = ((h1_oos.get("expectancy") or -9) > 0 and (h1_hold.get("expectancy") or -9) > 0)
    p("")
    if sample_ok and signs_ok:
        p("  ==> NOT VALIDATED. The sample/regime/session criteria PASS and every chronological")
        p("      split's H1_CONT E[R] is positive, BUT: (a) the HOLDOUT slice is n<20 and was")
        p("      already scored once by the frozen Stage-6 model (no sessions after 2026-09-04)")
        p("      -> not fresh out-of-sample; (b) the H1_CONT edge is concentrated in CHOP regime")
        p("      (see [1d]); (c) one broker's polled bars, intraday-correlated. Highest honest")
        p("      status stays PROMISING -- nothing is PROVEN.")
    else:
        p("  ==> NOT VALIDATED. The pre-declared sample bar is not met on this data")
        p(f"      (H1_CONT n={h1_n}, sessions={h1_sessions}, regimes={len(h1_regimes)}).")
        p("      Nothing is PROVEN.")
    p("\n  Frozen research ceiling (unchanged): H7 reclaim-distance boundary = SUPPORTED as an")
    p("  AVOIDANCE classifier (fading it REJECTED); H1_CONT on the underlying = PROMISING,")
    p("  CRUDEOIL-only, small; option-premium profitability = does not survive ATM premium.")
    p("  PROVEN: nothing.")
    return {"h1_cont": h1, "h1_oos": h1_oos, "h1_hold": h1_hold,
            "criteria": crit, "all_pass": all_pass, "cmp": cmp_out,
            "h7_supported": agg([r for r in rows if r["new_research_status"] == "SUPPORTED"]),
            "no_action": agg([r for r in rows if r["new_action"] == "NO_ACTION"])}


CSV_FIELDS = ["timestamp", "symbol", "session", "src", "regime", "split", "new_state",
              "new_action", "new_research_status", "baseline_state", "spike_direction",
              "entry_ref", "structural_level", "sl_price", "risk_points", "available_R",
              "n1_agreement", "disp_atr", "body_fraction", "reclaim_distance_ratio",
              "reclaimed_within_3", "acc1", "acc2", "reclaim2", "MFE_R", "MAE_R",
              "reached_1R", "reached_2R", "reached_3R", "sl_first", "fix3_R", "exit"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("histsrc", "market_hub"), default="histsrc",
                    help="histsrc = full multi-source history (~36-39 sessions/symbol, 3 regimes); "
                         "market_hub = the 4 zerohero histcap sessions only")
    ap.add_argument("--csv", default=None)
    a = ap.parse_args()
    rows = collect(a.source)
    out_csv = Path(a.csv) if a.csv else (
        Path(__file__).resolve().parents[1] / "data" / "orderflow_h1h7_performance.csv")
    with out_csv.open("w", newline="") as f:
        wr = _csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)
    report(rows, sys.stdout, a.source)
    print(f"\nwrote {len(rows)} rows -> {out_csv}")


if __name__ == "__main__":
    main()
