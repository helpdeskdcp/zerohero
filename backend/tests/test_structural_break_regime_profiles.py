"""
app/structural_break/regime_profiles.py -- registry-only, ships inert.
Pure in-memory, no DB.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.structural_break.regime_profiles import (  # noqa: E402
    REGIME_NAMES, RegimeProfile, RegimeProfileRegistry, scope_key,
)


def test_default_profile_for_every_known_regime_is_inert():
    reg = RegimeProfileRegistry()
    for name in REGIME_NAMES:
        p = reg.get(name)
        assert p.validated is False
        assert p.watch_min_override is None
        assert p.degrading_min_override is None
        assert p.break_min_override is None
        assert p.perf_window_overrides is None


def test_unknown_regime_falls_back_safely_instead_of_raising():
    reg = RegimeProfileRegistry()
    p = reg.get("SOME_FUTURE_REGIME_NOT_YET_NAMED")
    assert p.regime == "SOME_FUTURE_REGIME_NOT_YET_NAMED"
    assert p.validated is False


def test_none_regime_falls_back_to_unknown():
    reg = RegimeProfileRegistry()
    p = reg.get(None)
    assert p.regime == "UNKNOWN"


def test_registering_an_unvalidated_profile_is_refused():
    reg = RegimeProfileRegistry()
    with pytest.raises(ValueError, match="unvalidated"):
        reg.register(RegimeProfile(regime="TRENDING_UP", validated=False))


def test_registering_a_validated_profile_without_a_note_is_refused():
    reg = RegimeProfileRegistry()
    with pytest.raises(ValueError, match="validated_by_note"):
        reg.register(RegimeProfile(regime="TRENDING_UP", validated=True))


def test_registering_a_properly_validated_profile_takes_effect():
    reg = RegimeProfileRegistry()
    reg.register(RegimeProfile(regime="HIGH_VOLATILITY", validated=True,
                                validated_by_note="backtest_compare.py run 2026-09-12: wider "
                                                   "break thresholds reduced false alarms 40%",
                                break_min_override=8))
    p = reg.get("HIGH_VOLATILITY")
    assert p.break_min_override == 8
    assert p.validated is True
    # unrelated regimes are unaffected
    assert reg.get("RANGE").break_min_override is None


def test_unregister_reverts_to_the_inert_default():
    reg = RegimeProfileRegistry()
    reg.register(RegimeProfile(regime="RANGE", validated=True, validated_by_note="x",
                                watch_min_override=1))
    reg.unregister("RANGE")
    assert reg.get("RANGE").watch_min_override is None


def test_scope_key_distinguishes_symbol_alone_from_symbol_plus_regime():
    assert scope_key("NIFTY") == "NIFTY"
    assert scope_key("NIFTY", "TRENDING_UP") == "NIFTY:TRENDING_UP"
    assert scope_key("NIFTY", None) == "NIFTY"
    assert scope_key("NIFTY", "TRENDING_UP") != scope_key("NIFTY", "TRENDING_DOWN")
