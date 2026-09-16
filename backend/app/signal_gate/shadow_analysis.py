"""
Shared shadow-mode analysis: joins app.db.list_fsg_shadow_analysis() (gate
verdict + real resolved trade outcome, by trade_id) into summary stats.
Used by both scripts/fsg_shadow_analysis.py (on-demand report) and
/api/signals/gate-analysis (live observability) -- one implementation,
not two copies that could drift.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import mean

from .. import db


def _stats(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    pts = [r["y_points"] for r in rows if r["y_points"] is not None]
    win = [p for p in pts if p > 0]
    loss = [p for p in pts if p < 0]
    outcomes = defaultdict(int)
    for r in rows:
        outcomes[r["y_outcome"] or "UNRESOLVED"] += 1
    pf = round(sum(win) / abs(sum(loss)), 3) if loss else None
    return {
        "n": n, "win_rate_pct": round(100.0 * len(win) / len(pts), 1) if pts else None,
        "expectancy": round(mean(pts), 3) if pts else None,
        "profit_factor": pf,
        "outcome_counts": dict(outcomes),
    }


def build_shadow_report(limit: int = 20000) -> dict:
    rows = db.list_fsg_shadow_analysis(limit=limit)
    if not rows:
        return {"n_resolved": 0, "note": "no resolved shadow-log rows yet"}

    report = {"n_resolved": len(rows), "overall": _stats(rows)}

    by_state = defaultdict(list)
    for r in rows:
        by_state[r["state"]].append(r)
    report["by_state"] = {state: _stats(rs) for state, rs in by_state.items()}

    by_symbol = defaultdict(list)
    for r in rows:
        by_symbol[r["symbol"]].append(r)
    report["by_symbol"] = {sym: _stats(rs) for sym, rs in by_symbol.items()}

    approved = [r for r in rows if r["state"] == "APPROVED"]
    not_approved = [r for r in rows if r["state"] != "APPROVED"]
    report["approved_vs_rest"] = {"approved": _stats(approved), "not_approved": _stats(not_approved)}
    return report
