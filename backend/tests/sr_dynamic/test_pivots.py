"""Requirement 1 (detect confirmed swing highs/lows) + requirement 2/13
(no future candles in live mode) for app.sr_dynamic.pivots."""
from app.sr_dynamic.pivots import confirmed_swings


def _bar(h, l, c=None, o=None):
    c = c if c is not None else (h + l) / 2
    o = o if o is not None else c
    return {"o": o, "h": h, "l": l, "c": c, "v": 100.0}


def test_too_few_bars_returns_empty_not_an_error():
    assert confirmed_swings([_bar(10, 9)] * 3, left=2, right=2) == []


def test_detects_a_simple_confirmed_swing_high():
    # bar index 3 is a local high with 2 bars of confirmation on each side
    highs = [10, 11, 12, 15, 12, 11, 10, 9, 8]
    bars = [_bar(h, h - 1) for h in highs]
    swings = confirmed_swings(bars, left=2, right=2)
    kinds = {(s.index, s.kind) for s in swings}
    assert (3, "H") in kinds


def test_detects_a_simple_confirmed_swing_low():
    lows = [10, 9, 8, 5, 8, 9, 10, 11, 12]
    bars = [_bar(l + 1, l) for l in lows]
    swings = confirmed_swings(bars, left=2, right=2)
    kinds = {(s.index, s.kind) for s in swings}
    assert (3, "L") in kinds


def test_a_swing_within_right_bars_of_the_end_is_not_yet_confirmable():
    """The defining no-lookahead property: a pivot needs `right` bars AFTER it
    to confirm. If the extreme bar is the last bar (or within `right` of it),
    it must NOT be reported yet -- there isn't enough forward data on hand."""
    highs = [10, 11, 12, 15]          # the spike is the LAST bar
    bars = [_bar(h, h - 1) for h in highs]
    swings = confirmed_swings(bars, left=2, right=2)
    assert swings == []               # correctly withheld, not fabricated


def test_growing_history_never_repaints_an_already_confirmed_swing():
    """Anti-repaint: feed a longer bar array (as if more real time has
    passed) and the ALREADY-confirmed swing at the same index must report
    the identical price/kind -- appending future data must only ever ADD
    new swings, never change a past one."""
    highs = [10, 11, 12, 15, 12, 11, 10, 9, 8, 9, 10, 9, 8]
    bars = [_bar(h, h - 1) for h in highs]
    early = confirmed_swings(bars[:9], left=2, right=2)
    later = confirmed_swings(bars, left=2, right=2)
    early_at_3 = next(s for s in early if s.index == 3)
    later_at_3 = next(s for s in later if s.index == 3)
    assert early_at_3 == later_at_3
    assert len(later) >= len(early)   # only grows
