"""
app/structural_break/audit_log.py -- append-only decision log, own dedicated
temp DB file per test (never the shared market_history.db or chanakya.db).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.structural_break.audit_log import StructuralBreakAuditLog  # noqa: E402
from app.structural_break.break_score import StructuralBreakStateMachine  # noqa: E402


@pytest.fixture
def log(tmp_path):
    return StructuralBreakAuditLog(db_path=str(tmp_path / "sb_test.db"))


def test_log_transition_and_read_back(log):
    log.log_transition(symbol="NIFTY", state_from="NORMAL", state_to="WATCH",
                        evidence={"total_score": 2, "max_possible": 16,
                                  "category_scores": {"performance": 2}, "reasons": ["X"]},
                        reason="score 2 >= WATCH_MIN 2")
    rows = log.history("NIFTY")
    assert len(rows) == 1
    assert rows[0]["state_from"] == "NORMAL" and rows[0]["state_to"] == "WATCH"
    assert rows[0]["reason_codes"] == ["X"]
    assert rows[0]["category_scores"] == {"performance": 2}


def test_symbol_isolation(tmp_path):
    log = StructuralBreakAuditLog(db_path=str(tmp_path / "sb.db"))
    log.log_transition(symbol="NIFTY", state_from="NORMAL", state_to="WATCH",
                        evidence={"total_score": 2, "reasons": []}, reason="r")
    log.log_transition(symbol="BANKNIFTY", state_from="NORMAL", state_to="WATCH",
                        evidence={"total_score": 2, "reasons": []}, reason="r")
    assert len(log.history("NIFTY")) == 1
    assert len(log.history("BANKNIFTY")) == 1
    assert len(log.history()) == 2


def test_why_answers_the_structural_break_question(log):
    log.log_transition(symbol="NIFTY", state_from="NORMAL", state_to="WATCH",
                        evidence={"total_score": 2, "reasons": ["A"]}, reason="r1")
    log.log_transition(symbol="NIFTY", state_from="WATCH", state_to="DEGRADING",
                        evidence={"total_score": 4, "reasons": ["A", "B"]}, reason="r2")
    log.log_transition(symbol="NIFTY", state_from="DEGRADING", state_to="STRUCTURAL_BREAK",
                        evidence={"total_score": 7, "category_scores": {"performance": 4, "prediction": 3},
                                  "reasons": ["A", "B", "C", "D"]},
                        reason="score >= 6 for 5 evals across 2 categories: ['performance', 'prediction']")
    why = log.why("NIFTY")
    assert len(why) == 1
    assert why[0]["state_to"] == "STRUCTURAL_BREAK"
    assert "categories" in why[0]["notes"]
    assert set(why[0]["reason_codes"]) == {"A", "B", "C", "D"}


def test_log_event_from_a_real_state_machine_transition(log):
    sm = StructuralBreakStateMachine()
    degraded_perf = {
        "short": {"status": "OK", "win_rate": 0.3, "expectancy_points": -2.0, "profit_factor": 0.6,
                  "consecutive_losses_current": 6},
        "medium": {"status": "OK"}, "long": {"status": "OK", "win_rate": 0.55,
                                             "expectancy_points": 2.0, "profit_factor": 1.3},
    }
    features = {"status": "OK", "features": {"pcr": {"triggered": True}}}
    predictions = {"status": "OK", "streams": {"calibration_error": {"triggered": True}}}
    out = sm.evaluate(degraded_perf, features, predictions)
    assert out["state"] == "WATCH"
    assert len(sm.events) == 1
    log.log_event("NIFTY", sm.events[0], performance_metrics=degraded_perf, drift_metrics=features)
    rows = log.history("NIFTY")
    assert len(rows) == 1 and rows[0]["state_to"] == "WATCH"
    assert rows[0]["performance_metrics"]["short"]["win_rate"] == 0.3


def test_persists_across_reopening_the_same_file(tmp_path):
    path = str(tmp_path / "persist.db")
    log1 = StructuralBreakAuditLog(db_path=path)
    log1.log_transition(symbol="NIFTY", state_from="NORMAL", state_to="WATCH",
                        evidence={"total_score": 2, "reasons": []}, reason="r")
    log2 = StructuralBreakAuditLog(db_path=path)   # simulate a process restart
    assert len(log2.history("NIFTY")) == 1
