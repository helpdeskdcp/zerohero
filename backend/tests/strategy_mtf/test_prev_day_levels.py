"""
Previous-day H/L breakout / failed-breakout / rejection / reversal /
retest detection. A touch is never automatically a breakout.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from app.strategy_mtf.mtf_config import MTFConfig  # noqa: E402
from app.strategy_mtf.prev_day_levels import (  # noqa: E402
    BREAKOUT_DOWN, BREAKOUT_UP, FAILED_BREAKOUT_UP, NONE, REJECTION_UP, RETEST_UP, REVERSAL_UP, detect,
)

ATR = 1.0
CFG = MTFConfig()


def _bar(t, o, h, l, c, v=1000):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def _prev_day(prev_high=110.0, prev_low=90.0):
    return [_bar("2026-08-03T03:45:00Z", 100, prev_high, prev_low, 100, 1000)]


def test_plain_touch_is_not_a_breakout():
    """Wick just barely above PDH but closes back inside, with a margin
    smaller than the confirm threshold -- must not read as a breakout."""
    bars = _prev_day() + [_bar("2026-08-04T03:45:00Z", 109.9, 110.2, 109.5, 109.9)]
    ev = detect(bars, as_of_session="2026-08-04", atr=ATR, cfg=CFG)
    assert ev.event != BREAKOUT_UP


def test_confirmed_close_beyond_pdh_is_a_breakout():
    bars = _prev_day() + [_bar("2026-08-04T03:45:00Z", 109.5, 112.0, 109.4, 111.5)]
    ev = detect(bars, as_of_session="2026-08-04", atr=ATR, cfg=CFG)
    assert ev.event == BREAKOUT_UP and ev.direction == "BULLISH"


def test_confirmed_close_beyond_pdl_is_a_breakdown():
    bars = _prev_day() + [_bar("2026-08-04T03:45:00Z", 90.5, 90.6, 87.0, 88.0)]
    ev = detect(bars, as_of_session="2026-08-04", atr=ATR, cfg=CFG)
    assert ev.event == BREAKOUT_DOWN and ev.direction == "BEARISH"


def test_wick_beyond_pdh_closing_back_inside_is_a_rejection():
    bars = _prev_day() + [_bar("2026-08-04T03:45:00Z", 109.0, 113.0, 108.9, 109.5)]
    ev = detect(bars, as_of_session="2026-08-04", atr=ATR, cfg=CFG)
    assert ev.event == REJECTION_UP and ev.direction == "BEARISH"


def test_a_breakout_that_later_closes_back_below_the_level_is_a_failed_breakout():
    bars = _prev_day() + [
        _bar("2026-08-04T03:45:00Z", 109.5, 112.0, 109.4, 111.5),   # confirmed breakout above PDH
        _bar("2026-08-04T03:50:00Z", 109.9, 110.0, 109.5, 109.8),   # gaps down, closes back below PDH, no new wick above it
    ]
    ev = detect(bars, as_of_session="2026-08-04", atr=ATR, cfg=CFG)
    assert ev.event == FAILED_BREAKOUT_UP and ev.direction == "BEARISH"


def test_a_breakdown_that_fully_reverses_through_the_opposite_level_is_a_reversal():
    bars = _prev_day() + [
        _bar("2026-08-04T03:45:00Z", 90.5, 90.6, 87.0, 88.0),      # confirmed breakdown below PDL
        _bar("2026-08-04T03:50:00Z", 88.0, 111.0, 87.9, 110.5),    # violently reverses back above PDH
    ]
    ev = detect(bars, as_of_session="2026-08-04", atr=ATR, cfg=CFG)
    assert ev.event == REVERSAL_UP and ev.direction == "BULLISH"   # named for the NEW direction, not the original break


def test_pullback_to_pdh_after_a_confirmed_breakout_is_a_retest():
    bars = _prev_day() + [
        _bar("2026-08-04T03:45:00Z", 109.5, 112.0, 109.4, 111.5),   # confirmed breakout above PDH
        _bar("2026-08-04T03:50:00Z", 111.5, 111.6, 109.9, 110.05),  # pulls back to just above PDH (110)
    ]
    ev = detect(bars, as_of_session="2026-08-04", atr=ATR, cfg=CFG)
    assert ev.event == RETEST_UP and ev.direction == "BULLISH"


def test_no_prior_session_data_is_none_not_a_crash():
    ev = detect([_bar("2026-08-04T03:45:00Z", 100, 101, 99, 100)], as_of_session="2026-08-04", atr=ATR, cfg=CFG)
    assert ev.event == NONE
