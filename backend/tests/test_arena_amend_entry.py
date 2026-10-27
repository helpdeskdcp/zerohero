"""AMEND_ENTRY: move the trigger price of a WAITING ABOVE / LIMIT order (broker-style 'modify order'); never retroactive, ignored once filled."""
import pytest

from app.arena import settle as S, submit as SU
from tests.test_arena import ep, ev, sig, snaps, H


def test_limit_entry_lowered_then_fills_at_the_new_price():
    sn = snaps([("10:01", 110, 109, 111), ("10:03", 104, 103, 105), ("10:05", 99, 98, 99.5), ("10:10", 125, 124, 126)])
    # original LIMIT 95 never fills; moved to 100 at 10:02 (effective ~10:02:20) -> fills at the 10:05 ask
    base = S.settle(sig(entry_mode="LIMIT", entry=95.0, sl=90.0, targets=[120.0]), sn, half_spread=H)
    assert base["status"] == "NOT_TRIGGERED" and base["entry_now"] == 95.0
    r = S.settle(sig(entry_mode="LIMIT", entry=95.0, sl=90.0, targets=[120.0], events=[ev("10:02", "AMEND_ENTRY", entry=100.0)]), sn, half_spread=H)
    assert r["status"] == "SETTLED" and r["fill"] == 99.5 and r["fill_t"] == ep("10:05")


def test_waiting_row_reports_the_levels_after_amendments():
    sn = snaps([("10:01", 110, 109, 111), ("10:05", 108, 107, 109)])
    r = S.settle(sig(entry_mode="LIMIT", entry=95.0, sl=90.0, targets=[120.0], lots=1,
                     events=[ev("10:02", "AMEND_ENTRY", entry=96.5), ev("10:02", "AMEND_SL", sl=88.0), ev("10:02", "AMEND_TARGETS", targets=[125.0, 130.0])]), sn, half_spread=H)
    assert r["status"] == "NOT_TRIGGERED" and r["entry_now"] == 96.5 and r["sl_now"] == 88.0 and r["targets_now"] == [125.0, 130.0]


def test_amend_is_not_retroactive_and_is_ignored_after_the_fill():
    sn = snaps([("10:01", 110, 109, 111), ("10:05", 99, 98, 99.5), ("10:08", 99.2, 99.0, 99.4), ("10:10", 125, 124, 126)])
    # filled at 10:05 under LIMIT 100; an entry change at 10:06 must not move the fill
    r = S.settle(sig(entry_mode="LIMIT", entry=100.0, sl=90.0, targets=[120.0], events=[ev("10:06", "AMEND_ENTRY", entry=80.0)]), sn, half_spread=H)
    assert r["status"] == "SETTLED" and r["fill"] == 99.5 and r["fill_t"] == ep("10:05")


def test_above_entry_zone_can_be_moved():
    sn = snaps([("10:01", 100, 99, 101), ("10:04", 106, 105, 107), ("10:10", 109, 108, 110)])
    base = S.settle(sig(entry_mode="ABOVE", entry=110.0, entry_hi=110.0, sl=90.0, targets=[108.0]), sn, half_spread=H)
    assert base["status"] == "NOT_TRIGGERED"
    r = S.settle(sig(entry_mode="ABOVE", entry=110.0, entry_hi=110.0, sl=90.0, targets=[108.0], events=[ev("10:02", "AMEND_ENTRY", entry=105.0)]), sn, half_spread=H)
    assert r["status"] == "SETTLED" and r["fill"] == 107.0


def test_validation():
    assert SU.validate_event({"signal_id": "H1", "action": "AMEND_ENTRY", "entry": "101.5"})["entry"] == 101.5
    for bad in ({"entry": 0}, {"entry": -3}, {}, {"entry": 100, "entry_hi": 90}):
        with pytest.raises(SU.SubmissionError):
            SU.validate_event({"signal_id": "H1", "action": "AMEND_ENTRY", **bad})
