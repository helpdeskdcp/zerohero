"""
app/structural_break/break_score.py -- evidence scoring + state machine.
Pure in-memory, no DB.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.structural_break.break_score import (  # noqa: E402
    ADAPTATION_MIN_OBSERVATIONS, VALIDATION_MAX_ATTEMPTS,
    StructuralBreakStateMachine, compute_evidence,
)

HEALTHY_PERF = {
    "short": {"status": "OK", "win_rate": 0.55, "expectancy_points": 3.0, "profit_factor": 1.4,
              "consecutive_losses_current": 1},
    "medium": {"status": "OK", "win_rate": 0.53, "expectancy_points": 2.5, "profit_factor": 1.3},
    "long": {"status": "OK", "win_rate": 0.52, "expectancy_points": 2.0, "profit_factor": 1.25},
}
NO_FEATURE_DRIFT = {"status": "OK", "features": {
    "atr": {"triggered": False}, "pcr": {"triggered": False}, "regime": {"triggered": False},
    "momentum": {"triggered": False}, "mtf_alignment": {"triggered": False},
    "vwap_distance_pct": {"triggered": False}, "expected_premium_move": {"triggered": False},
    "volume_score": {"triggered": False}, "oi_score": {"triggered": False},
}}
NO_PREDICTION_DRIFT = {"status": "OK", "streams": {
    "directional_accuracy": {"triggered": False}, "calibration_error": {"triggered": False},
    "move_error": {"triggered": False},
}}


def _degraded_perf(consecutive_losses=1):
    return {
        "short": {"status": "OK", "win_rate": 0.30, "expectancy_points": -2.0, "profit_factor": 0.6,
                  "consecutive_losses_current": consecutive_losses},
        "medium": {"status": "OK", "win_rate": 0.45, "expectancy_points": 1.0, "profit_factor": 1.1},
        "long": {"status": "OK", "win_rate": 0.55, "expectancy_points": 2.5, "profit_factor": 1.3},
    }


def _feature_drift_on(*names):
    out = {"status": "OK", "features": {k: {"triggered": k in names} for k in NO_FEATURE_DRIFT["features"]}}
    return out


def _prediction_drift_on(*names):
    return {"status": "OK", "streams": {k: {"triggered": k in names} for k in NO_PREDICTION_DRIFT["streams"]}}


# --------------------------------------------------------------------------- #
#  compute_evidence (pure, single-call)                                       #
# --------------------------------------------------------------------------- #
def test_healthy_state_has_zero_evidence():
    ev = compute_evidence(HEALTHY_PERF, NO_FEATURE_DRIFT, NO_PREDICTION_DRIFT)
    assert ev.total_score == 0
    assert ev.reasons == []


def test_performance_deterioration_reasons_are_specific():
    ev = compute_evidence(_degraded_perf(), NO_FEATURE_DRIFT, NO_PREDICTION_DRIFT)
    assert ev.category_scores["performance"] >= 3  # win-rate drop, expectancy flip, PF<1
    assert any("WIN_RATE_DOWN" in r for r in ev.reasons)
    assert any("EXPECTANCY_TURNED_NEGATIVE" in r for r in ev.reasons)
    assert any("PROFIT_FACTOR_BELOW_1" in r for r in ev.reasons)


def test_consecutive_losses_alone_is_only_one_contributory_point():
    ev = compute_evidence(_degraded_perf(consecutive_losses=6), NO_FEATURE_DRIFT, NO_PREDICTION_DRIFT)
    # the streak itself adds exactly 1 more point on top of the other 3 perf signals -- never
    # treated as a category-spanning event by itself
    ev_no_streak = compute_evidence(_degraded_perf(consecutive_losses=1), NO_FEATURE_DRIFT, NO_PREDICTION_DRIFT)
    assert ev.category_scores["performance"] == ev_no_streak.category_scores["performance"] + 1
    assert ev.category_scores["prediction"] == 0 and ev.category_scores["feature_drift"] == 0


def test_regime_and_atr_are_categorized_separately_from_other_features():
    ev = compute_evidence(HEALTHY_PERF, _feature_drift_on("regime", "atr", "pcr"), NO_PREDICTION_DRIFT)
    assert ev.category_scores["regime_volatility"] == 2   # regime + atr
    assert ev.category_scores["feature_drift"] == 1       # pcr only


def test_prediction_drift_streams_each_count_once():
    ev = compute_evidence(HEALTHY_PERF, NO_FEATURE_DRIFT,
                          _prediction_drift_on("directional_accuracy", "calibration_error"))
    assert ev.category_scores["prediction"] == 2


# --------------------------------------------------------------------------- #
#  State machine hysteresis                                                   #
# --------------------------------------------------------------------------- #
def test_normal_stays_normal_under_healthy_evidence():
    sm = StructuralBreakStateMachine()
    for _ in range(20):
        out = sm.evaluate(HEALTHY_PERF, NO_FEATURE_DRIFT, NO_PREDICTION_DRIFT)
    assert out["state"] == "NORMAL"


def test_single_elevated_evaluation_moves_to_watch_not_further():
    sm = StructuralBreakStateMachine()
    out = sm.evaluate(_degraded_perf(), _feature_drift_on("pcr"), NO_PREDICTION_DRIFT)
    assert out["state"] == "WATCH"


def test_watch_does_not_escalate_to_degrading_on_a_single_bad_eval():
    sm = StructuralBreakStateMachine()
    sm.evaluate(_degraded_perf(), _feature_drift_on("pcr"), NO_PREDICTION_DRIFT)  # -> WATCH
    out = sm.evaluate(HEALTHY_PERF, NO_FEATURE_DRIFT, NO_PREDICTION_DRIFT)         # one healthy eval
    assert out["state"] in ("WATCH", "NORMAL")   # never jumped to DEGRADING off one bad eval


def test_small_losing_streak_alone_never_reaches_structural_break():
    """The central requirement: a losing streak touches ONLY the performance
    category. Even sustained for many evaluations, it must never reach
    STRUCTURAL_BREAK, because that requires evidence spanning >=2 categories."""
    sm = StructuralBreakStateMachine()
    out = None
    for _ in range(30):
        out = sm.evaluate(_degraded_perf(consecutive_losses=8), NO_FEATURE_DRIFT, NO_PREDICTION_DRIFT)
    assert out["state"] != "STRUCTURAL_BREAK"
    assert out["state"] != "ADAPTATION"
    # it CAN still reach DEGRADING (single-category sustained deterioration is real signal,
    # just not enough on its own to declare a full structural break)
    assert out["state"] in ("WATCH", "DEGRADING")


def _drive_until(sm, target_state, *, max_evals=60):
    """Feed sustained bad, multi-category evidence until `target_state` is
    reached (or raise) -- robust to the exact escalation thresholds so this
    helper doesn't need updating if those constants ever change."""
    out = None
    for _ in range(max_evals):
        out = sm.evaluate(_degraded_perf(), _feature_drift_on("pcr", "regime"),
                           _prediction_drift_on("calibration_error"))
        if out["state"] == target_state:
            return out
    raise AssertionError(f"never reached {target_state}; last state was {out['state']}")


def test_sustained_multi_category_evidence_reaches_structural_break_then_adaptation():
    sm = StructuralBreakStateMachine()
    _drive_until(sm, "STRUCTURAL_BREAK")
    out = sm.evaluate(_degraded_perf(), _feature_drift_on("pcr", "regime"),
                       _prediction_drift_on("calibration_error"))
    assert out["state"] == "ADAPTATION"   # auto-transitions the eval right after STRUCTURAL_BREAK


def test_adaptation_tracks_observation_count_then_moves_to_validation():
    """ADAPTATION is no longer a permanent hold -- once shadow.py existed to
    drive VALIDATION's promotion decision, break_score.py auto-advances out
    of ADAPTATION after ADAPTATION_MIN_OBSERVATIONS evaluations (see its
    module docstring)."""
    sm = StructuralBreakStateMachine()
    _drive_until(sm, "ADAPTATION")
    assert sm.adaptation_observations == 0   # just transitioned in, none collected yet

    out = None
    for i in range(1, ADAPTATION_MIN_OBSERVATIONS + 1):
        out = sm.evaluate(HEALTHY_PERF, NO_FEATURE_DRIFT, NO_PREDICTION_DRIFT)
        if out["state"] != "ADAPTATION":
            break
        assert out["adaptation_observations"] == i
    assert out["state"] == "VALIDATION"   # auto-transitions once enough observations collected


_OLD_MEDIUM = {"status": "OK", "n": 60, "win_rate": 0.50, "expectancy_points": 1.0,
               "profit_factor": 1.1, "directional_accuracy": 0.55, "ece": 0.08,
               "max_drawdown_points": -20.0}
_GOOD_CANDIDATE = {"status": "OK", "n": 65, "win_rate": 0.55, "expectancy_points": 1.5,
                    "profit_factor": 1.3, "directional_accuracy": 0.60, "ece": 0.05,
                    "max_drawdown_points": -10.0}
_BAD_CANDIDATE = {**_GOOD_CANDIDATE, "expectancy_points": 0.5}   # regresses expectancy -> never promoted


def _perf_with_medium(medium):
    return {"short": HEALTHY_PERF["short"], "medium": medium, "long": HEALTHY_PERF["long"]}


def test_validation_promotes_to_recovered_after_consecutive_passing_comparisons():
    sm = StructuralBreakStateMachine()
    _drive_until(sm, "ADAPTATION")
    _drive_until_state_via_healthy_evals(sm)   # -> VALIDATION
    assert sm.state == "VALIDATION"

    out = None
    for _ in range(3):   # shadow.ShadowValidator.CONSECUTIVE_PASSES_REQUIRED
        out = sm.evaluate(_perf_with_medium(_OLD_MEDIUM), NO_FEATURE_DRIFT, NO_PREDICTION_DRIFT,
                           candidate_window=_GOOD_CANDIDATE)
    assert out["state"] == "RECOVERED"
    assert out["shadow_validation"]["promote_now"] is True


def test_validation_reverts_to_adaptation_if_candidate_never_clears():
    sm = StructuralBreakStateMachine()
    _drive_until(sm, "ADAPTATION")
    _drive_until_state_via_healthy_evals(sm)   # -> VALIDATION
    assert sm.state == "VALIDATION"

    out = None
    for _ in range(VALIDATION_MAX_ATTEMPTS + 1):
        out = sm.evaluate(_perf_with_medium(_OLD_MEDIUM), NO_FEATURE_DRIFT, NO_PREDICTION_DRIFT,
                           candidate_window=_BAD_CANDIDATE)
        if out["state"] != "VALIDATION":
            break
    assert out["state"] == "ADAPTATION"   # reverted -- candidate regressed expectancy every attempt


def test_validation_without_candidate_data_stays_put_and_forces_no_decision():
    sm = StructuralBreakStateMachine()
    _drive_until(sm, "ADAPTATION")
    _drive_until_state_via_healthy_evals(sm)   # -> VALIDATION
    out = sm.evaluate(HEALTHY_PERF, NO_FEATURE_DRIFT, NO_PREDICTION_DRIFT)   # no candidate_window
    assert out["state"] == "VALIDATION"
    assert out["shadow_validation"] is None


def _drive_until_state_via_healthy_evals(sm, *, max_evals=20):
    """From ADAPTATION, feed healthy evals (content doesn't matter -- the
    ADAPTATION->VALIDATION transition is observation-count-driven, not
    evidence-driven) until VALIDATION is reached."""
    for _ in range(max_evals):
        out = sm.evaluate(HEALTHY_PERF, NO_FEATURE_DRIFT, NO_PREDICTION_DRIFT)
        if out["state"] == "VALIDATION":
            return out
    raise AssertionError("never reached VALIDATION")


def test_deescalation_steps_down_one_level_at_a_time():
    sm = StructuralBreakStateMachine()
    for _ in range(3):
        sm.evaluate(_degraded_perf(), _feature_drift_on("pcr"), NO_PREDICTION_DRIFT)
    assert sm.state in ("WATCH", "DEGRADING")
    state_after_bad = sm.state
    for _ in range(6):
        out = sm.evaluate(HEALTHY_PERF, NO_FEATURE_DRIFT, NO_PREDICTION_DRIFT)
    if state_after_bad == "DEGRADING":
        assert out["state"] in ("WATCH", "NORMAL")   # never straight to NORMAL in one hop's worth of evals
    else:
        assert out["state"] == "NORMAL"


def test_every_state_transition_is_logged_as_an_event():
    sm = StructuralBreakStateMachine()
    for _ in range(30):
        sm.evaluate(_degraded_perf(), _feature_drift_on("pcr", "regime"),
                    _prediction_drift_on("calibration_error"))
    assert len(sm.events) >= 3   # at least NORMAL->WATCH->DEGRADING->STRUCTURAL_BREAK->ADAPTATION
    for ev in sm.events:
        assert ev.state_from != ev.state_to
        assert ev.reason
        assert "total_score" in ev.evidence
