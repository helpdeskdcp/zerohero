"""
app/institutional_edge/edge_score.py -- evidence scoring + lifecycle state
machine. Pure in-memory, no DB.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.institutional_edge.conditional_edge import ConditionalEdgeResult  # noqa: E402
from app.institutional_edge.edge_score import (  # noqa: E402
    EdgeStateMachine, compute_evidence,
)
from app.institutional_edge.ev import EVResult  # noqa: E402

HEALTHY_COND = ConditionalEdgeResult(
    status="OK", condition_label="x", n_condition=250, n_baseline=500,
    p_condition=0.62, p_baseline=0.50, conditional_edge=0.12,
    edge_ci95_low=0.03, edge_ci95_high=0.21)
HEALTHY_EV = EVResult(status="OK", n=250, win_rate=0.62, avg_win_points=10.0,
                      avg_loss_points=8.0, gross_ev_points=3.16, cost_points=0.09,
                      net_ev_points=3.07, risk_adjusted_ev=0.38)

WEAK_COND = ConditionalEdgeResult(
    status="OK", condition_label="x", n_condition=40, n_baseline=500,
    p_condition=0.50, p_baseline=0.50, conditional_edge=0.0,
    edge_ci95_low=-0.08, edge_ci95_high=0.08)
WEAK_EV = EVResult(status="OK", n=40, win_rate=0.50, avg_win_points=8.0,
                   avg_loss_points=8.0, gross_ev_points=0.0, cost_points=0.09,
                   net_ev_points=-0.09, risk_adjusted_ev=-0.01)

MODERATE_COND = ConditionalEdgeResult(
    status="OK", condition_label="x", n_condition=40, n_baseline=500,
    p_condition=0.52, p_baseline=0.50, conditional_edge=0.02,
    edge_ci95_low=-0.05, edge_ci95_high=0.09)
MODERATE_EV = EVResult(status="OK", n=40, win_rate=0.52, avg_win_points=8.0,
                       avg_loss_points=8.0, gross_ev_points=0.5, cost_points=0.09,
                       net_ev_points=0.5, risk_adjusted_ev=0.03)   # score == 1 (only NET_EV_POSITIVE)

DEAD_COND = ConditionalEdgeResult(
    status="OK", condition_label="x", n_condition=40, n_baseline=500,
    p_condition=0.30, p_baseline=0.50, conditional_edge=-0.20,
    edge_ci95_low=-0.30, edge_ci95_high=-0.10)
DEAD_EV = EVResult(status="OK", n=40, win_rate=0.30, avg_win_points=8.0,
                   avg_loss_points=10.0, gross_ev_points=-4.6, cost_points=0.09,
                   net_ev_points=-4.69, risk_adjusted_ev=-0.47)

INSUFFICIENT_COND = ConditionalEdgeResult(status="INSUFFICIENT_DATA", condition_label="x",
                                          n_condition=5, n_baseline=500)
INSUFFICIENT_EV = EVResult(status="INSUFFICIENT_DATA", n=5)


def _drive(sm, cond, ev, n):
    out = None
    for _ in range(n):
        out = sm.evaluate(cond, ev)
    return out


# --------------------------------------------------------------------------- #
#  compute_evidence (pure, single-call)                                       #
# --------------------------------------------------------------------------- #
def test_healthy_evidence_scores_all_four_checks():
    ev = compute_evidence(HEALTHY_COND, HEALTHY_EV)
    assert ev.score == 4
    assert set(ev.reasons) == {"EDGE_CI95_EXCLUDES_ZERO", "NET_EV_POSITIVE",
                               "RISK_ADJUSTED_EV_ABOVE_0.1R", "SAMPLE_SIZE_ROBUST_250"}


def test_weak_evidence_scores_zero():
    ev = compute_evidence(WEAK_COND, WEAK_EV)
    assert ev.score == 0


def test_dead_evidence_scores_zero_not_negative():
    ev = compute_evidence(DEAD_COND, DEAD_EV)
    assert ev.score == 0


# --------------------------------------------------------------------------- #
#  State machine hysteresis                                                   #
# --------------------------------------------------------------------------- #
def test_a_single_good_reading_does_not_validate_immediately():
    sm = EdgeStateMachine()
    out = sm.evaluate(HEALTHY_COND, HEALTHY_EV)
    assert out["state"] == "CANDIDATE"


def test_sustained_healthy_evidence_progresses_to_active():
    sm = EdgeStateMachine()
    states = [_drive(sm, HEALTHY_COND, HEALTHY_EV, 1)["state"] for _ in range(10)]
    assert "VALIDATED" in states
    assert states[-1] == "ACTIVE"


def test_weak_evidence_from_the_start_stays_candidate_forever():
    sm = EdgeStateMachine()
    out = _drive(sm, WEAK_COND, WEAK_EV, 20)
    assert out["state"] == "CANDIDATE"


def test_insufficient_data_never_forces_a_transition():
    sm = EdgeStateMachine()
    out = _drive(sm, INSUFFICIENT_COND, INSUFFICIENT_EV, 20)
    assert out["state"] == "CANDIDATE"
    assert len(sm.events) == 0


def test_active_edge_that_weakens_then_collapses_moves_to_decaying_to_invalid():
    sm = EdgeStateMachine()
    _drive(sm, HEALTHY_COND, HEALTHY_EV, 10)
    assert sm.state == "ACTIVE"
    # score 1 (moderate) sustained -> WEAKENING first
    states_seen = [sm.evaluate(MODERATE_COND, MODERATE_EV)["state"] for _ in range(3)]
    assert states_seen[-1] == "WEAKENING"
    # then a full collapse to 0 sustained -> DECAYING -> INVALID
    for _ in range(20):
        sm.evaluate(DEAD_COND, DEAD_EV)
        if sm.state == "INVALID":
            break
    assert sm.state == "INVALID"


def test_a_sharp_collapse_from_active_can_skip_straight_to_decaying():
    sm = EdgeStateMachine()
    _drive(sm, HEALTHY_COND, HEALTHY_EV, 10)
    assert sm.state == "ACTIVE"
    out = _drive(sm, DEAD_COND, DEAD_EV, 3)   # score 0 immediately, no WEAKENING stop
    assert out["state"] == "DECAYING"


def test_recovery_from_weakening_back_to_active():
    sm = EdgeStateMachine()
    _drive(sm, HEALTHY_COND, HEALTHY_EV, 10)
    assert sm.state == "ACTIVE"
    _drive(sm, MODERATE_COND, MODERATE_EV, 3)
    assert sm.state == "WEAKENING"
    out = _drive(sm, HEALTHY_COND, HEALTHY_EV, 3)
    assert out["state"] == "ACTIVE"


def test_validated_but_not_yet_active_can_fall_back_to_candidate():
    sm = EdgeStateMachine()
    _drive(sm, HEALTHY_COND, HEALTHY_EV, 3)
    assert sm.state == "VALIDATED"
    out = sm.evaluate(WEAK_COND, WEAK_EV)
    assert out["state"] == "CANDIDATE"


def test_every_transition_is_logged_as_an_event():
    sm = EdgeStateMachine()
    _drive(sm, HEALTHY_COND, HEALTHY_EV, 10)
    assert len(sm.events) >= 2   # at least CANDIDATE->VALIDATED->ACTIVE
    for ev in sm.events:
        assert ev.state_from != ev.state_to
        assert ev.reason
        assert "score" in ev.evidence
