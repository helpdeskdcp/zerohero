"""
PHASE 14 — calibration + trading quality metrics from RESOLVED paper trades.

Read-only. Computes Brier score, log loss, expected calibration error (ECE),
a reliability table, win rate, expectancy, profit factor, max drawdown and the
false-signal rate. Nothing is trained or tuned here.

A 70% predicted probability should, in a well-calibrated model, win ~70% of the
time within its cohort. ECE and the reliability table make over/under-confidence
visible.
"""
from __future__ import annotations

import math

from .. import db


def _resolved_rows(limit=5000):
    """(probability, win_bool, pnl, r_multiple) for resolved LIVE scalp signals."""
    rows = db.list_scalp_signals(source="LIVE", status="CLOSED", limit=limit)
    out = []
    for r in rows:
        p = r.get("probability")
        oc = r.get("outcome")
        if p is None or oc not in ("WIN", "LOSS", "FLAT"):
            continue
        out.append((max(0.0, min(1.0, float(p))), 1 if oc == "WIN" else 0,
                    r.get("points"), r.get("r_multiple")))
    return out


def _reliability(pairs, bins=10):
    edges = [i / bins for i in range(bins + 1)]
    table, ece, n = [], 0.0, len(pairs)
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        cell = [w for p, w in pairs if (p >= lo and (p < hi or (i == bins - 1 and p <= hi)))]
        pp = [p for p, _ in pairs if (p >= lo and (p < hi or (i == bins - 1 and p <= hi)))]
        if not cell:
            table.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": 0, "pred": None, "actual": None})
            continue
        pred = sum(pp) / len(pp)
        actual = sum(cell) / len(cell)
        ece += (len(cell) / n) * abs(pred - actual) if n else 0.0
        table.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(cell),
                      "pred": round(pred, 3), "actual": round(actual, 3)})
    return table, round(ece, 4)


def calibration_report(limit=5000) -> dict:
    rows = _resolved_rows(limit)
    n = len(rows)
    base = {"n_resolved": n, "status": "INSUFFICIENT_DATA" if n < 20 else "OK",
            "min_for_stable_metrics": 20}
    if n == 0:
        return {**base, "note": "no resolved LIVE scalp signals with a probability yet"}

    probs = [p for p, *_ in rows]
    wins = [w for _, w, *_ in rows]
    pnls = [x for *_a, x, _ in rows if x is not None]

    brier = sum((p - w) ** 2 for p, w in zip(probs, wins)) / n
    ll = -sum(w * math.log(max(1e-9, p)) + (1 - w) * math.log(max(1e-9, 1 - p))
              for p, w in zip(probs, wins)) / n
    table, ece = _reliability(list(zip(probs, wins)))

    win_rate = sum(wins) / n
    gross_win = sum(x for x in pnls if x > 0)
    gross_loss = -sum(x for x in pnls if x < 0)
    pf = round(gross_win / gross_loss, 3) if gross_loss > 0 else None
    expectancy = round(sum(pnls) / len(pnls), 4) if pnls else None

    # equity-curve max drawdown on realised points
    eq, peak, mdd = 0.0, 0.0, 0.0
    for x in pnls:
        eq += x
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)

    # false-signal rate: BUY signals that lost (of all resolved BUY signals)
    fsr = round(sum(1 for w in wins if w == 0) / n, 4)

    overconf = ece > 0.1 and (sum(probs) / n) > win_rate + 0.05
    return {
        **base,
        "brier": round(brier, 4),
        "log_loss": round(ll, 4),
        "ece": ece,
        "reliability": table,
        "win_rate": round(win_rate, 4),
        "mean_predicted": round(sum(probs) / n, 4),
        "profit_factor": pf,
        "expectancy_points": expectancy,
        "max_drawdown_points": round(mdd, 3),
        "false_signal_rate": fsr,
        "overconfidence_flag": bool(overconf),
        "verdict": ("OVERCONFIDENT" if overconf
                    else "WELL_CALIBRATED" if ece <= 0.07
                    else "MILD_MISCALIBRATION"),
    }
