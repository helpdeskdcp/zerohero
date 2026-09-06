#!/usr/bin/env python3
"""
orderflow_h1h7_spike_options.py -- RESEARCH / EVALUATION ONLY.

First pass of the frozen H1/H7 Structural State Engine
(app/orderflow/h1h7_state.py) AND the frozen spike / sideways_spike / hammer
breakout engine (app/orderflow/smart_money.py) over the imported Upstox
EXPIRED-OPTIONS premium bars -- i.e. the option premium itself is the price
series, not the underlying.

Data source: the `option_bars_5m` VIEW (5-minute buckets aggregated on read from
the 1-minute normalized_option_bars, source='upstox_expired_options') in the
SEPARATE research DB data/historical/upstox/upstox_research.db. Nothing is copied.

FROZEN -- this script changes NOTHING on the engine / classifier / Stage-6
baseline / spike rules / entry / exit / trading / execution / broker /
live_trading / paper_mode / risk / test / production path. It only:
  * reads completed 5-minute premium bars (zero look-ahead),
  * feeds them UNCHANGED to classify_session() and smart_money_setups(),
  * forward-walks the premium with a realistic stop fill to score each already
    classified event,
  * aggregates per state / regime / chronological split,
  * compares against the frozen Stage-6 decision list on the same events,
  * checks the result against PRE-DECLARED gates.

Unit of analysis = one (option contract, trading day). RTH bars only
(09:15..15:30 IST). Direction = the spike direction for every state, uniformly
(for H7_* that is the 'buy the break' premium outcome the AVOID label says to
skip -- fading H7 is REJECTED, Stage-8, and is not walked).

KNOWN LIMITS, stated up front (not worked around):
  * The engine emits H1_CONT / CONTINUATION_CANDIDATE for CRUDEOIL only. The
    underlying here is NIFTY, so every continuation-gate match is emitted as
    H1_CONT_OBSERVE (NOT_VALIDATED / observe-only). The 'actionable' state gets
    ZERO samples BY DESIGN -- exactly like the kaggle_nifty run.
  * No bid/ask, no depth, no aggressor tape in the option data -> true order
    flow stays UNOBSERVABLE and is never estimated. Theta IS in the walked
    premium path; the bid/ask SPREAD is NOT -- so a spread-haircut sensitivity
    is printed as a DIAGNOSTIC (not a gate, not a rule).
  * Span is ~11 weeks (2026-06-16..2026-09-01 expiries) of one underlying ->
    thin regime diversity, heavy intraday / expiry correlation. There is NO
    untouched multi-month holdout -> nothing here can ever be 'PROVEN'.

Outputs:
  data/orderflow_h1h7_spike_options.csv        (one row per eligible H1/H7 event)
  data/orderflow_spike_options_legs.csv        (one row per spike/sideways/hammer leg)
  stdout report
"""
from __future__ import annotations

import argparse
import csv as _csv
import sqlite3
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.orderflow import h1h7_state as H
from app.orderflow import smart_money as SM
from scripts.orderflow_h1h7_performance import (
    _walk, _decision_list, _regime, agg as h1_agg, _fmt, _wilson,
    FR_TRAIN, FR_VAL, FR_OOS,
)
from scripts.orderflow_smartmoney_kaggle_nifty import agg as sp_agg, _line as sp_line

DB = Path(__file__).resolve().parents[1] / "data" / "historical" / "upstox" / "upstox_research.db"
UNDERLYING = "NIFTY"          # passed to the UNCHANGED engine as the symbol
PREM_TICK = 0.05             # NSE index-option premium tick
RR, VOL_MULT, STOP_FRAC = 3.0, 2.0, 1.0
SPREAD_TICKS = (1, 2, 4)     # round-trip spread haircut -- DIAGNOSTIC only
MIN_BARS = H.START_IDX + H.FWD_WINDOW + 1   # 12

VIEW_COLS = ("timestamp", "instrument_key", "expiry", "strike", "option_type",
             "open", "high", "low", "close", "volume", "open_interest",
             "n_1m_bars", "quality_flag")


def _rth(ts: str) -> bool:
    hm = ts[11:16]
    return "09:15" <= hm <= "15:30"


def _contract_sessions(con):
    """Yield (instrument_key, expiry, option_type, strike, session_date, bars[])
    -- bars are the RTH 5-minute premium bars for that contract-day, oldest
    first, each {bar_start,o,h,l,c,v,oi}. One indexed view query per contract."""
    keys = [r[0] for r in con.execute(
        "SELECT DISTINCT instrument_key FROM normalized_option_bars "
        "WHERE source='upstox_expired_options' ORDER BY instrument_key")]
    for ik in keys:
        rows = con.execute(
            f"SELECT {','.join(VIEW_COLS)} FROM option_bars_5m WHERE instrument_key=? "
            "ORDER BY timestamp", (ik,)).fetchall()
        by_day: dict = {}
        meta = None
        for (ts, _ik, expiry, strike, otype, o, h, l, c, v, oi, n1, qf) in rows:
            if not _rth(ts):
                continue
            meta = (expiry, otype, strike)
            by_day.setdefault(ts[:10], []).append(
                {"bar_start": ts, "o": o, "h": h, "l": l, "c": c,
                 "v": v or 0.0, "oi": oi})
        if not meta:
            continue
        expiry, otype, strike = meta
        for d in sorted(by_day):
            yield ik, expiry, otype, strike, d, by_day[d]


# ---------------------------------------------------------------- H1/H7 events
def _h1h7_events(clean, sym, regime):
    """Replicates scripts/orderflow_h1h7_performance.collect()'s inner loop
    VERBATIM (same helpers, same reference geometry) -- just on premium bars."""
    n = len(clean)
    base = H._running_base(clean)
    H._FRAC_LO, H._FRAC_HI = H._fractals(clean)
    va = H._va_series(clean)
    out = []
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
        stop = R_pts = None
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
        ev = H.classify_market_state(ctx)
        base_state = _decision_list(bool(reclaim2), bool(acc2),
                                    bool(ctx.get("n1_agreement")),
                                    ctx.get("body_fraction"), bool(acc1))
        w = _walk(clean, idx + 1, b["c"], stop, direction, PREM_TICK) if stop is not None else None
        out.append({
            "timestamp": b["bar_start"], "regime": regime,
            "new_state": ev["state"], "new_action": ev["action"],
            "new_research_status": ev["research_status"], "baseline_state": base_state,
            "direction": direction, "entry_ref": round(b["c"], 4),
            "structural_level": ctx["broken_level"],
            "sl_price": round(stop, 4) if stop is not None else None,
            "risk_points": round(R_pts, 4) if R_pts is not None else None,
            "available_R": ctx.get("available_R"), "disp_atr": ctx.get("disp_atr"),
            "body_fraction": ctx.get("body_fraction"),
            "reclaim_distance_ratio": ctx.get("reclaim_distance_ratio"),
            "reclaimed_within_3": ctx.get("reclaimed_within_3"),
            "acc1": acc1, "acc2": acc2, "reclaim2": reclaim2,
            "MFE_R": w["MFE_R"] if w else None, "MAE_R": w["MAE_R"] if w else None,
            "reached_1R": w["reached_1R"] if w else None,
            "reached_2R": w["reached_2R"] if w else None,
            "reached_3R": w["reached_3R"] if w else None,
            "sl_first": w["sl_first"] if w else None,
            "fix3_R": w["fix3_R"] if w else None, "exit": w["exit"] if w else None,
        })
    return out


# ---------------------------------------------------------------- spike legs
def _oi_confirm_ts(bars):
    """set of trigger bar_start where option OI rose across the bar (new positions)."""
    ok = set()
    prev = None
    for b in bars:
        oi = b.get("oi")
        if oi is not None and prev is not None and oi > prev:
            ok.add(b["bar_start"])
        if oi is not None:
            prev = oi
    return ok


def _spike_legs(bars, patterns=("spike", "sideways_spike", "hammer")):
    oi_ok = _oi_confirm_ts(bars)
    rows = []
    for pat in patterns:
        out = SM.smart_money_setups(bars, volume_mult=VOL_MULT, rr=RR,
                                    stop_frac=STOP_FRAC, trail=False,
                                    sig_filter="none", pattern=pat)
        if out.get("status") != "OK":
            continue
        for su in out["setups"]:
            cts = su["candle"]["bar_start"]
            for sk in ("buy", "sell"):
                leg = su.get(sk)
                if not leg:
                    continue
                oc = leg["outcome"]
                base = {"candle_ts": cts, "pattern": pat, "side": leg["side"],
                        "entry": leg["entry"], "stop_loss": leg["stop_loss"],
                        "target": leg["target"], "risk_points": leg["risk_points"],
                        "result": oc["status"], "points": oc["points"]}
                rows.append({**base, "variant": pat})
                if pat == "spike" and cts in oi_ok:
                    rows.append({**base, "variant": "spike+oi_confirm"})
    return rows


# ---------------------------------------------------------------- collect
def collect():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    h1_rows, sp_rows = [], []
    csess = set()
    n_contracts = 0
    last_ik = None
    for ik, expiry, otype, strike, d, bars in _contract_sessions(con):
        if ik != last_ik:
            n_contracts += 1
            last_ik = ik
            print(f"  ... {n_contracts:>3} contracts, {len(csess):>5} sessions", file=sys.stderr) \
                if n_contracts % 50 == 0 else None
        clean_h = H._clean(bars)
        clean_s = SM._clean(bars)
        if len(clean_h) < MIN_BARS:
            continue
        csess.add((ik, d))
        regime = _regime(clean_h)
        is_exp = (d == expiry)
        for e in _h1h7_events(clean_h, UNDERLYING, regime):
            e.update(instrument_key=ik, expiry=expiry, option_type=otype,
                     strike=strike, session=d, is_expiry_day=is_exp)
            h1_rows.append(e)
        for leg in _spike_legs(bars):
            leg.update(instrument_key=ik, expiry=expiry, option_type=otype,
                       strike=strike, session=d, regime=regime, is_expiry_day=is_exp)
            sp_rows.append(leg)
    con.close()
    return h1_rows, sp_rows, sorted(csess)


# ---------------------------------------------------------------- splits
def _split_map(csess):
    """chronological rank of (contract, session) by date -> TRAIN/VAL/OOS/HOLDOUT."""
    order = sorted(csess, key=lambda cs: (cs[1], cs[0]))
    n = len(order)
    a, b, c = int(n * FR_TRAIN), int(n * FR_VAL), int(n * FR_OOS)
    m = {}
    for i, cs in enumerate(order):
        m[cs] = "TRAIN" if i < a else "VALIDATION" if i < b else "OOS" if i < c else "HOLDOUT"
    return m


# ---------------------------------------------------------------- report
NEW_STATES = ["H1_CONT", "H1_CONT_OBSERVE", "H1_CONT_BLOCKED", "H1_WEAK",
              "H7_TRAP", "H7_LEANING_TRAP", "AMBIGUOUS", "SPIKE_NO_LEVEL"]
SP_VARIANTS = ("spike", "sideways_spike", "hammer", "spike+oi_confirm")


def _haircut_ER(rows, spread_ticks):
    w = [r for r in rows if r["fix3_R"] is not None and r["risk_points"]]
    if not w:
        return None
    vals = [r["fix3_R"] - spread_ticks * PREM_TICK / r["risk_points"] for r in w]
    return round(st.fmean(vals), 4)


def report(h1_rows, sp_rows, csess, out):
    p = lambda *a: print(*a, file=out)
    smap = _split_map(csess)
    for r in h1_rows:
        r["split"] = smap.get((r["instrument_key"], r["session"]), "OTHER")
    for r in sp_rows:
        r["split"] = smap.get((r["instrument_key"], r["session"]), "OTHER")

    p("=" * 104)
    p("H1/H7 STRUCTURAL STATE ENGINE  +  SPIKE/SIDEWAYS/HAMMER  --  ON UPSTOX EXPIRED-OPTION PREMIUM 5m BARS")
    p("RESEARCH / EVALUATION ONLY. No engine / baseline / spike rule / trading / execution / live_trading /")
    p("paper_mode / risk / test / production change. The PRICE SERIES IS THE OPTION PREMIUM itself.")
    p("Direction = spike direction for every state. 'win' = realised R at a fixed 3R target w/ realistic")
    p("stop fill > 0. Baseline = frozen Stage-6 decision list on the SAME events. Premium tick = 0.05.")
    p("Theta is in the walked premium path; bid/ask SPREAD is NOT (see the spread-haircut diagnostic).")
    p("=" * 104)

    exps = sorted({r["expiry"] for r in h1_rows} | {r["expiry"] for r in sp_rows})
    dsr = sorted({cs[1] for cs in csess})
    regs: dict = {}
    for r in h1_rows:
        regs[r["regime"]] = regs.get(r["regime"], 0) + 1
    p(f"\nDATA: {len(csess)} (contract,day) sessions | {len(set(cs[0] for cs in csess))} contracts | "
      f"{len(exps)} expiries {exps[0]}..{exps[-1]} | dates {dsr[0]}..{dsr[-1]}")
    p(f"      H1/H7 eligible events = {len(h1_rows)} ; spike/sideways/hammer legs = {len(sp_rows)}")
    p(f"      event regime mix (H1/H7): {regs}")
    ce = sum(1 for r in h1_rows if r['option_type'] == 'CE')
    pe = sum(1 for r in h1_rows if r['option_type'] == 'PE')
    xd = sum(1 for r in h1_rows if r['is_expiry_day'])
    p(f"      H1/H7 events CE/PE = {ce}/{pe} ; on expiry day = {xd}")

    # ---------- H1/H7 ----------
    p("\n" + "-" * 104)
    p("[1] H1/H7 ENGINE -- per state (premium walk, all contracts pooled)")
    p("-" * 104)
    for s in NEW_STATES:
        p(_fmt(s, h1_agg([r for r in h1_rows if r["new_state"] == s])))

    p("\n[1b] derived buckets")
    p(_fmt("research_status=SUPPORTED", h1_agg([r for r in h1_rows if r["new_research_status"] == "SUPPORTED"])))
    p(_fmt("action=AVOID", h1_agg([r for r in h1_rows if r["new_action"] == "AVOID"])))
    p(_fmt("action=NO_ACTION", h1_agg([r for r in h1_rows if r["new_action"] == "NO_ACTION"])))
    p(_fmt("action=CONTINUATION_CAND.", h1_agg([r for r in h1_rows if r["new_action"] == "CONTINUATION_CANDIDATE"])))

    p("\n[1c] H1_CONT_OBSERVE (the continuation-gate match; NOT acted on -- NIFTY continuation is NOT_VALIDATED)")
    p(_fmt("  CE", h1_agg([r for r in h1_rows if r["new_state"] == "H1_CONT_OBSERVE" and r["option_type"] == "CE"])))
    p(_fmt("  PE", h1_agg([r for r in h1_rows if r["new_state"] == "H1_CONT_OBSERVE" and r["option_type"] == "PE"])))
    p(_fmt("  expiry day", h1_agg([r for r in h1_rows if r["new_state"] == "H1_CONT_OBSERVE" and r["is_expiry_day"]])))
    p(_fmt("  non-expiry day", h1_agg([r for r in h1_rows if r["new_state"] == "H1_CONT_OBSERVE" and not r["is_expiry_day"]])))

    p("\n[1d] by REGIME")
    for s in ("H1_CONT_OBSERVE", "H7_TRAP", "H7_LEANING_TRAP"):
        for reg in ("TREND_UP", "TREND_DOWN", "CHOP"):
            p(_fmt(f"{s} / {reg}", h1_agg([r for r in h1_rows if r["new_state"] == s and r["regime"] == reg])))

    p("\n[1e] CHRONOLOGICAL SPLIT  (contract-session rank: TRAIN 0-45 / VAL 45-65 / OOS 65-82 / HOLDOUT 82-100)")
    for spl in ("TRAIN", "VALIDATION", "OOS", "HOLDOUT"):
        nse = len({(r["instrument_key"], r["session"]) for r in h1_rows if r["split"] == spl})
        p(f"  {spl:<11} contract-sessions={nse}")
    for s in ("H1_CONT_OBSERVE", "H7_TRAP"):
        p(f"  {s}:")
        for spl in ("TRAIN", "VALIDATION", "OOS", "HOLDOUT"):
            p("  " + _fmt(f"  {spl}", h1_agg([r for r in h1_rows if r["new_state"] == s and r["split"] == spl])))

    p("\n[1f] BASELINE (frozen Stage-6 decision list) vs NEW ENGINE -- same events")
    for label, new_sel, base_sel in (
        ("continuation", lambda r: r["new_state"] == "H1_CONT_OBSERVE", lambda r: r["baseline_state"] == "H1_CONT"),
        ("H7_TRAP (avoid)", lambda r: r["new_state"] == "H7_TRAP", lambda r: r["baseline_state"] == "H7_TRAP"),
    ):
        mn, mb = h1_agg([r for r in h1_rows if new_sel(r)]), h1_agg([r for r in h1_rows if base_sel(r)])
        p(f"\n  {label}")
        p(f"    {'':10} {'n':>6} {'win%':>7} {'E[R]':>8} {'PF':>7} {'netR':>9} {'maxDD':>9} {'maxCL':>6}")
        for nm, m in (("BASELINE", mb), ("NEW", mn)):
            if not m["n"]:
                p(f"    {nm:<10} {'0':>6}")
                continue
            p(f"    {nm:<10} {m['n']:>6} {m['win_rate']*100:>6.1f}% {m['expectancy']:>8.3f} "
              f"{str(m['profit_factor']):>7} {m['net_R']:>9.2f} {m['max_DD_R']:>9.2f} {m['max_consec_losses']:>6}")

    p("\n[1g] SPREAD-HAIRCUT DIAGNOSTIC (round-trip, per event = ticks*0.05 / R). NOT a gate, NOT a rule.")
    p("     Option quotes / bid-ask are not in the data; this only shows how fragile any premium edge is.")
    for s in ("H1_CONT_OBSERVE", "H7_TRAP", "AMBIGUOUS", "H1_WEAK"):
        rr = [r for r in h1_rows if r["new_state"] == s]
        base = h1_agg(rr)
        if not base["n"]:
            continue
        hc = "  ".join(f"-{t}t E[R]={_haircut_ER(rr, t)}" for t in SPREAD_TICKS)
        p(f"  {s:<20} n={base['n']:>4} gross E[R]={base['expectancy']:>7.3f}   {hc}")

    # ---------- SPIKE ----------
    p("\n" + "-" * 104)
    p("[2] SPIKE / SIDEWAYS_SPIKE / HAMMER (frozen smart_money engine) -- on the option premium, real option volume")
    p("-" * 104)
    p("  --- pooled ---")
    for v in SP_VARIANTS:
        p(sp_line(v, sp_agg([r for r in sp_rows if r["variant"] == v])))
    p("  --- spike by regime ---")
    for reg in ("TREND_UP", "TREND_DOWN", "CHOP"):
        p(sp_line(f"spike / {reg}", sp_agg([r for r in sp_rows if r["variant"] == "spike" and r["regime"] == reg])))
    p("  --- spike by CE/PE + expiry day ---")
    p(sp_line("spike / CE", sp_agg([r for r in sp_rows if r["variant"] == "spike" and r["option_type"] == "CE"])))
    p(sp_line("spike / PE", sp_agg([r for r in sp_rows if r["variant"] == "spike" and r["option_type"] == "PE"])))
    p(sp_line("spike / expiry day", sp_agg([r for r in sp_rows if r["variant"] == "spike" and r["is_expiry_day"]])))
    p(sp_line("spike / non-expiry", sp_agg([r for r in sp_rows if r["variant"] == "spike" and not r["is_expiry_day"]])))
    p("  --- spike chronological split ---")
    for spl in ("TRAIN", "VALIDATION", "OOS", "HOLDOUT"):
        p(sp_line(f"spike / {spl}", sp_agg([r for r in sp_rows if r["variant"] == "spike" and r["split"] == spl])))

    # ---------- gates / verdict ----------
    p("\n" + "=" * 104)
    p("[3] VERDICT -- against PRE-DECLARED gates")
    p("=" * 104)

    obs = h1_agg([r for r in h1_rows if r["new_state"] == "H1_CONT_OBSERVE"])
    obs_oos = h1_agg([r for r in h1_rows if r["new_state"] == "H1_CONT_OBSERVE" and r["split"] == "OOS"])
    obs_hold = h1_agg([r for r in h1_rows if r["new_state"] == "H1_CONT_OBSERVE" and r["split"] == "HOLDOUT"])
    obs_rows = [r for r in h1_rows if r["new_state"] == "H1_CONT_OBSERVE"]
    er_2t = _haircut_ER(obs_rows, 2)
    h7 = h1_agg([r for r in h1_rows if r["new_state"] == "H7_TRAP"])
    spk = sp_agg([r for r in sp_rows if r["variant"] == "spike"])

    crit = {
        "engine emits an ACTIONABLE H1_CONT here": h1_agg([r for r in h1_rows if r["new_state"] == "H1_CONT"]).get("n", 0) > 0,
        ">=10 contract-sessions with a continuation event":
            len({(r["instrument_key"], r["session"]) for r in obs_rows}) >= 10,
        ">=50 continuation events": obs.get("n", 0) >= 50,
        ">=2 regimes represented": len(obs.get("regimes", [])) >= 2,
        "OOS slice >=20 events": obs_oos.get("n", 0) >= 20,
        "OOS E[R] > 0": (obs_oos.get("expectancy") or -9) > 0,
        "HOLDOUT slice >=20 events": obs_hold.get("n", 0) >= 20,
        "HOLDOUT E[R] > 0": (obs_hold.get("expectancy") or -9) > 0,
        "continuation E[R] > 0 AFTER a 2-tick round-trip spread haircut": (er_2t or -9) > 0,
        "HOLDOUT is FRESH (contracts non-overlapping in calendar time, no expiry correlation)": False,
    }
    for k, v in crit.items():
        p(f"   [{'PASS' if v else 'FAIL'}] {k}")
    p(f"\n   (continuation H1_CONT_OBSERVE: n={obs.get('n',0)}, "
      f"sessions={len({(r['instrument_key'], r['session']) for r in obs_rows})}, "
      f"regimes={obs.get('regimes', [])}; OOS n={obs_oos.get('n',0)} E[R]={obs_oos.get('expectancy')}; "
      f"HOLDOUT n={obs_hold.get('n',0)} E[R]={obs_hold.get('expectancy')}; "
      f"gross E[R]={obs.get('expectancy')} -> after 2-tick spread {er_2t})")
    p(f"   (H7_TRAP 'buy the break' premium outcome: n={h7.get('n',0)} "
      f"win%={(h7.get('win_rate') or 0)*100:.1f} E[R]={h7.get('expectancy')} -- lower/negative = the AVOID label is right)")
    p(f"   (frozen spike on premium: signals={spk.get('signals',0)} resolved={spk.get('resolved',0)} "
      f"win%={(spk.get('win_rate') or 0) and round(spk['win_rate']*100,1)} E[pts]={spk.get('expectancy_pts')})")

    p("\n  ==> H1/H7 CONTINUATION ON OPTION PREMIUM: NOT VALIDATED.")
    p("      - The engine does not emit an actionable H1_CONT for NIFTY at all (CRUDEOIL-only, frozen).")
    p("      - The observed continuation-gate matches are scored gross of the option bid/ask spread; the")
    p("        2-tick haircut column shows how little survives.")
    p("      - Span is ~11 weeks of one underlying, contracts overlap in calendar time and share expiry")
    p("        dynamics -> the HOLDOUT slice is NOT fresh out-of-sample. Nothing here is PROVEN.")
    p("  ==> SPIKE / SIDEWAYS / HAMMER ON OPTION PREMIUM: NOT VALIDATED -- see the pooled + split numbers")
    p("      above; this is a price-shape breakout on a decaying, convex series, gross of spread.")
    p("  ==> H7 = the reclaim-distance AVOIDANCE reading is reported for information; fading H7 is REJECTED")
    p("      (Stage-8) and is NOT walked. The frozen research ceiling is unchanged.")
    p("  ==> Real order flow (aggressor tape / depth) is UNOBSERVABLE in this data and is never estimated.")
    p("  PROVEN: nothing.")


CSV_H1 = ["timestamp", "session", "expiry", "instrument_key", "option_type", "strike", "is_expiry_day",
          "regime", "split", "new_state", "new_action", "new_research_status", "baseline_state",
          "direction", "entry_ref", "structural_level", "sl_price", "risk_points", "available_R",
          "disp_atr", "body_fraction", "reclaim_distance_ratio", "reclaimed_within_3",
          "acc1", "acc2", "reclaim2", "MFE_R", "MAE_R", "reached_1R", "reached_2R", "reached_3R",
          "sl_first", "fix3_R", "exit"]
CSV_SP = ["session", "expiry", "instrument_key", "option_type", "strike", "is_expiry_day",
          "regime", "split", "variant", "pattern", "candle_ts", "side", "entry", "stop_loss",
          "target", "risk_points", "result", "points"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h1-csv", default=None)
    ap.add_argument("--sp-csv", default=None)
    a = ap.parse_args()
    if not DB.exists():
        sys.exit(f"[STOP] {DB} not found -- run the option import + 5m view first.")
    print("collecting (one indexed view query per contract) ...", file=sys.stderr)
    h1_rows, sp_rows, csess = collect()
    h1p = Path(a.h1_csv) if a.h1_csv else DB.parents[2] / "orderflow_h1h7_spike_options.csv"
    spp = Path(a.sp_csv) if a.sp_csv else DB.parents[2] / "orderflow_spike_options_legs.csv"
    report(h1_rows, sp_rows, csess, sys.stdout)   # sets r["split"]
    with h1p.open("w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=CSV_H1, extrasaction="ignore")
        w.writeheader(); w.writerows(h1_rows)
    with spp.open("w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=CSV_SP, extrasaction="ignore")
        w.writeheader(); w.writerows(sp_rows)
    print(f"\nwrote {len(h1_rows)} H1/H7 rows -> {h1p}")
    print(f"wrote {len(sp_rows)} spike legs -> {spp}")


if __name__ == "__main__":
    main()
