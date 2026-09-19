"""Live-wiring spec sections 8-10 + test list items 12,13 (BUY_CE/BUY_PE SR
confirmation) and 12 (room-to-move)."""
from app.sr_dynamic.live_state import LiveSRState
from app.sr_dynamic.signal_confirm import (
    CONFIRM,
    CONTRADICT,
    INSUFFICIENT,
    evaluate_sr_confirmation,
)


def _state(**kw) -> LiveSRState:
    base = dict(symbol="NIFTY", timestamp="t", spot=100.0, support_distance=5.0, resistance_distance=5.0,
               zone_strength=70.0, sr_reaction_score=80.0, state="TEST_FIXTURE")
    base.update(kw)
    return LiveSRState(**base)


def test_no_live_state_is_insufficient_never_fabricated():
    r = evaluate_sr_confirmation("CE", None, index_move_pts=2.0)
    assert r["verdict"] == INSUFFICIENT
    assert r["sr_score"] is None


def test_buy_ce_confirms_on_support_rejection_with_room():
    st = _state(support_rejection=True, resistance_distance=10.0)
    r = evaluate_sr_confirmation("CE", st, index_move_pts=2.0, min_room_ratio=1.0)
    assert r["verdict"] == CONFIRM
    assert "support_rejection" in r["positives"]


def test_buy_ce_contradicts_at_unbroken_resistance():
    st = _state(resistance_touch=True, resistance_breakout=False, resistance_distance=0.2)
    r = evaluate_sr_confirmation("CE", st, index_move_pts=2.0)
    assert r["verdict"] == CONTRADICT
    assert "at_unbroken_resistance" in r["negatives"]


def test_buy_ce_confirms_on_confirmed_resistance_breakout():
    st = _state(breakout_confirmed=True, resistance_breakout=True, resistance_retest_status="SUCCESSFUL",
               resistance_distance=10.0)
    r = evaluate_sr_confirmation("CE", st, index_move_pts=2.0)
    assert r["verdict"] == CONFIRM
    assert "resistance_breakout_confirmed" in r["positives"]


def test_buy_ce_contradicts_on_failed_resistance_breakout():
    st = _state(resistance_retest_status="FAILED", resistance_distance=10.0)
    r = evaluate_sr_confirmation("CE", st, index_move_pts=2.0)
    assert r["verdict"] == CONTRADICT
    assert "failed_resistance_breakout" in r["negatives"]


def test_buy_pe_confirms_on_resistance_rejection():
    st = _state(resistance_rejection=True, support_distance=10.0)
    r = evaluate_sr_confirmation("PE", st, index_move_pts=2.0)
    assert r["verdict"] == CONFIRM
    assert "resistance_rejection" in r["positives"]


def test_buy_pe_confirms_on_confirmed_support_breakdown():
    st = _state(breakdown_confirmed=True, support_breakdown=True, support_retest_status="SUCCESSFUL",
               support_distance=10.0)
    r = evaluate_sr_confirmation("PE", st, index_move_pts=2.0)
    assert r["verdict"] == CONFIRM
    assert "support_breakdown_confirmed" in r["positives"]


def test_buy_pe_contradicts_on_strong_support_below():
    st = _state(support_touch=True, support_breakdown=False, support_distance=0.1)
    r = evaluate_sr_confirmation("PE", st, index_move_pts=2.0)
    assert r["verdict"] == CONTRADICT
    assert "strong_support_below" in r["negatives"]


def test_room_to_move_blocks_when_next_zone_too_close():
    # resistance only 0.5 pts away, but expected index move is 2.0 -> insufficient room
    st = _state(support_rejection=True, resistance_distance=0.5)
    r = evaluate_sr_confirmation("CE", st, index_move_pts=2.0, min_room_ratio=1.0)
    assert any("insufficient_room" in n for n in r["negatives"])
    assert r["components"]["room_ratio"] == 0.25


def test_room_to_move_passes_when_plenty_of_room():
    st = _state(support_rejection=True, resistance_distance=10.0)
    r = evaluate_sr_confirmation("CE", st, index_move_pts=2.0, min_room_ratio=1.0)
    assert not any("insufficient_room" in n for n in r["negatives"])


def test_sr_score_is_bounded_0_to_100():
    st = _state(support_rejection=True, breakout_confirmed=True, resistance_breakout=True,
               resistance_retest_status="SUCCESSFUL", resistance_distance=10.0, zone_strength=100.0,
               sr_reaction_score=100.0)
    r = evaluate_sr_confirmation("CE", st, index_move_pts=1.0)
    assert 0.0 <= r["sr_score"] <= 100.0
