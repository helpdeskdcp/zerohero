"""
app/institutional_edge/evaluator.py -- on-demand orchestrator wiring
conditional_edge + ev + edge_score + store together. fresh_db fixture for
scalp_signals; a temp-file store (never data/institutional_edge.db).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.institutional_edge import evaluator  # noqa: E402
from app.institutional_edge.store import EdgeStore  # noqa: E402


def _seed(db, n, *, win_rate=0.5, symbol="NATURALGAS", regime="TRENDING_UP",
          probability=0.6, points_win=10.0, points_loss=-8.0, start_id=0, source="LIVE"):
    for i in range(n):
        is_win = ((start_id + i) % 10) < round(win_rate * 10)
        db.insert_scalp_signal({
            "signal_id": f"sig-{symbol}-{start_id + i}",
            "source": source, "status": "CLOSED", "symbol": symbol, "regime": regime,
            "probability": probability, "outcome": "WIN" if is_win else "LOSS",
            "points": points_win if is_win else points_loss,
            "created_ts": f"2026-09-01T00:{i % 60:02d}:00Z",
            "resolved": 1,
        })


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    s = EdgeStore(db_path=str(tmp_path / "ie_eval_test.db"))
    monkeypatch.setattr(evaluator, "store", lambda: s)
    evaluator._machines.clear()
    yield s
    evaluator._machines.clear()


def test_evaluate_now_returns_full_report_shape(fresh_db):
    _seed(fresh_db, 60, win_rate=0.6, regime="TRENDING_UP")
    out = evaluator.evaluate_now("NATURALGAS", "regime==TRENDING_UP")
    assert out["instrument"] == "NATURALGAS"
    assert out["condition_label"] == "regime==TRENDING_UP"
    assert "conditional_edge" in out and "ev" in out
    assert out["state"] in ("CANDIDATE", "VALIDATED", "ACTIVE")


def test_ev_is_computed_on_the_conditional_subset_not_the_whole_baseline(fresh_db):
    # TRENDING_UP rows are strong winners, RANGE rows are weak -- EV for the
    # TRENDING_UP condition should reflect ONLY the TRENDING_UP subset
    _seed(fresh_db, 60, win_rate=0.9, points_win=20.0, points_loss=-5.0,
          regime="TRENDING_UP", start_id=0)
    _seed(fresh_db, 60, win_rate=0.1, points_win=5.0, points_loss=-20.0,
          regime="RANGE", start_id=60)
    out = evaluator.evaluate_now("NATURALGAS", "regime==TRENDING_UP")
    assert out["ev"]["win_rate"] > 0.8   # dominated by the TRENDING_UP subset alone


def test_unknown_instrument_reports_uncalibrated_cost_but_still_computes_edge(fresh_db):
    _seed(fresh_db, 60, win_rate=0.6, symbol="NIFTY", regime="TRENDING_UP")
    out = evaluator.evaluate_now("NIFTY", "regime==TRENDING_UP")
    assert out["ev"]["status"] == "UNCALIBRATED_COST"
    assert out["conditional_edge"]["status"] == "OK"   # edge math doesn't need a cost profile


def test_repeated_calls_reuse_the_same_state_machine_instance(fresh_db):
    _seed(fresh_db, 60, win_rate=0.6, regime="TRENDING_UP")
    evaluator.evaluate_now("NATURALGAS", "regime==TRENDING_UP")
    out2 = evaluator.evaluate_now("NATURALGAS", "regime==TRENDING_UP")
    assert out2["n_evaluations"] == 2


def test_different_conditions_get_independent_trackers(fresh_db):
    _seed(fresh_db, 60, win_rate=0.6, regime="TRENDING_UP")
    evaluator.evaluate_now("NATURALGAS", "regime==TRENDING_UP")
    out = evaluator.evaluate_now("NATURALGAS", "regime==RANGE")
    assert out["n_evaluations"] == 1
    assert len(evaluator.tracked_edges()) == 2


def test_every_evaluation_is_persisted_to_the_store(fresh_db, isolated_store):
    _seed(fresh_db, 60, win_rate=0.6, regime="TRENDING_UP")
    evaluator.evaluate_now("NATURALGAS", "regime==TRENDING_UP")
    evaluator.evaluate_now("NATURALGAS", "regime==TRENDING_UP")
    rows = isolated_store.history("NATURALGAS", "regime==TRENDING_UP")
    assert len(rows) == 2


def test_unknown_condition_label_raises_a_clear_error(fresh_db):
    _seed(fresh_db, 60)
    with pytest.raises(KeyError, match="unknown condition"):
        evaluator.evaluate_now("NATURALGAS", "not_a_real_condition")
