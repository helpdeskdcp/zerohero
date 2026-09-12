"""
app/structural_break/evaluator.py -- on-demand orchestrator wiring
performance_monitor + feature_drift + prediction_drift + break_score
together, persisting transitions to the audit log. fresh_db fixture for
scalp_signals; a temp-file audit log (never the shared data/structural_break.db).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.structural_break import evaluator  # noqa: E402
from app.structural_break.audit_log import StructuralBreakAuditLog  # noqa: E402


def _seed(db, n, *, win_rate=0.5, symbol="NIFTY", regime="TRENDING_UP",
          probability=0.6, points_win=10.0, points_loss=-8.0, mfe_win=12.0,
          mfe_loss=2.0, start_id=0, source="LIVE"):
    for i in range(n):
        is_win = (i % 10) < round(win_rate * 10)
        db.insert_scalp_signal({
            "signal_id": f"sig-{symbol}-{start_id + i}",
            "source": source, "status": "CLOSED", "symbol": symbol, "regime": regime,
            "probability": probability, "outcome": "WIN" if is_win else "LOSS",
            "points": points_win if is_win else points_loss,
            "mfe": mfe_win if is_win else mfe_loss,
            "created_ts": f"2026-09-01T00:{i:02d}:00Z",
            "resolved": 1,
        })


@pytest.fixture(autouse=True)
def isolated_log(tmp_path, monkeypatch):
    """Never touch the shared data/structural_break.db from tests, and never
    let one test's in-memory state machines leak into another."""
    log = StructuralBreakAuditLog(db_path=str(tmp_path / "sb_eval_test.db"))
    monkeypatch.setattr(evaluator, "audit_log", lambda: log)
    evaluator._machines.clear()
    yield log
    evaluator._machines.clear()


def test_evaluate_now_returns_full_report_shape(fresh_db):
    _seed(fresh_db, 30, win_rate=0.5)
    out = evaluator.evaluate_now("NIFTY")
    assert out["symbol"] == "NIFTY"
    assert out["scope_key"] == "NIFTY"
    assert out["state"] in ("NORMAL", "WATCH")
    assert "performance" in out and "feature_drift" in out and "prediction_drift" in out
    assert out["performance"]["short"]["n"] == 20


def test_scope_key_includes_regime_when_given(fresh_db):
    out = evaluator.evaluate_now("NIFTY", regime="TRENDING_UP")
    assert out["scope_key"] == "NIFTY:TRENDING_UP"


def test_repeated_calls_reuse_the_same_state_machine_instance(fresh_db):
    _seed(fresh_db, 30)
    evaluator.evaluate_now("NIFTY")
    out2 = evaluator.evaluate_now("NIFTY")
    assert out2["n_evaluations"] == 2   # hysteresis history accumulates across calls


def test_different_symbols_get_independent_state_machines(fresh_db):
    _seed(fresh_db, 30, symbol="NIFTY")
    _seed(fresh_db, 30, symbol="BANKNIFTY", start_id=1000)
    evaluator.evaluate_now("NIFTY")
    evaluator.evaluate_now("NIFTY")
    out_bn = evaluator.evaluate_now("BANKNIFTY")
    assert out_bn["n_evaluations"] == 1
    assert len(evaluator.tracked_scopes()) == 2


def test_a_state_transition_is_persisted_to_the_audit_log(fresh_db, isolated_log):
    # long healthy history, then a sharply degraded recent patch -- same
    # shape as performance_monitor.py's own "recent deterioration" test
    _seed(fresh_db, 100, win_rate=0.6, points_win=10.0, points_loss=-6.0)
    _seed(fresh_db, 20, win_rate=0.1, points_win=10.0, points_loss=-15.0, start_id=1000)
    out = evaluator.evaluate_now("NIFTY")
    assert out["state"] in ("WATCH", "DEGRADING")
    rows = isolated_log.history("NIFTY")
    assert len(rows) == 1
    assert rows[0]["state_from"] == "NORMAL"
    assert rows[0]["state_to"] == out["state"]
    assert rows[0]["performance_metrics"]["short"]["win_rate"] < 0.2


def test_tracked_scopes_reports_current_state(fresh_db):
    _seed(fresh_db, 30)
    evaluator.evaluate_now("NIFTY")
    scopes = evaluator.tracked_scopes()
    assert scopes[0]["scope_key"] == "NIFTY"
    assert scopes[0]["n_evaluations"] == 1
