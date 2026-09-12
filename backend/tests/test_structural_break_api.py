"""
app/structural_break/api.py -- read-only routes. Direct handler calls (this
repo's convention for FastAPI route testing, e.g. test_optionchain_api.py).
fresh_db fixture for scalp_signals; a temp-file audit log (never the shared
data/structural_break.db).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.structural_break import api, evaluator  # noqa: E402
from app.structural_break.audit_log import StructuralBreakAuditLog  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_log(tmp_path, monkeypatch):
    log = StructuralBreakAuditLog(db_path=str(tmp_path / "sb_api_test.db"))
    monkeypatch.setattr(evaluator, "audit_log", lambda: log)
    monkeypatch.setattr(api, "audit_log", lambda: log)
    evaluator._machines.clear()
    yield log
    evaluator._machines.clear()


def _seed(db, n, *, win_rate=0.5, symbol="NIFTY", probability=0.6,
          points_win=10.0, points_loss=-8.0, start_id=0):
    for i in range(n):
        is_win = (i % 10) < round(win_rate * 10)
        db.insert_scalp_signal({
            "signal_id": f"sig-{symbol}-{start_id + i}", "source": "LIVE", "status": "CLOSED",
            "symbol": symbol, "probability": probability, "outcome": "WIN" if is_win else "LOSS",
            "points": points_win if is_win else points_loss, "mfe": 1.0,
            "created_ts": f"2026-09-01T00:{i:02d}:00Z", "resolved": 1,
        })


def test_status_starts_empty_before_any_evaluation():
    assert api.status() == {"tracked": []}


def test_status_symbol_evaluates_and_registers_the_scope(fresh_db):
    _seed(fresh_db, 30)
    out = api.status_symbol("NIFTY", regime=None, source="LIVE")
    assert out["symbol"] == "NIFTY"
    assert api.status() == {
        "tracked": [{"scope_key": "NIFTY", "state": out["state"], "n_evaluations": 1}]
    }


def test_status_symbol_never_touches_execution_or_broker_state(fresh_db):
    """Sanity check on the hard constraint: the report has no order/execution
    fields, and calling it twice is side-effect-free beyond this layer's own
    in-memory registry + audit log."""
    _seed(fresh_db, 30)
    out = api.status_symbol("NIFTY")
    forbidden_keys = {"order_id", "broker", "execution", "credentials"}
    assert forbidden_keys.isdisjoint(out.keys())


def test_history_reads_from_the_audit_log(isolated_log):
    isolated_log.log_transition(symbol="NIFTY", state_from="NORMAL", state_to="WATCH",
                                 evidence={"total_score": 3, "reasons": ["X"]}, reason="r")
    out = api.history(symbol="NIFTY", state_to=None, limit=200)
    assert len(out["events"]) == 1
    assert out["events"][0]["state_to"] == "WATCH"


def test_history_can_filter_by_state_to(isolated_log):
    isolated_log.log_transition(symbol="NIFTY", state_from="NORMAL", state_to="WATCH",
                                 evidence={"total_score": 3, "reasons": []}, reason="r1")
    isolated_log.log_transition(symbol="NIFTY", state_from="WATCH", state_to="DEGRADING",
                                 evidence={"total_score": 5, "reasons": []}, reason="r2")
    out = api.history(symbol="NIFTY", state_to="DEGRADING", limit=200)
    assert len(out["events"]) == 1
    assert out["events"][0]["state_to"] == "DEGRADING"


def test_events_endpoint_answers_why(isolated_log):
    isolated_log.log_transition(symbol="NIFTY", state_from="DEGRADING", state_to="STRUCTURAL_BREAK",
                                 evidence={"total_score": 7, "reasons": ["A", "B"]}, reason="r")
    out = api.why("NIFTY", limit=5)
    assert out["symbol"] == "NIFTY"
    assert len(out["structural_break_events"]) == 1
    assert out["structural_break_events"][0]["state_to"] == "STRUCTURAL_BREAK"


def test_events_endpoint_empty_when_no_structural_break_yet(isolated_log):
    isolated_log.log_transition(symbol="NIFTY", state_from="NORMAL", state_to="WATCH",
                                 evidence={"total_score": 3, "reasons": []}, reason="r")
    out = api.why("NIFTY", limit=5)
    assert out["structural_break_events"] == []
