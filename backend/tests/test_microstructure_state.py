"""
app/microstructure/state.py -- h1h7_state event -> section 13 vocabulary.
Pure function, no I/O.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.microstructure.state import STATES, classify  # noqa: E402


def _event(state, **overrides):
    base = {"state": state, "spike_direction": None, "broken_level": None,
            "body_fraction": None}
    base.update(overrides)
    return base


def test_h7_trap_maps_to_rejection():
    r = classify(_event("H7_TRAP", spike_direction="UP"))
    assert r.state == "REJECTION"
    assert "H1H7_STATE_H7_TRAP" in r.reason_codes


def test_h7_leaning_trap_also_maps_to_rejection():
    r = classify(_event("H7_LEANING_TRAP"))
    assert r.state == "REJECTION"


def test_h1_cont_maps_to_breakout():
    r = classify(_event("H1_CONT"))
    assert r.state == "BREAKOUT"


def test_h1_cont_observe_also_maps_to_breakout():
    r = classify(_event("H1_CONT_OBSERVE"))
    assert r.state == "BREAKOUT"


def test_h1_cont_blocked_maps_to_failed_breakout():
    r = classify(_event("H1_CONT_BLOCKED"))
    assert r.state == "FAILED_BREAKOUT"


def test_spike_no_level_with_small_body_is_absorption():
    r = classify(_event("SPIKE_NO_LEVEL", spike_direction="UP", body_fraction=0.2))
    assert r.state == "ABSORPTION"


def test_spike_no_level_with_missing_body_fraction_defaults_to_absorption():
    r = classify(_event("SPIKE_NO_LEVEL", spike_direction="UP", body_fraction=None))
    assert r.state == "ABSORPTION"


def test_spike_no_level_with_decisive_up_body_is_buying_pressure():
    r = classify(_event("SPIKE_NO_LEVEL", spike_direction="UP", body_fraction=0.8))
    assert r.state == "BUYING_PRESSURE"


def test_spike_no_level_with_decisive_down_body_is_selling_pressure():
    r = classify(_event("SPIKE_NO_LEVEL", spike_direction="DOWN", body_fraction=0.8))
    assert r.state == "SELLING_PRESSURE"


def test_spike_no_level_decisive_body_but_no_direction_is_unknown():
    r = classify(_event("SPIKE_NO_LEVEL", spike_direction=None, body_fraction=0.8))
    assert r.state == "UNKNOWN"


def test_neutral_maps_to_balanced():
    r = classify(_event("NEUTRAL"))
    assert r.state == "BALANCED"


def test_ambiguous_maps_to_unknown():
    r = classify(_event("AMBIGUOUS"))
    assert r.state == "UNKNOWN"


def test_h1_weak_maps_to_unknown():
    r = classify(_event("H1_WEAK"))
    assert r.state == "UNKNOWN"


def test_missing_or_unrecognized_state_maps_to_unknown_not_a_crash():
    assert classify({}).state == "UNKNOWN"
    assert classify(_event("SOME_FUTURE_STATE_NOT_YET_MAPPED")).state == "UNKNOWN"


def test_every_result_carries_at_least_one_reason_code():
    for state_name in ("H7_TRAP", "H1_CONT", "H1_CONT_BLOCKED", "NEUTRAL", "AMBIGUOUS"):
        r = classify(_event(state_name))
        assert len(r.reason_codes) >= 1


def test_result_state_is_always_one_of_the_documented_states():
    for state_name in ("H7_TRAP", "H7_LEANING_TRAP", "H1_CONT", "H1_CONT_OBSERVE",
                       "H1_CONT_BLOCKED", "NEUTRAL", "AMBIGUOUS", "H1_WEAK", "SPIKE_NO_LEVEL"):
        r = classify(_event(state_name, spike_direction="UP", body_fraction=0.8))
        assert r.state in STATES


def test_evidence_carries_the_raw_h1h7_fields_for_transparency():
    r = classify(_event("H7_TRAP", broken_level=24100.0, reclaim_distance_ratio=1.2))
    assert r.evidence["broken_level"] == 24100.0
    assert r.evidence["reclaim_distance_ratio"] == 1.2
    assert r.source_h1h7_state == "H7_TRAP"
