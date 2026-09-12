"""
app/structural_break/shadow.py -- old-vs-candidate comparison + promotion
decision. Pure in-memory, no DB.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.structural_break.shadow import (  # noqa: E402
    MIN_SHADOW_N, ShadowValidator, compare,
)

_OLD = {"status": "OK", "n": 60, "win_rate": 0.50, "expectancy_points": 1.0,
        "profit_factor": 1.1, "directional_accuracy": 0.55, "ece": 0.08,
        "max_drawdown_points": -20.0}


def _candidate(n=MIN_SHADOW_N, **overrides):
    base = {"status": "OK", "n": n, "win_rate": 0.55, "expectancy_points": 1.5,
            "profit_factor": 1.3, "directional_accuracy": 0.60, "ece": 0.05,
            "max_drawdown_points": -10.0}
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
#  compare()                                                                   #
# --------------------------------------------------------------------------- #
def test_candidate_better_on_every_metric_is_promoted():
    cmp = compare(_OLD, _candidate())
    assert cmp.status == "OK"
    assert cmp.should_promote is True
    assert cmp.expectancy_regressed is False
    assert cmp.metrics_better_or_equal == 5


def test_expectancy_regression_always_rejects_regardless_of_other_metrics():
    # every OTHER metric improved, only expectancy got worse
    cand = _candidate(expectancy_points=0.5)
    cmp = compare(_OLD, cand)
    assert cmp.expectancy_regressed is True
    assert cmp.should_promote is False
    assert "REJECTED" in cmp.reason


def test_minority_of_metrics_better_is_rejected_even_without_expectancy_regression():
    # expectancy/pf improve (2 of 5), but directional_accuracy/ece/drawdown all regress
    cand = _candidate(directional_accuracy=0.40, ece=0.20, max_drawdown_points=-40.0)
    cmp = compare(_OLD, cand)
    assert cmp.expectancy_regressed is False
    assert cmp.metrics_better_or_equal < 3
    assert cmp.should_promote is False


def test_insufficient_candidate_sample_never_promotes():
    cand = _candidate(n=MIN_SHADOW_N - 1)
    cmp = compare(_OLD, cand)
    assert cmp.status == "INSUFFICIENT_CANDIDATE_DATA"
    assert cmp.should_promote is False


def test_insufficient_old_sample_never_promotes():
    old = {**_OLD, "status": "INSUFFICIENT_DATA"}
    cmp = compare(old, _candidate())
    assert cmp.status == "INSUFFICIENT_OLD_DATA"
    assert cmp.should_promote is False


def test_missing_metric_on_either_side_is_excluded_not_penalized():
    old_partial = {k: v for k, v in _OLD.items() if k != "ece"}
    cmp = compare(old_partial, _candidate())
    ece_entry = next(m for m in cmp.metrics if m["metric"] == "ece")
    assert ece_entry["better_or_equal"] is None
    assert cmp.metrics_compared == 4   # ece excluded from the scored set


def test_equal_metrics_count_as_better_or_equal_not_a_regression():
    cand = _candidate(**{k: _OLD[k] for k in
                         ("expectancy_points", "profit_factor", "directional_accuracy",
                          "ece", "max_drawdown_points")})
    cmp = compare(_OLD, cand)
    assert cmp.expectancy_regressed is False
    assert cmp.metrics_better_or_equal == 5
    assert cmp.should_promote is True


# --------------------------------------------------------------------------- #
#  ShadowValidator (hysteresis across evaluations)                            #
# --------------------------------------------------------------------------- #
def test_validator_requires_consecutive_passes_not_just_one():
    v = ShadowValidator()
    out1 = v.evaluate(_OLD, _candidate())
    assert out1["consecutive_passes"] == 1
    assert out1["promote_now"] is False
    out2 = v.evaluate(_OLD, _candidate())
    out3 = v.evaluate(_OLD, _candidate())
    assert out3["consecutive_passes"] == 3
    assert out3["promote_now"] is True


def test_validator_resets_streak_on_a_single_failing_comparison():
    v = ShadowValidator()
    v.evaluate(_OLD, _candidate())
    v.evaluate(_OLD, _candidate())
    assert v.consecutive_passes == 2
    out = v.evaluate(_OLD, _candidate(expectancy_points=0.1))   # one bad comparison
    assert out["consecutive_passes"] == 0
    out2 = v.evaluate(_OLD, _candidate())
    assert out2["consecutive_passes"] == 1   # streak restarted, not resumed
