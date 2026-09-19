"""Final high-confidence single-signal gate: APPROVED/WAIT/REJECT/DUPLICATE/
COOLDOWN, confidence scoring, and historical-confidence labeling."""
from app.signal_gate.final_signal_gate import (
    _ACTIVE_SIGNAL,
    _LAST_APPROVED,
    APPROVED,
    COOLDOWN,
    DUPLICATE,
    REJECT,
    WAIT,
    evaluate_final_signal,
    signal_fingerprint,
)


def _decision(**kw):
    base = dict(decision="BUY_CE", symbol="NIFTY", direction="BULLISH", signal_type="SUPPORT_REVERSAL",
               strike=23500.0, expiry="2026-09-30", support=23400.0, resistance=23600.0,
               rr=2.0, calibration_status="prior", calibration_samples=0, probability=0.6,
               component_scores={"htf": 0.9, "vwap": 0.9, "momentum": 0.9, "volume": 0.9, "oi": 0.9})
    base.update(kw)
    return base


def _sr(verdict="CONFIRM", score=90.0):
    return {"verdict": verdict, "reason": f"stub:{verdict}", "sr_score": score}


def setup_function(_):
    _LAST_APPROVED.clear()
    _ACTIVE_SIGNAL.clear()


def test_no_raw_signal_is_rejected():
    d = evaluate_final_signal(_decision(decision="NO_TRADE"), sr_confirmation=_sr())
    assert d.state == REJECT


def test_sr_contradict_is_rejected():
    d = evaluate_final_signal(_decision(), sr_confirmation=_sr("CONTRADICT"))
    assert d.state == REJECT
    assert "SR contradicts" in d.reason


def test_sr_insufficient_is_wait_not_reject():
    d = evaluate_final_signal(_decision(), sr_confirmation=_sr("INSUFFICIENT"))
    assert d.state == WAIT


def test_high_confidence_setup_is_approved():
    d = evaluate_final_signal(_decision(), sr_confirmation=_sr("CONFIRM", 95.0))
    assert d.state == APPROVED
    assert d.confidence_score is not None and d.confidence_score >= 85.0
    assert d.fingerprint


def test_low_confidence_setup_is_wait_not_rejected_as_no_signal():
    weak = _decision(rr=1.6, component_scores={"htf": 0.1, "vwap": 0.1, "momentum": 0.1,
                                               "volume": 0.1, "oi": 0.1})
    d = evaluate_final_signal(weak, sr_confirmation=_sr("CONFIRM", 30.0))
    assert d.state == WAIT
    assert d.confidence_score < 80.0


def test_rr_below_minimum_is_rejected():
    d = evaluate_final_signal(_decision(rr=0.8), sr_confirmation=_sr("CONFIRM", 95.0))
    assert d.state == REJECT
    assert "risk/reward" in d.reason


def test_chasing_an_extended_move_waits_not_rejects():
    d = evaluate_final_signal(_decision(dist_from_anchor_atr=4.5), sr_confirmation=_sr("CONFIRM", 95.0))
    assert d.state == WAIT
    assert "chasing" in d.reason


def test_a_fresh_entry_near_the_trigger_level_is_not_blocked_as_chasing():
    d = evaluate_final_signal(_decision(dist_from_anchor_atr=0.5), sr_confirmation=_sr("CONFIRM", 95.0))
    assert d.state == APPROVED


def test_missing_dist_from_anchor_never_blocks_as_chasing():
    d = evaluate_final_signal(_decision(dist_from_anchor_atr=None), sr_confirmation=_sr("CONFIRM", 95.0))
    assert d.state == APPROVED


def test_missing_components_are_excluded_not_penalized_to_zero():
    d = evaluate_final_signal(_decision(component_scores={}), sr_confirmation=_sr("CONFIRM", 95.0))
    # only SR + RR available -> score should still reflect what's real, not crash
    assert d.state in (APPROVED, WAIT)
    assert d.confidence_score is not None


def test_duplicate_when_an_identical_setup_is_already_active():
    dec = _decision()
    fp = signal_fingerprint(symbol=dec["symbol"], direction=dec["direction"], instrument=dec["decision"],
                            strike=dec["strike"], expiry=dec["expiry"],
                            sr_zone=(dec["support"], dec["resistance"]), signal_type=dec["signal_type"])
    d = evaluate_final_signal(dec, sr_confirmation=_sr("CONFIRM", 95.0), active_fingerprints={fp})
    assert d.state == DUPLICATE


def test_cooldown_blocks_a_re_approval_within_the_window():
    # isolate the cooldown mechanism from the one-active-signal mechanism
    # (which would otherwise classify a same-symbol-same-direction repeat as
    # DUPLICATE first, since the position never resolves in this test)
    dec = _decision()
    t0 = 1_000_000.0
    first = evaluate_final_signal(dec, sr_confirmation=_sr("CONFIRM", 95.0), now=t0, cooldown_sec=300,
                                  one_active_signal=False)
    assert first.state == APPROVED
    second = evaluate_final_signal(dec, sr_confirmation=_sr("CONFIRM", 95.0), now=t0 + 60, cooldown_sec=300,
                                   one_active_signal=False)
    assert second.state == COOLDOWN


def test_a_new_approval_is_allowed_after_the_cooldown_window():
    dec = _decision()
    t0 = 1_000_000.0
    evaluate_final_signal(dec, sr_confirmation=_sr("CONFIRM", 95.0), now=t0, cooldown_sec=300,
                          one_active_signal=False)
    later = evaluate_final_signal(dec, sr_confirmation=_sr("CONFIRM", 95.0), now=t0 + 301, cooldown_sec=300,
                                  one_active_signal=False)
    assert later.state == APPROVED


def test_uncalibrated_when_prior_or_thin_sample():
    d = evaluate_final_signal(_decision(calibration_status="prior"), sr_confirmation=_sr("CONFIRM", 95.0))
    assert d.historical_confidence == "UNCALIBRATED"
    d2 = evaluate_final_signal(_decision(calibration_status="fitted", calibration_samples=5),
                               sr_confirmation=_sr("CONFIRM", 95.0))
    assert d2.historical_confidence == "UNCALIBRATED"


def test_real_calibrated_confidence_is_labeled_with_sample_size():
    d = evaluate_final_signal(_decision(calibration_status="fitted", calibration_samples=120, probability=0.63),
                              sr_confirmation=_sr("CONFIRM", 95.0))
    assert "63%" in d.historical_confidence
    assert "120" in d.historical_confidence


def test_one_active_signal_same_direction_is_duplicate():
    dec = _decision()
    first = evaluate_final_signal(dec, sr_confirmation=_sr("CONFIRM", 95.0), now=1000.0)
    assert first.state == APPROVED
    second = evaluate_final_signal(dec, sr_confirmation=_sr("CONFIRM", 95.0), now=1000.0)
    assert second.state == DUPLICATE


def test_opposite_direction_waits_for_active_signal_to_resolve():
    dec_ce = _decision(direction="BULLISH")
    first = evaluate_final_signal(dec_ce, sr_confirmation=_sr("CONFIRM", 95.0), now=1000.0)
    assert first.state == APPROVED
    dec_pe = _decision(decision="BUY_PE", direction="BEARISH", signal_type="SUPPORT_BREAKDOWN")
    second = evaluate_final_signal(dec_pe, sr_confirmation=_sr("CONFIRM", 95.0), now=1000.0)
    assert second.state == WAIT
    assert "active" in second.reason


def test_allow_opposite_while_active_bypasses_the_wait():
    dec_ce = _decision(direction="BULLISH")
    evaluate_final_signal(dec_ce, sr_confirmation=_sr("CONFIRM", 95.0), now=1000.0)
    dec_pe = _decision(decision="BUY_PE", direction="BEARISH", signal_type="SUPPORT_BREAKDOWN")
    second = evaluate_final_signal(dec_pe, sr_confirmation=_sr("CONFIRM", 95.0), now=1000.0,
                                   allow_opposite_while_active=True)
    assert second.state == APPROVED


def test_active_signal_released_after_mark_resolved():
    from app.signal_gate.final_signal_gate import mark_resolved
    dec = _decision()
    evaluate_final_signal(dec, sr_confirmation=_sr("CONFIRM", 95.0), now=1000.0)
    mark_resolved(dec["symbol"])
    again = evaluate_final_signal(dec, sr_confirmation=_sr("CONFIRM", 95.0), now=1000.0 + 601)
    assert again.state == APPROVED


def test_require_volume_confirmation_waits_when_volume_data_missing():
    dec = _decision(component_scores={"htf": 0.9, "vwap": 0.9, "momentum": 0.9, "oi": 0.9})
    d = evaluate_final_signal(dec, sr_confirmation=_sr("CONFIRM", 95.0), require_volume_confirmation=True)
    assert d.state == WAIT


def test_require_oi_confirmation_waits_when_oi_data_missing():
    dec = _decision(component_scores={"htf": 0.9, "vwap": 0.9, "momentum": 0.9, "volume": 0.9})
    d = evaluate_final_signal(dec, sr_confirmation=_sr("CONFIRM", 95.0), require_oi_confirmation=True)
    assert d.state == WAIT


def test_sr_confirmation_not_required_when_disabled():
    d = evaluate_final_signal(_decision(), sr_confirmation=_sr("CONTRADICT"), require_sr_confirmation=False)
    assert d.state != REJECT


def test_fingerprint_differs_by_direction_and_symbol():
    fp1 = signal_fingerprint(symbol="NIFTY", direction="BULLISH", instrument="BUY_CE", strike=23500,
                             expiry="x", sr_zone=(23400, 23600), signal_type="SUPPORT_REVERSAL")
    fp2 = signal_fingerprint(symbol="NIFTY", direction="BEARISH", instrument="BUY_PE", strike=23500,
                             expiry="x", sr_zone=(23400, 23600), signal_type="SUPPORT_REVERSAL")
    fp3 = signal_fingerprint(symbol="BANKNIFTY", direction="BULLISH", instrument="BUY_CE", strike=23500,
                             expiry="x", sr_zone=(23400, 23600), signal_type="SUPPORT_REVERSAL")
    assert len({fp1, fp2, fp3}) == 3
