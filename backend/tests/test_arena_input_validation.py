"""Bad numbers in a submission or an amendment are a normal rejection (SubmissionError -> 422), never a crash, and never reach the ledger (Infinity is not valid JSON)."""
import pytest

from app.arena import submit as SU

BASE = {"participant": "X", "underlying": "NIFTY", "expiry": "2026-11-03", "strike": 25000, "option_type": "CE", "entry": 100.0, "sl": 90.0, "targets": [120.0]}
EQ = {"participant": "X", "instrument": "EQUITY", "underlying": "RELIANCE", "side": "BUY", "entry": 100.0, "sl": 95.0, "targets": [110.0], "qty": 10}


@pytest.mark.parametrize("patch", [{"strike": "abc"}, {"entry": "inf"}, {"entry": float("nan")}, {"entry": float("inf")}, {"sl": "x"}, {"targets": 12}, {"targets": ["a"]}, {"targets": [float("inf")]},
                                   {"valid_sec": "x"}, {"valid_sec": 5}, {"valid_sec": 60.5}, {"lots": "abc"}, {"lots": 2.5}, {"lots": True}, {"entry_hi": "q"}, {"entry": True}])
def test_option_submission_rejects_bad_numbers_with_a_submission_error(patch):
    with pytest.raises(SU.SubmissionError): SU.validate({**BASE, **patch})


@pytest.mark.parametrize("patch", [{"entry": "abc"}, {"qty": "x"}, {"qty": 2.5}, {"qty": None, "lots": None}, {"targets": "100"}, {"sl": float("inf")}, {"valid_sec": "x"}])
def test_equity_submission_rejects_bad_numbers_with_a_submission_error(patch):
    with pytest.raises(SU.SubmissionError): SU.validate({**EQ, **patch})


@pytest.mark.parametrize("ev", [{"action": "AMEND_SL", "sl": "abc"}, {"action": "AMEND_SL", "sl": "nan"}, {"action": "AMEND_ENTRY", "entry": "x"}, {"action": "AMEND_ENTRY", "entry": 10, "entry_hi": "x"},
                                {"action": "AMEND_LOTS", "lots": "x"}, {"action": "AMEND_LOTS", "lots": 1.5}, {"action": "AMEND_TARGETS", "targets": "12"}, {"action": "AMEND_TARGETS", "targets": ["a"]},
                                {"action": "TRAIL_SL", "trail_points": "x"}, {"action": "TRAIL_SL", "trail_pct": "inf"}, {"action": "TRAIL_SL"}])
def test_events_reject_bad_numbers_with_a_submission_error(ev):
    with pytest.raises(SU.SubmissionError): SU.validate_event({"signal_id": "H1", **ev})


def test_valid_input_is_unchanged_and_blank_means_absent():
    v = SU.validate({**BASE, "sl": "", "targets": [], "lots": "3", "valid_sec": "900", "entry_hi": 105}); assert v["sl"] is None and v["targets"] == [] and v["lots"] == 3 and v["valid_sec"] == 900 and v["entry_hi"] == 105
    assert SU.validate({**BASE, "targets": [130, 120, 120.0]})["targets"] == [120.0, 130.0]
    assert SU.validate_event({"signal_id": "H1", "action": "TRAIL_SL", "trail_pct": "5"})["trail_pct"] == 5.0
    assert SU.validate(EQ)["qty"] == 10
