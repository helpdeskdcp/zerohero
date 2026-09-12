"""
Persistence for the Institutional Edge layer -- section 12: "Store: sample
size, probability, baseline, conditional probability, win/loss, mean/median
return, variance, confidence interval, regime, OOS result... Every result
must be reproducible" (section 21).

Own dedicated DB file (data/institutional_edge.db), same isolation
rationale as structural_break/audit_log.py: this is a research/decision log
for a shadow analytical layer, never the live app DB (chanakya.db) or the
capture DB (market_history.db) -- no lock/contention risk with either.

Unlike audit_log.py (which logs only state TRANSITIONS, because most
structural-break evaluations don't transition and logging every one would
be noise without audit value), this layer logs EVERY evaluation: section 12
explicitly asks to store sample size/probabilities/CI/returns for the edge
record itself, not just its state changes -- these rows ARE the research
data this layer exists to produce.

`oos_result` is included as a column but always None until a real
walk-forward/OOS run (mega-brief Phase 13, not built yet) populates it --
an honest gap, not a fabricated field.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "institutional_edge.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS edge_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_ts TEXT NOT NULL,
    instrument TEXT NOT NULL,
    condition_label TEXT NOT NULL,
    regime TEXT,
    n_condition INTEGER,
    n_baseline INTEGER,
    p_condition REAL,
    p_baseline REAL,
    conditional_edge REAL,
    edge_ci95_low REAL,
    edge_ci95_high REAL,
    mean_return REAL,
    median_return REAL,
    variance_return REAL,
    win_rate REAL,
    avg_win_points REAL,
    avg_loss_points REAL,
    gross_ev_points REAL,
    cost_points REAL,
    net_ev_points REAL,
    risk_adjusted_ev REAL,
    edge_score INTEGER,
    edge_score_max INTEGER,
    reason_codes_json TEXT,
    state TEXT,
    oos_result_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_ee_instrument_cond ON edge_evaluations(instrument, condition_label, logged_ts);
CREATE INDEX IF NOT EXISTS ix_ee_state ON edge_evaluations(state);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class EdgeStore:
    def __init__(self, db_path: str | None = None):
        self.path = os.path.abspath(db_path or os.environ.get(
            "CHANAKYA_INSTITUTIONAL_EDGE_DB_PATH") or _DEFAULT_PATH)
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            c.commit()

    def _conn(self):
        c = sqlite3.connect(self.path, check_same_thread=False, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL;")
        return c

    def log_evaluation(self, *, instrument: str, condition_label: str, conditional_result,
                       ev_result, evidence, state: str, regime: str | None = None,
                       oos_result: dict | None = None) -> int:
        row = (
            _now_iso(), instrument, condition_label, regime,
            conditional_result.n_condition, conditional_result.n_baseline,
            conditional_result.p_condition, conditional_result.p_baseline,
            conditional_result.conditional_edge, conditional_result.edge_ci95_low,
            conditional_result.edge_ci95_high, conditional_result.mean_return_condition,
            conditional_result.median_return_condition, conditional_result.variance_return_condition,
            ev_result.win_rate, ev_result.avg_win_points, ev_result.avg_loss_points,
            ev_result.gross_ev_points, ev_result.cost_points, ev_result.net_ev_points,
            ev_result.risk_adjusted_ev, evidence.score, evidence.max_score,
            json.dumps(evidence.reasons), state,
            json.dumps(oos_result) if oos_result is not None else None,
        )
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO edge_evaluations (logged_ts, instrument, condition_label, regime, "
                "n_condition, n_baseline, p_condition, p_baseline, conditional_edge, edge_ci95_low, "
                "edge_ci95_high, mean_return, median_return, variance_return, win_rate, "
                "avg_win_points, avg_loss_points, gross_ev_points, cost_points, net_ev_points, "
                "risk_adjusted_ev, edge_score, edge_score_max, reason_codes_json, state, "
                "oos_result_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            c.commit()
            return int(cur.lastrowid)

    def history(self, instrument: str | None = None, condition_label: str | None = None, *,
                state: str | None = None, limit: int = 500) -> list[dict]:
        clauses, params = [], []
        if instrument is not None:
            clauses.append("instrument=?"); params.append(instrument)
        if condition_label is not None:
            clauses.append("condition_label=?"); params.append(condition_label)
        if state is not None:
            clauses.append("state=?"); params.append(state)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM edge_evaluations{where} ORDER BY id DESC LIMIT ?", params
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for key in ("reason_codes_json", "oos_result_json"):
                if d.get(key):
                    try:
                        d[key[:-5]] = json.loads(d[key])
                    except (ValueError, TypeError):
                        pass
            out.append(d)
        return out

    def latest(self, instrument: str, condition_label: str) -> dict | None:
        rows = self.history(instrument, condition_label, limit=1)
        return rows[0] if rows else None


_singleton: EdgeStore | None = None


def store() -> EdgeStore:
    global _singleton
    if _singleton is None:
        _singleton = EdgeStore()
    return _singleton
