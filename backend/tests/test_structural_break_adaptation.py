"""
app/structural_break/adaptation.py -- hooks the state machine into the
EXISTING Safeguards gate (app.autoscalp.safeguards), and section H's
adaptation-lifecycle bookkeeping.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

import app.autoscalp.safeguards as _sg  # noqa: E402
from app.autoscalp.safeguards import Safeguards  # noqa: E402
from app.structural_break.adaptation import AdaptationTracker, apply_to_safeguards  # noqa: E402


@pytest.fixture(autouse=True)
def _in_session_clock(monkeypatch, fresh_db):
    # Fixed "Wednesday, 12:00" clock -- same convention as test_autoscalp.py's
    # _ks_off fixture -- so check_entry()'s weekday/session-time gates never
    # depend on when this test suite happens to run. fresh_db (conftest.py)
    # gives check_entry()'s _trades_today()/_daily_realised() DB reads an
    # isolated, schema-initialized temp DB instead of the shared session one.
    monkeypatch.setattr(_sg, "_mod_now", lambda: (720, 2))


def _entry_kwargs(**overrides):
    base = dict(open_count=0, feed_connected=True, feed_age_sec=1.0, underlying="NIFTY",
                side="BUY_CE", open_keys=set())
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
#  Safeguards.set_external_halt (the new public hook)                         #
# --------------------------------------------------------------------------- #
def test_set_external_halt_blocks_check_entry():
    sg = Safeguards()
    ok, reason = sg.check_entry(**_entry_kwargs())
    assert ok is True
    sg.set_external_halt("STRUCTURAL_BREAK: NIFTY")
    ok, reason = sg.check_entry(**_entry_kwargs())
    assert ok is False and "STRUCTURAL_BREAK" in reason


def test_set_external_halt_none_clears_it():
    sg = Safeguards()
    sg.set_external_halt("STRUCTURAL_BREAK: NIFTY")
    sg.set_external_halt(None)
    ok, _ = sg.check_entry(**_entry_kwargs())
    assert ok is True


# --------------------------------------------------------------------------- #
#  apply_to_safeguards                                                        #
# --------------------------------------------------------------------------- #
def test_structural_break_state_halts_trading():
    sg = Safeguards()
    halted = apply_to_safeguards(sg, "STRUCTURAL_BREAK", reason="test")
    assert halted is True
    ok, reason = sg.check_entry(**_entry_kwargs())
    assert ok is False and "STRUCTURAL_BREAK" in reason


def test_adaptation_state_also_halts_trading():
    sg = Safeguards()
    halted = apply_to_safeguards(sg, "ADAPTATION")
    assert halted is True


def test_normal_state_does_not_halt():
    sg = Safeguards()
    halted = apply_to_safeguards(sg, "NORMAL")
    assert halted is False
    ok, _ = sg.check_entry(**_entry_kwargs())
    assert ok is True


def test_recovering_clears_a_structural_break_halt():
    sg = Safeguards()
    apply_to_safeguards(sg, "STRUCTURAL_BREAK")
    assert sg.check_entry(**_entry_kwargs())[0] is False
    halted = apply_to_safeguards(sg, "RECOVERED")
    assert halted is False
    assert sg.check_entry(**_entry_kwargs())[0] is True


def test_never_clears_a_halt_it_did_not_set():
    """A daily-loss-cap halt (set by Safeguards itself, not this layer) must
    survive a NORMAL-state structural-break evaluation -- this layer only
    clears halts carrying its own STRUCTURAL_BREAK: prefix."""
    sg = Safeguards()
    sg.set_external_halt("daily loss cap hit (-5000)")   # simulates Safeguards' own halt
    halted = apply_to_safeguards(sg, "NORMAL")
    assert halted is True   # still reports a halt is active...
    ok, reason = sg.check_entry(**_entry_kwargs())
    assert ok is False and "daily loss cap" in reason   # ...and did NOT wipe the real reason


# --------------------------------------------------------------------------- #
#  AdaptationTracker                                                          #
# --------------------------------------------------------------------------- #
def test_tracker_resets_candidate_on_fresh_adaptation_episode():
    tr = AdaptationTracker()
    tr.assign_candidate("cand-v1")
    status = tr.update("ADAPTATION", adaptation_observations=5)
    assert status.candidate_model_id is None   # fresh episode -- shadow.py assigns a new one
    assert status.validation_status == "NOT_STARTED"
    assert status.is_halted is True


def test_tracker_progresses_through_validation_lifecycle():
    tr = AdaptationTracker()
    tr.update("ADAPTATION", adaptation_observations=1)
    tr.assign_candidate("cand-v2")
    s = tr.update("ADAPTATION", adaptation_observations=2)
    assert s.candidate_model_id == "cand-v2"
    s = tr.update("VALIDATION")
    assert s.validation_status == "IN_PROGRESS"
    assert s.candidate_model_id == "cand-v2"   # carried forward, not reset
    s = tr.update("RECOVERED")
    assert s.validation_status == "PASSED"
    assert s.is_halted is False


def test_tracker_entered_state_ts_only_changes_on_transition():
    tr = AdaptationTracker()
    s1 = tr.update("WATCH")
    s2 = tr.update("WATCH")
    assert s1.entered_state_ts == s2.entered_state_ts
    s3 = tr.update("DEGRADING")
    assert s3.entered_state_ts != s1.entered_state_ts
