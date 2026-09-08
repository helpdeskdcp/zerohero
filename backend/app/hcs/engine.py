"""
HCS engine -- the A+ gate. SHADOW / READ-ONLY.

Reads the live engine's latest persisted decision per symbol
(`live_market_snapshots`), assembles the 16-module evidence view, scores the
confluence 0-100, runs the hard-filter veto layer, and emits:

  { symbol, decision (BUY_CE|BUY_PE|NO_TRADE), a_plus,
    hcs_score, calibrated_probability, confidence,
    entry, stop_loss, target_1, target_2, invalidation, direction,
    components[], vetoes[], reasons[] }

A trade is emitted ONLY when: the live engine itself decided BUY_CE/BUY_PE,
hcs_score >= HCS_MIN, calibrated_probability >= PROB_MIN, confidence in the
allowed set, AND zero HARD vetoes. Otherwise NO_TRADE with the exact reasons.
Emits no order, no live signal.
"""
from __future__ import annotations

import os
import sqlite3

from . import adaptive as _adp
from . import adaptive_mc as _adp_mc
from . import evidence as _ev
from . import filters as _flt
from . import memory as _mem
from . import score as _sc

_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                   "data", "chanakya.db")

A_PLUS = {
    "hcs_min": 68.0,
    "prob_min": 0.56,
    "allowed_confidence": {"HIGH", "MEDIUM"},
    "max_soft_vetoes": 1,
}


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _latest_snaps(symbol=None, session_date=None):
    try:
        con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []
    con.row_factory = sqlite3.Row
    try:
        where = ["1=1"]
        args = []
        if symbol:
            where.append("symbol=?"); args.append(symbol)
        if session_date:
            where.append("session_date=?"); args.append(session_date)
        q = con.execute(
            f"SELECT * FROM live_market_snapshots WHERE {' AND '.join(where)} "
            "AND ts=(SELECT MAX(ts) FROM live_market_snapshots s2 "
            "        WHERE s2.symbol=live_market_snapshots.symbol"
            + (" AND s2.session_date=live_market_snapshots.session_date" if session_date else "")
            + ") GROUP BY symbol ORDER BY symbol", args)
        return [dict(r) for r in q.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _tod_bucket(ts: str | None) -> str | None:
    if not ts:
        return None
    try:
        hh = int(ts[11:13]); mm = int(ts[14:16])
    except (ValueError, IndexError):
        return None
    m = hh * 60 + mm  # UTC; IST = +330
    ist = (m + 330) % (24 * 60)
    if ist < 9 * 60 + 45:
        return "OPEN"
    if ist < 12 * 60:
        return "MORNING"
    if ist < 13 * 60 + 30:
        return "MIDDAY"
    if ist < 15 * 60:
        return "AFTERNOON"
    return "CLOSE"


def evaluate_one(snap: dict, cfg: dict | None = None) -> dict:
    cfg = {**A_PLUS, **(cfg or {})}
    snap = dict(snap)
    snap.setdefault("tod_bucket", _tod_bucket(snap.get("ts")))
    ev = _ev.assemble(snap)
    direction = ev["direction"]
    mem = _mem.similarity_score(snap)
    sc = _sc.compute(ev, mem, direction=direction)
    vetoes = _flt.run(snap, ev, cfg.get("filters"))
    hard = [v for v in vetoes if v["severity"] == "HARD"]
    soft = [v for v in vetoes if v["severity"] == "SOFT"]

    live_dec = str(snap.get("decision") or "NO_TRADE")
    prob = _f(snap.get("probability"))
    conf = str(snap.get("confidence") or "").upper()

    reasons = []
    a_plus = False
    if live_dec not in ("BUY_CE", "BUY_PE"):
        decision = "NO_TRADE"
        reasons.append(f"live engine decided {live_dec} ({snap.get('reason') or snap.get('no_trade_reason_class') or '-'})")
    elif hard:
        decision = "NO_TRADE"
        reasons.append(f"{len(hard)} hard veto(es): " + "; ".join(v["filter"] for v in hard))
    elif sc["hcs_score"] < cfg["hcs_min"]:
        decision = "NO_TRADE"
        reasons.append(f"HCS score {sc['hcs_score']} < A+ min {cfg['hcs_min']}")
    elif prob is None or prob < cfg["prob_min"]:
        decision = "NO_TRADE"
        reasons.append(f"calibrated probability {prob} < min {cfg['prob_min']}")
    elif conf not in cfg["allowed_confidence"]:
        decision = "NO_TRADE"
        reasons.append(f"confidence {conf or 'NONE'} not in {sorted(cfg['allowed_confidence'])}")
    elif len(soft) > cfg["max_soft_vetoes"]:
        decision = "NO_TRADE"
        reasons.append(f"{len(soft)} soft vetoes > {cfg['max_soft_vetoes']}")
    else:
        decision = live_dec
        a_plus = True
        reasons.append(f"A+ : HCS {sc['hcs_score']}, p {prob}, conf {conf}, 0 hard vetoes")

    # advisory adaptive probability (SHADOW -- does NOT affect the A+ gate above)
    _adp_row = {**snap, "hcs_score": sc["hcs_score"],
                "evidence_coverage": sc["evidence_coverage"],
                "setup_memory_wr": (mem.get("shrunk_win_rate")
                                    if mem.get("status") == "OK" else None)}
    adp = _adp.score(_adp_row)
    # Tier-A multinomial-logit shadow model (3-class + calibrated P(win) + E[R]).
    # SHADOW -- also does NOT affect the A+ gate. Isolated so it can never break evaluate().
    try:
        adp_mc = _adp_mc.score(_adp_row)
    except Exception as _e:  # pragma: no cover - defensive
        adp_mc = {"status": f"ERROR: {type(_e).__name__}"}

    entry = _f(snap.get("entry"))
    atr = _f(snap.get("atr"))
    invalidation = None
    if entry is not None:
        sl = _f(snap.get("stop_loss"))
        invalidation = (f"close beyond {sl} (structural SL)" if sl is not None
                        else (f"{round(entry - 1.2 * atr, 2)} adverse" if atr else None))

    return {
        "symbol": snap.get("symbol"),
        "as_of": snap.get("ts"),
        "decision": decision,
        "a_plus": a_plus,
        "direction": direction,
        "signal_type": ev["signal_type"],
        "hcs_score": sc["hcs_score"],
        "hcs_raw_agreement": sc["raw_agreement"],
        "evidence_coverage": sc["evidence_coverage"],
        "calibrated_probability": prob,
        "probability_source": snap.get("calibration_status"),
        "adaptive_probability": adp.get("adaptive_probability"),
        "adaptive_status": adp.get("status"),
        "adaptive_vs_calibrated": (round(adp["adaptive_probability"] - prob, 4)
                                   if (adp.get("adaptive_probability") is not None and prob is not None)
                                   else None),
        # Tier-A multinomial-logit shadow outputs (advisory; not in any gate)
        "mc_status": adp_mc.get("status"),
        "mc_model_version": adp_mc.get("model_id"),
        "mc_p_up": adp_mc.get("p_up"),
        "mc_p_down": adp_mc.get("p_down"),
        "mc_p_no_move": adp_mc.get("p_no_move"),
        "mc_p_win_calibrated": adp_mc.get("p_win_cal"),
        "mc_expected_r": adp_mc.get("exp_r"),
        "mc_setup_rank": adp_mc.get("setup_rank_score"),
        "confidence": conf or None,
        "expected_premium_move": _f(snap.get("expected_premium_move")),
        "epm_method": snap.get("epm_method"),
        "entry": entry,
        "stop_loss": _f(snap.get("stop_loss")),
        "target_1": _f(snap.get("target_1")) if "target_1" in snap else None,
        "target_2": _f(snap.get("target_2")) if "target_2" in snap else None,
        "invalidation": invalidation,
        "rr": _f(snap.get("rr")), "ev_r": _f(snap.get("ev_r")),
        "regime": snap.get("regime"),
        "setup_memory": mem,
        "components": sc["components"],
        "evidence": ev["modules"],
        "vetoes": vetoes,
        "reasons": reasons,
        "note": "SHADOW / advisory only -- HCS is not wired into the live engine. "
                "Calibration is one-week / one-regime (see HCS_CALIBRATION_REPORT.md); "
                "score is a confluence measure, not a probability.",
    }


def evaluate(symbol: str | None = None, session_date: str | None = None,
             cfg: dict | None = None) -> dict:
    snaps = _latest_snaps(symbol, session_date)
    if not snaps:
        return {"available": False, "reason": "no live_market_snapshots rows",
                "symbol": symbol, "session_date": session_date}
    out = [evaluate_one(s, cfg) for s in snaps]
    return {
        "available": True,
        "generated_from": "live_market_snapshots (latest per symbol)",
        "a_plus_count": sum(1 for r in out if r["a_plus"]),
        "results": out,
        "a_plus_gate": {**A_PLUS, "allowed_confidence": sorted(A_PLUS["allowed_confidence"])},
    }
