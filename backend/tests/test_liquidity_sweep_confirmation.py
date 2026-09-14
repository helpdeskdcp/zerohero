"""
app/liquidity_sweep/confirmation.py -- CHoCH/BOS/CISD/FVG/Order Block.
Pure functions, no I/O.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.liquidity_sweep import confirmation, structure  # noqa: E402


def _bar(o, h, l, c, v=1000, t="t"):
    return {"o": o, "h": h, "l": l, "c": c, "v": v, "t": t}


def _swing(index, price, kind):
    return structure.SwingPoint(index, price, kind, f"t{index}")


def test_bos_when_close_breaks_the_last_swing_high_in_an_uptrend():
    # L(100) -> H(110) -> L(105, "HL" since 105>100) -> H(115, "HH" since 115>110):
    # a clean, unambiguous uptrend (both the H-sequence and L-sequence agree)
    pts = [_swing(0, 100, "L"), _swing(2, 110, "H"), _swing(4, 105, "L"), _swing(6, 115, "H")]
    bars = [_bar(0, 0, 0, 0) for _ in range(9)]
    bars[-1] = _bar(115, 118, 115, 117)   # closes above the last swing high (115)
    out = confirmation.structure_break(bars, pts)
    assert out["type"] == "BOS"
    assert out["direction"] == "BULLISH"


def test_choch_when_close_breaks_against_the_prevailing_uptrend():
    pts = [_swing(0, 100, "L"), _swing(2, 110, "H"), _swing(4, 105, "L"), _swing(6, 112, "H")]
    bars = [_bar(0, 0, 0, 0) for _ in range(9)]
    bars[-1] = _bar(105, 106, 98, 99)   # closes BELOW the last swing low (105) while uptrend was established
    out = confirmation.structure_break(bars, pts)
    assert out["type"] == "CHOCH"
    assert out["direction"] == "BEARISH"


def test_no_structure_break_when_close_stays_inside_the_range():
    pts = [_swing(0, 100, "L"), _swing(2, 110, "H")]
    bars = [_bar(0, 0, 0, 0) for _ in range(3)]
    bars[-1] = _bar(105, 106, 104, 105)
    out = confirmation.structure_break(bars, pts)
    assert out["type"] == "NONE"


def test_cisd_confirms_the_first_opposite_color_candle():
    bars = [_bar(10, 11, 9, 9.5), _bar(9.5, 10, 8, 8.5), _bar(8.5, 9, 7, 7.5),  # 3 red
           _bar(7.5, 9, 7, 8.8)]                                                 # first green
    out = confirmation.cisd(bars)
    assert out["confirmed"] is True
    assert out["direction"] == "BULLISH"


def test_cisd_not_confirmed_when_same_color_continues():
    bars = [_bar(10, 11, 9, 9.5), _bar(9.5, 10, 8, 8.5), _bar(8.5, 9, 7, 7.5), _bar(7.5, 8, 6, 6.5)]
    out = confirmation.cisd(bars)
    assert out["confirmed"] is False


def test_fvg_bullish_gap_between_bar_minus3_high_and_bar_minus1_low():
    bars = [_bar(0, 100, 95, 98), _bar(98, 110, 97, 108), _bar(108, 120, 105, 118)]
    out = confirmation.fvg(bars)
    assert out["confirmed"] is True
    assert out["direction"] == "BULLISH"
    assert out["gap_low"] == 100 and out["gap_high"] == 105


def test_fvg_bearish_gap():
    bars = [_bar(0, 100, 95, 98), _bar(98, 96, 85, 90), _bar(90, 92, 80, 85)]
    out = confirmation.fvg(bars)
    assert out["confirmed"] is True
    assert out["direction"] == "BEARISH"


def test_fvg_not_confirmed_when_candles_overlap():
    bars = [_bar(0, 100, 95, 98), _bar(98, 102, 96, 100), _bar(100, 104, 97, 102)]
    out = confirmation.fvg(bars)
    assert out["confirmed"] is False


def test_order_block_is_the_opposite_colored_bar_before_an_impulsive_move():
    ob_bar = _bar(100, 101, 98, 99)          # red
    impulse_bar = _bar(99, 115, 99, 114)     # green, big range
    out = confirmation.order_block([ob_bar, impulse_bar], atr=2.0, impulse_atr_mult=1.2)
    assert out["confirmed"] is True
    assert out["direction"] == "BULLISH"


def test_order_block_rejected_when_same_color():
    ob_bar = _bar(100, 105, 99, 104)         # green
    impulse_bar = _bar(104, 120, 104, 119)   # also green
    out = confirmation.order_block([ob_bar, impulse_bar], atr=2.0)
    assert out["confirmed"] is False


def test_order_block_rejected_when_move_is_not_impulsive():
    ob_bar = _bar(100, 101, 99, 99.5)
    impulse_bar = _bar(99.5, 100.5, 99, 100)   # small range, not impulsive
    out = confirmation.order_block([ob_bar, impulse_bar], atr=2.0, impulse_atr_mult=1.2)
    assert out["confirmed"] is False


def test_evaluate_reports_secondary_confirmed_true_if_any_of_three_present():
    pts = [_swing(0, 100, "L"), _swing(2, 110, "H")]
    bars = [_bar(100, 101, 99, 100), _bar(0, 100, 95, 98), _bar(98, 110, 97, 108), _bar(108, 120, 105, 118)]
    out = confirmation.evaluate(bars, pts)
    assert out.candle_closed is True
    assert isinstance(out.secondary_confirmed, bool)
