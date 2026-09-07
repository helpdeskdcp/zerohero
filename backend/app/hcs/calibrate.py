"""
HCS calibration report -- READ-ONLY.

Runs the EXISTING app/backtest/calibration.fit() + reliability_curve over the
resolved AUTOSCALP paper-trade outcomes joined to their immutable entry
features. Reports: sample size, global logistic k/b, per-regime / per-signal-
type / per-tod coverage vs the 40-row minimum, Brier + ECE on a chronological
holdout, and an explicit INSUFFICIENT verdict while the outcome log is one week
/ one regime.

Nothing is trained here that is not already trained by the live runner; this
just surfaces the numbers.
"""
from __future__ import annotations

import os
import sqlite3
from collections import Counter

from ..backtest import calibration as _cal

_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                   "data", "chanakya.db")


def _samples():
    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT signal_score AS score, regime, signal_type, tod_bucket, "
            "       outcome AS result, created_ts, session_date "
            "FROM scalp_signals "
            "WHERE resolved=1 AND outcome IN ('WIN','LOSS') AND signal_score IS NOT NULL "
            "ORDER BY created_ts").fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def report() -> dict:
    rows = [r for r in _samples() if r.get("score") is not None]
    n = len(rows)
    if n == 0:
        return {"available": False, "reason": "no resolved AUTOSCALP outcomes with an entry-feature score"}

    sessions = sorted({r["session_date"] for r in rows if r.get("session_date")})
    regimes = Counter(r["regime"] or "?" for r in rows)
    types = Counter(r["signal_type"] or "?" for r in rows)
    tods = Counter(r["tod_bucket"] or "?" for r in rows)

    samp = [{"score": r["score"], "regime": r["regime"], "signal_type": r["signal_type"],
             "win": r["result"] == "WIN"} for r in rows]
    calib = _cal.fit(samp, version="hcs-report")

    # chronological holdout: fit on first 70%, score last 30%
    k = int(n * 0.7)
    fit_c = _cal.fit(samp[:k], version="hcs-oos") if k >= _cal._MIN_ROWS else None
    pairs = []
    if fit_c:
        for r in rows[k:]:
            p = _cal.predict(fit_c, r["score"], regime=r["regime"] or "?",
                             signal_type=r["signal_type"] or "?")
            pairs.append((p, r["result"] == "WIN"))
    rel = _cal.reliability_curve(pairs) if pairs else {"n": 0}

    min_rows = _cal._MIN_ROWS
    coverage = {
        "per_regime": {kk: {"n": vv, "fits": vv >= min_rows} for kk, vv in regimes.items()},
        "per_signal_type": {kk: {"n": vv, "fits": vv >= min_rows} for kk, vv in types.items()},
        "per_tod": {kk: {"n": vv, "fits": vv >= min_rows} for kk, vv in tods.items()},
    }

    wins = sum(1 for r in rows if r["result"] == "WIN")
    verdict = (
        "NOT VALIDATED -- INSUFFICIENT. Outcome log is "
        f"{len(sessions)} session(s) ({sessions[0] if sessions else '?'}..{sessions[-1] if sessions else '?'}), "
        "effectively one volatility regime, and the chronological holdout is a few days -- "
        "not a walk-forward. The global logistic curve fits (n>=40) but per-regime / per-tod "
        "curves mostly do not. HCS therefore runs SHADOW and its A+ gate is intentionally strict."
    )
    return {
        "available": True,
        "n_resolved": n, "wins": wins, "losses": n - wins,
        "base_win_rate": round(wins / n, 4),
        "sessions": sessions,
        "min_rows_for_a_curve": min_rows,
        "global_curve": calib.get("global"),
        "fitted_curves": {kk: vv for kk, vv in (calib.get("curves") or {}).items()},
        "coverage": coverage,
        "oos_holdout": {
            "fit_on_first": k, "scored_last": len(pairs),
            "brier": rel.get("brier"), "ece": rel.get("ece"),
            "reliability_bins": rel.get("bins"),
        },
        "verdict": verdict,
    }
