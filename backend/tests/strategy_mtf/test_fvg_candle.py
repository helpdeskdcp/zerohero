"""
FVG + inside-candle: confirmation-only, never a standalone direction
generator.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from app.strategy_mtf.fvg_candle import evaluate


def _bar(o, h, l, c, t="2026-08-04T03:45:00Z", v=1000):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def test_bullish_fvg_boosts_a_bullish_candidate_only():
    bars = [_bar(100, 100.5, 99.5, 100.2), _bar(101, 102, 100.8, 101.5), _bar(103, 104, 102.5, 103.5)]
    r_bull = evaluate(bars, candidate_direction="BULLISH")
    assert r_bull.fvg_confirmed and r_bull.fvg_direction == "BULLISH"
    assert r_bull.boost > 0
    r_bear = evaluate(bars, candidate_direction="BEARISH")
    assert r_bear.boost == 0.0   # a bullish FVG does not boost a bearish candidate


def test_no_fvg_gives_no_boost():
    bars = [_bar(100, 100.5, 99.5, 100.2), _bar(100.2, 100.6, 99.8, 100.3), _bar(100.3, 100.7, 99.9, 100.4)]
    r = evaluate(bars, candidate_direction="BULLISH")
    assert r.fvg_confirmed is False


def test_inside_candle_detected_and_only_boosts_when_a_candidate_exists():
    mother = _bar(100, 105, 95, 102)
    inside = _bar(101, 103, 98, 101.5)
    r = evaluate([mother, inside], candidate_direction="BULLISH")
    assert r.inside_candle is True
    assert r.inside_candle_direction == "BULLISH"
    r_no_candidate = evaluate([mother, inside], candidate_direction=None)
    assert r_no_candidate.boost == 0.0


def test_never_returns_a_direction_of_its_own_without_a_candidate():
    mother = _bar(100, 105, 95, 102)
    inside = _bar(101, 103, 98, 101.5)
    r = evaluate([mother, inside], candidate_direction=None)
    assert r.inside_candle_direction is None
