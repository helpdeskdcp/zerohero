"""
app/institutional_edge/conditions.py -- named single-feature condition
registry. Pure functions, no DB.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.institutional_edge.conditions import (  # noqa: E402
    CONDITIONS, condition_fn, condition_labels,
)


def test_every_condition_is_single_feature_and_callable():
    for label, fn in CONDITIONS.items():
        assert callable(fn)
        # sanity: doesn't raise on a mostly-empty row
        fn({})


def test_regime_conditions_cover_every_known_regime():
    from app.structural_break.regime_profiles import REGIME_NAMES
    for r in REGIME_NAMES:
        assert f"regime=={r}" in CONDITIONS


def test_condition_fn_raises_a_clear_error_for_an_unknown_label():
    with pytest.raises(KeyError, match="unknown condition"):
        condition_fn("not_a_real_condition")


def test_pcr_thresholds_are_directionally_correct():
    bullish = condition_fn("pcr>1.2")
    bearish = condition_fn("pcr<0.8")
    assert bullish({"pcr": 1.5}) is True
    assert bullish({"pcr": 0.9}) is False
    assert bearish({"pcr": 0.5}) is True
    assert bearish({"pcr": 1.0}) is False


def test_missing_field_never_crashes_and_defaults_to_not_matching():
    assert condition_fn("pcr>1.2")({}) is False
    assert condition_fn("mtf_alignment>30")({}) is False
    assert condition_fn("confidence==HIGH")({}) is False


def test_condition_labels_matches_the_registry_keys():
    assert set(condition_labels()) == set(CONDITIONS.keys())
