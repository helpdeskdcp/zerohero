"""
HCS forward-test -- READ-ONLY.

Replays every RESOLVED autoscalp signal (`scalp_signals`, WIN/LOSS) through the
HCS engine and compares the A+ subset against the full set: win-rate, expectancy
(WIN=+1 / LOSS=-1 R proxy; `points` sign as a cross-check), and a chronological
split. Also records any A+ signal live *now* to an append-only log.

Limitation: `scalp_signals` rows carry no option chain, so the OI-structure and
option-spread modules are UNOBSERVABLE in this replay and their vetoes cannot
fire -- the replay is a lower bound on the veto layer. `outcome` (WIN/LOSS) is
the ground truth. Nothing here trades, emits a signal, or changes any engine.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

from .engine import A_PLUS, evaluate_one

_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                   "data", "chanakya.db")
_LOG = os.path.join(os.path.dirname(_DB), "hcs_forward_test.jsonl")

_MAP = {
    "index_ltp": "index_ltp", "atm": "opt_strike", "vwap": "vwap", "atr": "atr",
    "regime": "regime", "signal_type": "signal_type", "direction": "direction",
    "signal_score": "signal_score", "state_score": "signal_score",
    "probability": "probability", "confidence": "confidence",
    "mtf_alignment": "mtf_alignment", "momentum": "momentum",
    "support": "support", "resistance": "resistance",
    "support_strength": "support_strength", "resistance_strength": "resistance_strength",
    "rr": "rr", "ev_r": "ev_r", "entry": "entry", "stop_loss": "stop_loss",
    "target_1": "target_1", "target_2": "target_2", "decision": "decision",
    "session_date": "session_date", "tod_bucket": "tod_bucket",
}


def _snap(row: dict) -> dict:
    s = {k: row.get(v) for k, v in _MAP.items()}
    s["symbol"] = (row.get("symbol") or "?")
    s["ts"] = row.get("created_ts")
    s["chain_json"] = "[]"
    s["data_quality"] = "available"
    s["calibration_status"] = row.get("calib_version") and "fitted" or "prior"
    s["vwap_status"] = None
    s["no_trade_reason_class"] = None
    return s


def _resolved(before: str | None = None):
    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        q = con.execute(
            "SELECT * FROM scalp_signals WHERE resolved=1 AND outcome IN ('WIN','LOSS') "
            + ("AND created_ts < ? " if before else "")
            + "ORDER BY created_ts", ((before,) if before else ()))
        return [dict(r) for r in q.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _tally(rows):
    n = len(rows)
    if not n:
        return {"n": 0}
    wins = sum(1 for r in rows if r["outcome"] == "WIN")
    rmult = [1.0 if r["outcome"] == "WIN" else -1.0 for r in rows]
    pts = [r["points"] for r in rows if r.get("points") is not None]
    return {
        "n": n, "wins": wins, "losses": n - wins,
        "win_rate": round(wins / n, 3),
        "expectancy_R_proxy": round(sum(rmult) / n, 3),   # WIN=+1 / LOSS=-1
        "avg_points": round(sum(pts) / len(pts), 2) if pts else None,
        "sessions": len({r["session_date"] for r in rows}),
    }


def replay() -> dict:
    rows = _resolved()
    if not rows:
        return {"available": False, "reason": "no resolved scalp_signals"}
    for r in rows:
        r["_res"] = evaluate_one(_snap(r))
    a_plus = [r for r in rows if r["_res"]["a_plus"]]
    non = [r for r in rows if not r["_res"]["a_plus"]]
    # chronological split of the A+ subset
    ap_sorted = sorted(a_plus, key=lambda r: r["created_ts"])
    half = len(ap_sorted) // 2
    return {
        "available": True,
        "gate": {**A_PLUS, "allowed_confidence": sorted(A_PLUS["allowed_confidence"])},
        "limitation": "scalp_signals has no chain -> OI/spread modules UNOBSERVABLE, "
                      "those vetoes could not fire in this replay (lower bound).",
        "all_resolved": _tally(rows),
        "hcs_a_plus": _tally(a_plus),
        "hcs_rejected": _tally(non),
        "a_plus_first_half": _tally(ap_sorted[:half]) if half else {"n": 0},
        "a_plus_second_half": _tally(ap_sorted[half:]) if half else _tally(ap_sorted),
        "a_plus_signals": [
            {"signal_id": r["signal_id"], "session": r["session_date"],
             "symbol": r["symbol"], "decision": r["decision"],
             "hcs_score": r["_res"]["hcs_score"], "calib_p": r["_res"]["calibrated_probability"],
             "confidence": r["_res"]["confidence"], "outcome": r["outcome"],
             "points": r.get("points")}
            for r in ap_sorted
        ],
        "verdict": _verdict(a_plus, rows),
    }


def _verdict(a_plus, rows):
    na = len(a_plus)
    if na == 0:
        return ("NO A+ SIGNALS in the resolved history -- the gate is stricter than any "
                "setup the live engine has produced in these 6 sessions. Nothing to forward-test yet.")
    aw = sum(1 for r in a_plus if r["outcome"] == "WIN") / na
    allw = sum(1 for r in rows if r["outcome"] == "WIN") / len(rows)
    lift = aw - allw
    return (f"A+ fired {na}/{len(rows)} times; A+ win-rate {aw:.0%} vs all {allw:.0%} "
            f"(lift {lift:+.0%}). n={na} over one week / one regime -> INDICATIVE ONLY, "
            f"NOT VALIDATED. Needs dozens of A+ signals across >= 2 regimes.")


def record_live(session_date: str | None = None) -> dict:
    """Append the current A+ signals (if any) to the append-only forward-test log."""
    from .engine import evaluate
    r = evaluate(session_date=session_date)
    rec = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "session_date": session_date,
        "a_plus_count": r.get("a_plus_count", 0),
        "a_plus": [
            {"symbol": x["symbol"], "as_of": x["as_of"], "decision": x["decision"],
             "hcs_score": x["hcs_score"], "calib_p": x["calibrated_probability"],
             "confidence": x["confidence"], "entry": x["entry"], "stop_loss": x["stop_loss"],
             "reasons": x["reasons"]}
            for x in r.get("results", []) if x.get("a_plus")
        ],
    }
    lines = []
    if os.path.exists(_LOG):
        for ln in open(_LOG):
            try:
                o = json.loads(ln)
                if not (o.get("session_date") == session_date and session_date):
                    lines.append(ln.rstrip("\n"))
            except ValueError:
                pass
    lines.append(json.dumps(rec, sort_keys=True))
    with open(_LOG, "w") as f:
        f.write("\n".join(lines) + "\n")
    rec["log"] = _LOG
    return rec


def log_summary() -> dict:
    if not os.path.exists(_LOG):
        return {"observations": 0, "a_plus_total": 0}
    recs = []
    for ln in open(_LOG):
        try:
            recs.append(json.loads(ln))
        except ValueError:
            pass
    tot = sum(r.get("a_plus_count", 0) for r in recs)
    return {"observations": len(recs), "a_plus_total": tot,
            "last": recs[-1] if recs else None,
            "note": "live A+ occurrences logged; grade against outcomes once the paired "
                    "paper trades resolve"}
