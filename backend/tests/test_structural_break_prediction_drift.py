"""
app/structural_break/prediction_drift.py -- predicted-vs-actual drift over
scalp_signals-shaped rows. No DB, pure function over an in-memory row list.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.structural_break.prediction_drift import evaluate_prediction_drift  # noqa: E402

random.seed(21)


def _healthy_row(i):
    is_win = (i % 10) < 6   # ~60% win rate, matching probability
    return {
        "outcome": "WIN" if is_win else "LOSS",
        "mfe": 8.0 if is_win else random.choice([0.0, 2.0]),  # losses occasionally had positive MFE
        "probability": 0.6 + random.gauss(0, 0.03),
        "expected_premium_move": 6.0 + random.gauss(0, 0.5),
        "points": (6.0 if is_win else -5.0) + random.gauss(0, 0.5),
    }


def test_insufficient_data_reports_clearly():
    rows = [_healthy_row(i) for i in range(50)]
    out = evaluate_prediction_drift(rows, baseline_n=100)
    assert out["status"] == "INSUFFICIENT_DATA"


def test_healthy_stable_predictions_do_not_trigger():
    rows = [_healthy_row(i) for i in range(300)]
    out = evaluate_prediction_drift(rows, baseline_n=100)
    assert out["status"] == "OK"
    for name, r in out["streams"].items():
        assert r["triggered"] is False, f"{name} false-triggered on healthy stable data: {r}"


def test_directional_accuracy_persistent_decline_triggers():
    rows = [_healthy_row(i) for i in range(100)]   # healthy baseline
    for i in range(150):   # then a persistent decline: mostly wrong direction
        is_win = (i % 10) < 2   # ~20% win, and losses now have NO mfe (direction was wrong)
        rows.append({
            "outcome": "WIN" if is_win else "LOSS",
            "mfe": 8.0 if is_win else 0.0,
            "probability": 0.6, "expected_premium_move": 6.0,
            "points": 6.0 if is_win else -5.0,
        })
    out = evaluate_prediction_drift(rows, baseline_n=100)
    r = out["streams"]["directional_accuracy"]
    assert r["triggered"] is True
    assert r["direction"] == "decrease"
    assert r["fired_at_index"] is not None


def test_calibration_error_persistent_increase_triggers():
    rows = [_healthy_row(i) for i in range(100)]   # well-calibrated baseline (~60% predicted, ~60% actual)
    for i in range(150):   # model stays confident but stops being right -> calibration error rises
        is_win = (i % 10) < 2
        rows.append({
            "outcome": "WIN" if is_win else "LOSS",
            "mfe": 8.0 if is_win else 3.0,
            "probability": 0.85,   # still claiming high confidence
            "expected_premium_move": 6.0, "points": 6.0 if is_win else -5.0,
        })
    out = evaluate_prediction_drift(rows, baseline_n=100)
    r = out["streams"]["calibration_error"]
    assert r["triggered"] is True and r["direction"] == "increase"


def test_move_error_persistent_increase_triggers():
    rows = [_healthy_row(i) for i in range(100)]
    for i in range(150):   # EPM stays the same but realised points diverge sharply
        rows.append({
            "outcome": "WIN" if i % 2 == 0 else "LOSS",
            "mfe": 5.0, "probability": 0.6,
            "expected_premium_move": 6.0,
            "points": 6.0 + (25.0 if i % 2 == 0 else -25.0),   # far off the 6.0 prediction
        })
    out = evaluate_prediction_drift(rows, baseline_n=100)
    r = out["streams"]["move_error"]
    assert r["triggered"] is True and r["direction"] == "increase"


def test_single_isolated_bad_prediction_does_not_trigger():
    rows = [_healthy_row(i) for i in range(100)]
    for i in range(150):
        rows.append(_healthy_row(100 + i))
    # inject exactly ONE bad row in the middle of the eval region
    rows[180] = {"outcome": "LOSS", "mfe": 0.0, "probability": 0.6,
                 "expected_premium_move": 6.0, "points": -40.0}
    out = evaluate_prediction_drift(rows, baseline_n=100)
    for name, r in out["streams"].items():
        assert r["triggered"] is False, f"{name} triggered on a single isolated bad row: {r}"


def test_rows_missing_fields_are_skipped_not_crashing():
    rows = [_healthy_row(i) for i in range(100)]
    for i in range(150):
        if i % 5 == 0:
            rows.append({"outcome": "FLAT"})   # no usable probability/mfe/points at all
        else:
            rows.append(_healthy_row(100 + i))
    out = evaluate_prediction_drift(rows, baseline_n=100)
    assert out["status"] == "OK"
