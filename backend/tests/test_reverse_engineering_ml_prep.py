"""app.reverse_engineering.ml_prep -- NOT_READY gate, no xgboost import/fit."""
import sys

import pytest

from app.reverse_engineering import ml_prep


def test_readiness_not_ready_below_threshold():
    events = [{"outcome": "TARGET_ACHIEVED"}] * 5
    r = ml_prep.check_training_readiness(events)
    assert r["model_status"] == "NOT_READY"
    assert r["reason"] == "INSUFFICIENT_SAMPLE"


def test_readiness_insufficient_labelled_even_with_enough_rows():
    events = [{"outcome": None}] * 150
    r = ml_prep.check_training_readiness(events)
    assert r["model_status"] == "NOT_READY"
    assert r["reason"] == "INSUFFICIENT_LABELLED_SAMPLE"


def test_readiness_ready_with_enough_labelled_events():
    events = [{"outcome": "TARGET_ACHIEVED"}] * 150
    r = ml_prep.check_training_readiness(events)
    assert r["model_status"] == "READY"


def test_train_returns_before_importing_xgboost():
    assert "xgboost" not in sys.modules
    events = [{"outcome": "TARGET_ACHIEVED"}] * 5   # current real dataset shape (n=5)
    result = ml_prep.train(events)
    assert result["model_status"] == "NOT_READY"
    assert "xgboost" not in sys.modules   # proves the not-ready path never imports it


def test_train_with_ready_data_raises_not_implemented_never_fits():
    events = [{"outcome": "TARGET_ACHIEVED", "timestamp": str(i)} for i in range(150)]
    with pytest.raises(NotImplementedError):
        ml_prep.train(events)
    assert "xgboost" not in sys.modules


def test_build_feature_vector_excludes_label_fields():
    event = {"entry": 64.0, "exit": 80.0, "outcome": "TARGET_ACHIEVED", "mfe": 20.0,
            "mae": -2.0, "underlying": "NIFTY", "id": 1, "capture_timestamp": "t"}
    fv = ml_prep.build_feature_vector(event)
    assert "exit" not in fv and "outcome" not in fv and "mfe" not in fv and "mae" not in fv
    assert fv["entry"] == 64.0 and fv["underlying"] == "NIFTY"


def test_build_label_only_contains_label_fields():
    event = {"entry": 64.0, "exit": 80.0, "outcome": "TARGET_ACHIEVED", "mfe": 20.0, "mae": -2.0}
    label = ml_prep.build_label(event)
    assert set(label.keys()) == {"outcome", "exit", "mfe", "mae"}


def test_chronological_split_is_time_ordered_not_shuffled():
    events = [{"timestamp": f"2026-09-{d:02d}"} for d in [3, 1, 2, 5, 4]]
    split = ml_prep.chronological_split(events)
    all_ts = [e["timestamp"] for e in split["train"] + split["validation"] + split["out_of_sample"]]
    assert all_ts == sorted(all_ts)
