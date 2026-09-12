"""
Append-only audit log for the Structural Break layer -- section I of the
spec: "The system must be able to answer WHY did the model decide the
previous model was no longer valid."

Lives in its OWN dedicated file (data/structural_break.db), not
market_history.db (histcap's capture domain) and not chanakya.db (the main
app DB used by live order execution). This is a decision-log for a shadow
analytical layer, not a derived-from-capture table like greek_exposure --
keeping it in its own file means it can never contend with, lock, or risk
either of those two databases, and it's trivially safe to point at a temp
file in tests (same `db_path` override convention as GreeksEngine/HistStore
elsewhere in this codebase).

Logs one row per STATE TRANSITION (not every evaluate() call -- most calls
don't transition state, and logging every single evaluation would make this
table enormous without adding audit value; the state machine's own
`BreakEvent`s already mark exactly the moments that matter).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "structural_break.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS structural_break_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT,
    old_model_id TEXT,
    current_regime TEXT,
    state_from TEXT NOT NULL,
    state_to TEXT NOT NULL,
    total_score INTEGER,
    max_possible_score INTEGER,
    category_scores_json TEXT,
    reason_codes_json TEXT,
    performance_metrics_json TEXT,
    drift_metrics_json TEXT,
    adaptation_observations INTEGER,
    candidate_model_id TEXT,
    validation_result_json TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS ix_sbe_symbol_ts ON structural_break_events(symbol, logged_ts);
CREATE INDEX IF NOT EXISTS ix_sbe_state_to  ON structural_break_events(state_to);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class LoggedEvent:
    id: int
    logged_ts: str
    symbol: str
    state_from: str
    state_to: str
    reason_codes: list
    total_score: int | None = None


class StructuralBreakAuditLog:
    def __init__(self, db_path: str | None = None):
        self.path = os.path.abspath(db_path or os.environ.get(
            "CHANAKYA_STRUCTURAL_BREAK_DB_PATH") or _DEFAULT_PATH)
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

    def log_transition(self, *, symbol: str, state_from: str, state_to: str,
                        evidence: dict, reason: str, timeframe: str | None = None,
                        old_model_id: str | None = None, current_regime: str | None = None,
                        performance_metrics: dict | None = None, drift_metrics: dict | None = None,
                        adaptation_observations: int | None = None,
                        candidate_model_id: str | None = None,
                        validation_result: dict | None = None) -> int:
        row = (
            _now_iso(), symbol, timeframe, old_model_id, current_regime, state_from, state_to,
            evidence.get("total_score"), evidence.get("max_possible"),
            json.dumps(evidence.get("category_scores") or {}),
            json.dumps(evidence.get("reasons") or []),
            json.dumps(performance_metrics) if performance_metrics is not None else None,
            json.dumps(drift_metrics) if drift_metrics is not None else None,
            adaptation_observations, candidate_model_id,
            json.dumps(validation_result) if validation_result is not None else None,
            reason,
        )
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO structural_break_events (logged_ts, symbol, timeframe, old_model_id, "
                "current_regime, state_from, state_to, total_score, max_possible_score, "
                "category_scores_json, reason_codes_json, performance_metrics_json, drift_metrics_json, "
                "adaptation_observations, candidate_model_id, validation_result_json, notes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            c.commit()
            return int(cur.lastrowid)

    def log_event(self, symbol: str, event, **extra) -> int:
        """Convenience: log a break_score.BreakEvent directly."""
        return self.log_transition(symbol=symbol, state_from=event.state_from,
                                    state_to=event.state_to, evidence=event.evidence,
                                    reason=event.reason, **extra)

    def history(self, symbol: str | None = None, *, state_to: str | None = None,
                limit: int = 500) -> list[dict]:
        clauses, params = [], []
        if symbol is not None:
            clauses.append("symbol=?"); params.append(symbol)
        if state_to is not None:
            clauses.append("state_to=?"); params.append(state_to)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM structural_break_events{where} ORDER BY id DESC LIMIT ?", params
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for key in ("category_scores_json", "reason_codes_json", "performance_metrics_json",
                        "drift_metrics_json", "validation_result_json"):
                if d.get(key):
                    try:
                        d[key[:-5]] = json.loads(d[key])
                    except (ValueError, TypeError):
                        pass
            out.append(d)
        return out

    def why(self, symbol: str, *, limit: int = 1) -> list[dict]:
        """Directly answers section I's question: 'why did the model decide
        the previous model was no longer valid' -- the most recent
        transition(s) INTO STRUCTURAL_BREAK for this symbol, with full
        evidence and reason codes attached."""
        return self.history(symbol, state_to="STRUCTURAL_BREAK", limit=limit)


_singleton: StructuralBreakAuditLog | None = None


def audit_log() -> StructuralBreakAuditLog:
    global _singleton
    if _singleton is None:
        _singleton = StructuralBreakAuditLog()
    return _singleton
