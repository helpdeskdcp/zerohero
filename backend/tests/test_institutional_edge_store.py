"""
app/institutional_edge/store.py -- own dedicated temp DB file per test
(never data/institutional_edge.db).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.institutional_edge.conditional_edge import ConditionalEdgeResult  # noqa: E402
from app.institutional_edge.edge_score import EdgeEvidence  # noqa: E402
from app.institutional_edge.ev import EVResult  # noqa: E402
from app.institutional_edge.store import EdgeStore  # noqa: E402


@pytest.fixture
def edge_store(tmp_path):
    return EdgeStore(db_path=str(tmp_path / "ie_test.db"))


_COND = ConditionalEdgeResult(status="OK", condition_label="regime==TRENDING_UP", n_condition=250,
                              n_baseline=500, p_condition=0.62, p_baseline=0.50,
                              conditional_edge=0.12, edge_ci95_low=0.03, edge_ci95_high=0.21,
                              mean_return_condition=3.1, median_return_condition=2.5,
                              variance_return_condition=15.2)
_EV = EVResult(status="OK", n=250, win_rate=0.62, avg_win_points=10.0, avg_loss_points=8.0,
              gross_ev_points=3.16, cost_points=0.09, net_ev_points=3.07, risk_adjusted_ev=0.38)
_EVIDENCE = EdgeEvidence(score=4, max_score=4, reasons=["EDGE_CI95_EXCLUDES_ZERO", "NET_EV_POSITIVE"])


def test_log_and_read_back_one_evaluation(edge_store):
    edge_store.log_evaluation(instrument="NATURALGAS", condition_label="regime==TRENDING_UP",
                              conditional_result=_COND, ev_result=_EV, evidence=_EVIDENCE,
                              state="ACTIVE", regime="TRENDING_UP")
    rows = edge_store.history("NATURALGAS", "regime==TRENDING_UP")
    assert len(rows) == 1
    r = rows[0]
    assert r["n_condition"] == 250 and r["p_condition"] == 0.62
    assert r["net_ev_points"] == 3.07 and r["state"] == "ACTIVE"
    assert r["reason_codes"] == ["EDGE_CI95_EXCLUDES_ZERO", "NET_EV_POSITIVE"]
    assert r.get("oos_result") is None   # honestly absent, never fabricated


def test_instrument_and_condition_isolation(edge_store):
    edge_store.log_evaluation(instrument="NATURALGAS", condition_label="A",
                              conditional_result=_COND, ev_result=_EV, evidence=_EVIDENCE, state="ACTIVE")
    edge_store.log_evaluation(instrument="CRUDEOIL", condition_label="A",
                              conditional_result=_COND, ev_result=_EV, evidence=_EVIDENCE, state="CANDIDATE")
    edge_store.log_evaluation(instrument="NATURALGAS", condition_label="B",
                              conditional_result=_COND, ev_result=_EV, evidence=_EVIDENCE, state="CANDIDATE")
    assert len(edge_store.history("NATURALGAS", "A")) == 1
    assert len(edge_store.history("CRUDEOIL", "A")) == 1
    assert len(edge_store.history("NATURALGAS", "B")) == 1
    assert len(edge_store.history("NATURALGAS")) == 2
    assert len(edge_store.history()) == 3


def test_history_can_filter_by_state(edge_store):
    edge_store.log_evaluation(instrument="X", condition_label="A", conditional_result=_COND,
                              ev_result=_EV, evidence=_EVIDENCE, state="CANDIDATE")
    edge_store.log_evaluation(instrument="X", condition_label="A", conditional_result=_COND,
                              ev_result=_EV, evidence=_EVIDENCE, state="ACTIVE")
    active_only = edge_store.history("X", "A", state="ACTIVE")
    assert len(active_only) == 1 and active_only[0]["state"] == "ACTIVE"


def test_latest_returns_the_most_recent_row(edge_store):
    edge_store.log_evaluation(instrument="X", condition_label="A", conditional_result=_COND,
                              ev_result=_EV, evidence=_EVIDENCE, state="CANDIDATE")
    edge_store.log_evaluation(instrument="X", condition_label="A", conditional_result=_COND,
                              ev_result=_EV, evidence=_EVIDENCE, state="VALIDATED")
    latest = edge_store.latest("X", "A")
    assert latest["state"] == "VALIDATED"


def test_latest_is_none_when_nothing_logged(edge_store):
    assert edge_store.latest("NOPE", "NOPE") is None


def test_oos_result_stored_when_provided(edge_store):
    edge_store.log_evaluation(instrument="X", condition_label="A", conditional_result=_COND,
                              ev_result=_EV, evidence=_EVIDENCE, state="ACTIVE",
                              oos_result={"verdict": "CONFIRMED", "n": 100})
    row = edge_store.latest("X", "A")
    assert row["oos_result"] == {"verdict": "CONFIRMED", "n": 100}


def test_persists_across_reopening_the_same_file(tmp_path):
    path = str(tmp_path / "persist.db")
    s1 = EdgeStore(db_path=path)
    s1.log_evaluation(instrument="X", condition_label="A", conditional_result=_COND,
                      ev_result=_EV, evidence=_EVIDENCE, state="CANDIDATE")
    s2 = EdgeStore(db_path=path)
    assert len(s2.history("X", "A")) == 1
