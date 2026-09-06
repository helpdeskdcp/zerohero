#!/usr/bin/env python3
"""
orderflow_l2_imbalance_probe.py -- RESEARCH-FIRST DESIGN + FEASIBILITY PROBE.
READ-ONLY. NO production wiring, NO live-order change, NO engine / H1-H7 /
trading / execution / broker / risk / test change. Reads market_history.db in
read-only mode and prints. Writes at most one CSV under data/ (git-ignored).

GOAL (from the brief): find out whether a genuine "200% L2 order-flow imbalance"
is a statistically useful CONFIRMATION FILTER that improves target-hit
probability while reducing adverse movement -- WITHOUT assuming it reduces SLs.

GATE AS SPECIFIED
  BUY  imbalance = bid-side / aggressive-buy pressure  ÷  ask-side / aggressive-sell pressure  >= 2.0
  SELL imbalance = ask-side / aggressive-sell pressure  ÷  bid-side / aggressive-buy pressure  >= 2.0

WHAT IS GENUINELY AVAILABLE (see also backend/ORDERFLOW_STAGE9_L2_RESEARCH.md):
  * quote_snapshots (source=ANGELONE_QUOTE_FULL): L1 bid/ask + bid_qty/ask_qty,
    tot_buy_qty/tot_sell_qty (day-cumulative pending), depth_json (5-level book:
    price/quantity/orders). Poll cadence ~25-30 s -- SNAPSHOTS, not an update
    stream. Persisted only for a handful of sessions in the first week of
    2026-09 for NIFTY / CRUDEOIL / NATURALGAS futures.
  * These are RESTING / PASSIVE limit-order liquidity.

WHAT IS NOT AVAILABLE (and is NEVER synthesised here):
  * aggressor-classified trades (which side lifted the offer / hit the bid),
    trade delta, cumulative delta, aggressive-buy vs aggressive-sell VOLUME.
    There is no tick feed and no per-trade side anywhere -> the "aggressive-buy
    pressure / aggressive-sell pressure" half of the gate is UNOBSERVABLE.
  * an order-book add/cancel/modify event stream (only ~30 s snapshots exist).
  * depth beyond 5 levels; any L1/L2 history before 2026-09-01.

CONSEQUENCE
  * The gate AS SPECIFIED (aggressor pressure) CANNOT be implemented -- the data
    does not exist and synthesising it is prohibited.
  * A PASSIVE-BOOK depth-imbalance variant (bid_qty/ask_qty, summed 5-level
    depth, tot_buy/tot_sell) CAN be computed from genuine data. This script
    implements it as a read-only research component and runs the event study
    that COULD validate it -- but on the current ~3 usable futures sessions,
    one regime, snapshot cadence, that study is under-powered. The script
    reports INSUFFICIENT DATA and does NOT claim any improvement.

Nothing here is ever labelled PROVEN. A positive number on 3 correlated
same-week sessions is not evidence.
"""
from __future__ import annotations

import argparse
import csv as _csv
import json
import sqlite3
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

HDB = Path(__file__).resolve().parents[1] / "data" / "market_history.db"
OUT = Path(__file__).resolve().parents[1] / "data" / "orderflow_l2_imbalance_probe.csv"

SYMBOLS = ("NIFTY", "CRUDEOIL", "NATURALGAS")
THRESHOLDS = (1.5, 2.0, 2.5, 3.0)          # 200% = 2.0
PERSIST = (1, 2, 3, 4)                     # consecutive same-sign snapshots
MEASURES = ("imb_L1", "imb_D5", "imb_BOOK")
FWD_MIN = (2, 5, 10)                       # forward-return horizons (minutes)
RR_TARGET, RR_STOP = 3.0, 1.0             # reference geometry, R = L1 spread at entry


# ---------------------------------------------------------------- load
def _ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def load(con, sym):
    """Genuine L2 snapshots for one FUTURE symbol, oldest first, grouped by
    session date. Only rows with a sane L1 book are kept."""
    rows = con.execute(
        "SELECT session_date_ist, received_ts, exch_ts, ltp, bid, ask, bid_qty, ask_qty, "
        "tot_buy_qty, tot_sell_qty, depth_json FROM quote_snapshots "
        "WHERE kind='FUTURE' AND symbol=? ORDER BY received_ts", (sym,)).fetchall()
    by_day: dict = {}
    for (d, rts, ets, ltp, bid, ask, bq, aq, tbq, tsq, dj) in rows:
        if None in (ltp, bid, ask) or bid <= 0 or ask <= 0 or bid > ask:
            continue
        t = _ts(ets) or _ts(rts)
        if t is None:
            continue
        sb5 = ss5 = None
        if dj and dj not in ("", "null", "{}"):
            try:
                d5 = json.loads(dj)
                sb5 = sum(l.get("quantity") or 0 for l in d5.get("buy") or [] if (l.get("price") or 0) > 0)
                ss5 = sum(l.get("quantity") or 0 for l in d5.get("sell") or [] if (l.get("price") or 0) > 0)
            except (ValueError, TypeError):
                sb5 = ss5 = None
        by_day.setdefault(d, []).append({
            "t": t, "ltp": float(ltp), "bid": float(bid), "ask": float(ask),
            "spread": float(ask) - float(bid),
            "bid_qty": float(bq) if bq else None, "ask_qty": float(aq) if aq else None,
            "tbq": float(tbq) if tbq else None, "tsq": float(tsq) if tsq else None,
            "sb5": float(sb5) if sb5 else None, "ss5": float(ss5) if ss5 else None,
        })
    # de-dup identical seconds, keep last
    for d, snaps in by_day.items():
        seen, out = set(), []
        for s in snaps:
            k = s["t"].replace(microsecond=0)
            if k in seen:
                out[-1] = s
            else:
                seen.add(k)
                out.append(s)
        by_day[d] = out
    return by_day


# ---------------------------------------------------------------- imbalance (PASSIVE book only)
def _ratio(snap, measure):
    """PASSIVE resting-liquidity ratio = buy-side size / sell-side size.
    NOT aggressor flow (which is UNOBSERVABLE). > 1 => more resting bid size."""
    if measure == "imb_L1":
        a, b = snap["bid_qty"], snap["ask_qty"]
    elif measure == "imb_D5":
        a, b = snap["sb5"], snap["ss5"]
    else:  # imb_BOOK
        a, b = snap["tbq"], snap["tsq"]
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return a / b


def _side(ratio, thr):
    if ratio is None:
        return None
    if ratio >= thr:
        return "BUY"
    if (1.0 / ratio) >= thr:
        return "SELL"
    return None


def persistent_events(snaps, measure, thr, p_min):
    """An imbalance EVENT = the same-sign passive imbalance >= thr held for
    >= p_min consecutive snapshots. Returns the snapshot index at which the
    event is CONFIRMED (>= p_min in a row), plus run stats. Zero look-ahead:
    confirmation uses only snapshots up to that index."""
    ev = []
    run_side, run_lo, ratios = None, None, []
    for i, s in enumerate(snaps):
        r = _ratio(s, measure)
        side = _side(r, thr)
        if side is not None and side == run_side:
            ratios.append(r)
            if (i - run_lo + 1) == p_min:                 # just reached persistence
                dur = (snaps[i]["t"] - snaps[run_lo]["t"]).total_seconds()
                ev.append({"idx": i, "side": side, "n_snaps": p_min,
                           "ratio_mean": round(st.fmean(ratios), 3),
                           "ratio_at_confirm": round(r, 3),
                           "persist_sec": round(dur, 1)})
        elif side is not None:
            run_side, run_lo, ratios = side, i, [r]
        else:
            run_side, run_lo, ratios = None, None, []
    return ev


# ---------------------------------------------------------------- forward walk (snapshot LTP path)
def walk(snaps, i0, side):
    """Entry = LTP at the confirming snapshot. R = L1 spread at entry (>= a
    floor). target = entry +/- 3R, stop = entry -/+ 1R in the imbalance
    direction. Walk the ~30 s LTP snapshots to session end. Snapshot cadence
    means times are +/- one poll (~30 s)."""
    e = snaps[i0]
    entry, t0 = e["ltp"], e["t"]
    R = max(e["spread"], 1e-6)
    dirmul = 1.0 if side == "BUY" else -1.0
    tgt = entry + dirmul * RR_TARGET * R
    slp = entry - dirmul * RR_STOP * R
    mfe = mae = 0.0
    tt_tgt = tt_sl = None
    fwd = {m: None for m in FWD_MIN}
    realized = None
    for s in snaps[i0 + 1:]:
        dt = (s["t"] - t0).total_seconds()
        adv = dirmul * (s["ltp"] - entry)          # favourable move, points
        mfe = max(mfe, adv)
        mae = min(mae, adv)
        for m in FWD_MIN:
            if fwd[m] is None and dt >= m * 60:
                fwd[m] = round(adv / R, 3)
        hit_t = (s["ltp"] >= tgt) if side == "BUY" else (s["ltp"] <= tgt)
        hit_s = (s["ltp"] <= slp) if side == "BUY" else (s["ltp"] >= slp)
        if hit_s and tt_sl is None:
            tt_sl = dt
        if hit_t and tt_tgt is None:
            tt_tgt = dt
        if tt_tgt is not None or tt_sl is not None:
            break
    if tt_tgt is not None and (tt_sl is None or tt_tgt <= tt_sl):
        realized, outcome = RR_TARGET, "TARGET"
    elif tt_sl is not None:
        realized, outcome = -RR_STOP, "SL"
    else:
        realized = round(dirmul * (snaps[-1]["ltp"] - entry) / R, 4)
        outcome = "EOD"
    return {"entry": entry, "R_pts": round(R, 4), "side": side,
            "MFE_R": round(mfe / R, 3), "MAE_R": round(mae / R, 3),
            "target_hit": outcome == "TARGET", "sl_hit": outcome == "SL",
            "time_to_target_s": tt_tgt, "time_to_sl_s": tt_sl,
            "realized_R": realized, "outcome": outcome,
            **{f"fwd_{m}m_R": fwd[m] for m in FWD_MIN}}


# ---------------------------------------------------------------- aggregate
def agg(recs):
    n = len(recs)
    if not n:
        return {"n": 0}
    rz = [r["realized_R"] for r in recs]
    wins = [x for x in rz if x > 0]
    losses = [x for x in rz if x < 0]
    gp, gl = sum(wins), -sum(losses)
    tt = [r["time_to_target_s"] for r in recs if r["time_to_target_s"] is not None]
    ts = [r["time_to_sl_s"] for r in recs if r["time_to_sl_s"] is not None]
    return {
        "n": n,
        "target_hit_rate": round(sum(1 for r in recs if r["target_hit"]) / n, 3),
        "sl_hit_rate": round(sum(1 for r in recs if r["sl_hit"]) / n, 3),
        "expectancy_R": round(st.fmean(rz), 3),
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
        "MFE_R_med": round(st.median(r["MFE_R"] for r in recs), 3),
        "MAE_R_med": round(st.median(r["MAE_R"] for r in recs), 3),
        "t2target_med_s": round(st.median(tt), 1) if tt else None,
        "t2sl_med_s": round(st.median(ts), 1) if ts else None,
        "sessions": len({r["session"] for r in recs}),
    }


def _fmt(tag, m):
    if not m.get("n"):
        return f"  {tag:<34} n=0"
    return (f"  {tag:<34} n={m['n']:>4} ses={m['sessions']} "
            f"tgt%={m['target_hit_rate']*100:>5.1f} sl%={m['sl_hit_rate']*100:>5.1f} "
            f"E[R]={m['expectancy_R']:>6.2f} PF={m['profit_factor']} "
            f"MFE~{m['MFE_R_med']:>5.2f} MAE~{m['MAE_R_med']:>6.2f} "
            f"t2tgt={m['t2target_med_s']} t2sl={m['t2sl_med_s']}")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(OUT))
    a = ap.parse_args()
    if not HDB.exists():
        sys.exit(f"[STOP] {HDB} not found.")
    con = sqlite3.connect(f"file:{HDB}?mode=ro", uri=True)
    P = print
    P("=" * 100)
    P("L2 ORDER-FLOW IMBALANCE -- RESEARCH-FIRST DESIGN + FEASIBILITY PROBE (READ-ONLY)")
    P("PASSIVE resting-book imbalance only. Aggressor-classified pressure is UNOBSERVABLE and is")
    P("never synthesised. No production wiring, no live-order change, no engine/H1-H7/trading change.")
    P("=" * 100)

    # ---- 1. field census ------------------------------------------------
    P("\n[1] GENUINE L2 FIELD CENSUS (quote_snapshots, source=ANGELONE_QUOTE_FULL)")
    tot = con.execute("SELECT COUNT(*), MIN(session_date_ist), MAX(session_date_ist), "
                      "COUNT(DISTINCT session_date_ist) FROM quote_snapshots").fetchone()
    P(f"  all rows={tot[0]:,}  span {tot[1]}..{tot[2]}  distinct session dates={tot[3]}")
    P("  per tradable FUTURE symbol -> sessions with a usable L1 book (bid/ask>0, bid<=ask):")
    data = {}
    usable_sessions = set()
    for sym in SYMBOLS:
        bd = load(con, sym)
        data[sym] = bd
        good = {d: len(s) for d, s in bd.items() if len(s) >= 20}
        usable_sessions |= {(sym, d) for d in good}
        P(f"    {sym:<11} {len(good)} session(s): " +
          ", ".join(f"{d}({n})" for d, n in sorted(good.items())))
    P("\n  aggressor / trade-side / delta / cumulative-delta / order-book-event-stream: "
      "NONE (UNOBSERVABLE)")

    # ---- 2. STOP condition on the gate AS SPECIFIED --------------------
    P("\n[2] GATE AS SPECIFIED (aggressive-buy pressure / aggressive-sell pressure >= 2.0)")
    P("  ==> CANNOT BE IMPLEMENTED. No aggressor-classified trade data exists anywhere in this")
    P("      environment (no tick feed, no per-trade side, no delta). Synthesising it is prohibited.")
    P("      The rest of this probe studies a PASSIVE-BOOK depth-imbalance variant built from")
    P("      genuine resting-liquidity sizes -- a different quantity, reported as such.")

    # ---- 3-8. passive-book event study -------------------------------------
    P("\n[3] PASSIVE-BOOK IMBALANCE EVENT STUDY  (persistence x threshold x measure)")
    P("  measure: imb_L1 = bid_qty/ask_qty | imb_D5 = sum5(buy.q)/sum5(sell.q) | imb_BOOK = tot_buy/tot_sell")
    P("  event = same-sign ratio >= T for >= P consecutive ~30s snapshots (zero look-ahead).")
    P("  geometry (SCORING ref, not an order): entry=LTP@confirm, R=L1 spread, target=+3R, stop=-1R,")
    P("  walked over the ~30s LTP snapshot path to session close.")

    all_recs = []
    for sym in SYMBOLS:
        for d, snaps in sorted(data[sym].items()):
            if len(snaps) < 20:
                continue
            for measure in MEASURES:
                for thr in THRESHOLDS:
                    for p in PERSIST:
                        for ev in persistent_events(snaps, measure, thr, p):
                            w = walk(snaps, ev["idx"], ev["side"])
                            if w is None:
                                continue
                            all_recs.append({
                                "symbol": sym, "session": d, "measure": measure,
                                "threshold": thr, "persist": p,
                                "confirm_ts": snaps[ev["idx"]]["t"].isoformat(),
                                "ratio_mean": ev["ratio_mean"], "persist_sec": ev["persist_sec"],
                                **w})

    # baseline: every snapshot (>= START) as a pseudo-event, both sides, same geometry
    base_recs = []
    for sym in SYMBOLS:
        for d, snaps in sorted(data[sym].items()):
            if len(snaps) < 20:
                continue
            for i in range(5, len(snaps) - 5, 3):
                for side in ("BUY", "SELL"):
                    w = walk(snaps, i, side)
                    if w:
                        base_recs.append({"symbol": sym, "session": d, "measure": "BASELINE",
                                          "threshold": 0, "persist": 0, **w})

    P(f"\n  events produced: {len(all_recs)}   baseline pseudo-events: {len(base_recs)}")
    P("\n  BASELINE (no imbalance filter, both sides):")
    P(_fmt("baseline", agg(base_recs)))
    P("\n  imb_D5 (5-level depth) -- persistence P x threshold T:")
    for p in PERSIST:
        for thr in THRESHOLDS:
            sub = [r for r in all_recs if r["measure"] == "imb_D5" and r["persist"] == p and r["threshold"] == thr]
            P(_fmt(f"  P>={p}  T>={thr}", agg(sub)))
    P("\n  threshold sweep at P>=2, pooled over measures:")
    for thr in THRESHOLDS:
        P(_fmt(f"  T>={thr}", agg([r for r in all_recs if r["persist"] == 2 and r["threshold"] == thr])))
    P("\n  persistence sweep at T>=2.0 (200%), pooled over measures:")
    for p in PERSIST:
        P(_fmt(f"  P>={p}", agg([r for r in all_recs if r["persist"] == p and r["threshold"] == 2.0])))
    P("\n  the 200% gate (T>=2.0, P>=2) by measure:")
    for measure in MEASURES:
        P(_fmt(f"  {measure}", agg([r for r in all_recs
              if r["measure"] == measure and r["threshold"] == 2.0 and r["persist"] == 2])))

    # ---- 9. walk-forward split --------------------------------------------
    P("\n[4] OUT-OF-SAMPLE / WALK-FORWARD SPLIT (chronological by session)")
    sess = sorted({r["session"] for r in all_recs})
    P(f"  usable sessions for the study: {sess}")
    gate = [r for r in all_recs if r["threshold"] == 2.0 and r["persist"] == 2]
    for d in sess:
        P(_fmt(f"  200% gate @ {d}", agg([r for r in gate if r["session"] == d])))
    P(_fmt("  200% gate ALL", agg(gate)))
    P(_fmt("  baseline ALL", agg(base_recs)))

    # ---- 10-11. verdict -------------------------------------------------
    P("\n" + "=" * 100)
    P("[5] VERDICT")
    P("=" * 100)
    n_ses = len(sess)
    P(f"  usable futures L2 sessions = {n_ses}  (need >= ~40 independent sessions across >= 2 regimes")
    P("  for a TRAIN/VAL/OOS/HOLDOUT split with any statistical power -- Stage-6/8 standard).")
    P("  All usable sessions are the same calendar week (2026-09-02..04), one volatility regime,")
    P("  intraday-correlated, sampled at ~30 s (a SNAPSHOT cadence, not an L2 update stream).")
    P("")
    P("  1. GATE AS SPECIFIED (aggressor pressure ratio >= 2.0): CANNOT IMPLEMENT -- required data")
    P("     (aggressive-buy / aggressive-sell classified flow) does not exist and is never synthesised.")
    P("  2. PASSIVE-BOOK depth-imbalance variant: data is genuine but INSUFFICIENT -- one week, one")
    P("     regime, snapshot cadence, no order-book event stream. A 4-way walk-forward split is not")
    P("     possible; any threshold/persistence result above is within-noise on correlated sessions.")
    P("  3. Therefore the feature is REJECTED AT THE FEASIBILITY GATE: it cannot be shown to provide")
    P("     a STATISTICALLY STABLE improvement in target-hit rate / expectancy / MAE / SL frequency /")
    P("     profit factor, so per the brief it is rejected. Not disproven -- untestable on this data.")
    P("  4. No production wiring. No live-order change. Consistent with ORDERFLOW_STAGE9_L2_RESEARCH.md")
    P("     (bid-ask & depth imbalance = INSUFFICIENT DATA; aggressor flow = UNOBSERVABLE).")
    P("  5. PROVEN: nothing.")
    P("")
    P("  UNBLOCK REQUIREMENT: a real aggressor-classified tick feed + full-depth L2 update stream")
    P("  for NIFTY/CRUDEOIL futures, persisted across >= 40 sessions spanning >= 2 regimes. Then this")
    P("  probe's event study becomes a genuine walk-forward validation with no code change to the calc.")

    # ---- csv -----------------------------------------------------------
    if all_recs:
        cols = ["symbol", "session", "measure", "threshold", "persist", "confirm_ts",
                "ratio_mean", "persist_sec", "side", "entry", "R_pts", "MFE_R", "MAE_R",
                "target_hit", "sl_hit", "time_to_target_s", "time_to_sl_s", "realized_R",
                "outcome"] + [f"fwd_{m}m_R" for m in FWD_MIN]
        with open(a.csv, "w", newline="") as f:
            wr = _csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            wr.writeheader()
            wr.writerows(all_recs)
        P(f"\nwrote {len(all_recs)} event rows -> {a.csv}  (git-ignored)")
    con.close()


if __name__ == "__main__":
    main()
