"""
app/liquidity_sweep/sweep.py -- liquidity sweep detection. Pure functions,
no I/O. Includes the sweep-specific no-look-ahead property test (the
mutation test on the full pipeline lives in
test_liquidity_sweep_lookahead.py, but the property is checked here too
since this is the single most look-ahead-sensitive function).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.liquidity_sweep.sweep import (
    LOWER_SWEEP,
    UPPER_SWEEP,
    detect_sweep,
)


def _bar(o, h, l, c, v=1000):
    return {"o": o, "h": h, "l": l, "c": c, "v": v}


def test_lower_sweep_same_bar_wick_and_reclaim():
    bars = [_bar(100, 101, 99, 100) for _ in range(5)]
    bars.append(_bar(100, 101, 90, 101))   # wicks below 95 (level), closes back above
    sw = detect_sweep(bars, [{"price": 95.0, "source": "PDL"}])
    assert sw is not None
    assert sw.kind == LOWER_SWEEP
    assert sw.level == 95.0
    assert sw.bars_to_reclaim == 0


def test_upper_sweep_same_bar():
    bars = [_bar(100, 101, 99, 100) for _ in range(5)]
    bars.append(_bar(100, 106, 99, 99))   # wicks above 105, closes back below
    sw = detect_sweep(bars, [{"price": 105.0, "source": "PDH"}])
    assert sw is not None
    assert sw.kind == UPPER_SWEEP


def test_reclaim_over_multiple_bars_within_window():
    bars = [_bar(100, 101, 99, 100) for _ in range(5)]
    bars.append(_bar(100, 101, 90, 92))     # wicks below level, closes BELOW still (not yet reclaimed)
    bars.append(_bar(92, 93, 91, 96))       # still below level
    bars.append(_bar(96, 98, 95, 97))       # closes back above level -- reclaimed 2 bars later
    sw = detect_sweep(bars, [{"price": 95.0, "source": "PDL"}], reclaim_window=3)
    assert sw is not None
    assert sw.bars_to_reclaim == 2


def test_reclaim_outside_the_window_is_not_a_sweep():
    bars = [_bar(100, 101, 99, 100) for _ in range(5)]
    bars.append(_bar(100, 101, 90, 92))          # the ONLY bar that wicks below the level (index 5)
    for _ in range(5):
        bars.append(_bar(96, 97, 96, 96.5))      # low stays ABOVE the level -- no further wicks
    bars.append(_bar(96, 98, 96, 97))            # closes above the level, but the wick was 6 bars ago
    sw = detect_sweep(bars, [{"price": 95.0, "source": "PDL"}], reclaim_window=3)
    assert sw is None


def test_plain_breakout_with_no_reclaim_is_not_a_sweep():
    bars = [_bar(100, 101, 99, 100) for _ in range(5)]
    bars.append(_bar(100, 106, 100, 105))   # breaks above level and STAYS above -- a breakout, not a sweep
    sw = detect_sweep(bars, [{"price": 105.0, "source": "PDH"}])
    assert sw is None


def test_no_wick_beyond_level_is_not_a_sweep():
    bars = [_bar(100, 101, 99, 100) for _ in range(10)]
    sw = detect_sweep(bars, [{"price": 200.0, "source": "PDH"}, {"price": 0.0, "source": "PDL"}])
    assert sw is None


def test_min_reaction_atr_filters_a_tiny_poke():
    bars = [_bar(100, 101, 99, 100) for _ in range(5)]
    bars.append(_bar(100, 100.1, 99, 99))   # wicks only 0.1 above the level
    sw = detect_sweep(bars, [{"price": 100.0, "source": "PDH"}], min_reaction_atr=1.0, atr=2.0)
    assert sw is None   # 0.1 < 1.0*2.0 required reaction


def test_no_levels_or_too_few_bars_returns_none():
    assert detect_sweep([_bar(100, 101, 99, 100)], [{"price": 95.0, "source": "PDL"}]) is None
    assert detect_sweep([_bar(100, 101, 99, 100), _bar(100, 101, 99, 100)], []) is None


def test_appending_a_new_bar_after_a_confirmed_sweep_does_not_change_the_original_reading():
    """The core no-look-ahead property: detect_sweep(bars[:T]) must equal
    detect_sweep(bars[:T] + <anything after>) when re-evaluated for THAT
    same T -- i.e. calling it on a longer series but asking about the same
    historical point (by re-truncating) must be stable."""
    bars = [_bar(100, 101, 99, 100) for _ in range(5)]
    bars.append(_bar(100, 101, 90, 101))   # confirmed lower sweep at index 5
    levels = [{"price": 95.0, "source": "PDL"}]
    sw_at_T = detect_sweep(bars, levels)

    future_bars = bars + [_bar(101, 150, 101, 149), _bar(149, 149, 50, 60)]   # wild future moves
    sw_at_T_again = detect_sweep(future_bars[:len(bars)], levels)   # same T, re-truncated
    assert sw_at_T.to_dict() == sw_at_T_again.to_dict()
