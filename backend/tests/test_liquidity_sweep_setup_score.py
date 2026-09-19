"""app/liquidity_sweep/setup_score.py -- explainable 0-100 setup score."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.liquidity_sweep.setup_score import compute

FULL_IND = {"above_vwap": True, "above_ema20": True, "rsi14": 55.0, "adx": 30.0}


def test_all_checks_pass_scores_100():
    s = compute(direction="BULLISH", structure_direction="BULLISH", secondary_confirmed=True,
               htf_bias="BULLISH", indicators_snapshot=FULL_IND, avg_volume=1000, sweep_bar_volume=2000)
    assert s.score_0_100 == 100.0
    assert s.passed_checks == s.max_checks


def test_no_checks_pass_scores_zero():
    s = compute(direction="BULLISH", structure_direction="BEARISH", secondary_confirmed=False,
               htf_bias="BEARISH", indicators_snapshot={"above_vwap": False, "above_ema20": False,
                                                        "rsi14": 95.0, "adx": 10.0},
               avg_volume=1000, sweep_bar_volume=500)
    assert s.score_0_100 == 0.0


def test_missing_indicator_values_are_skipped_not_penalized_as_hard_fail():
    s = compute(direction="BULLISH", structure_direction="BULLISH", secondary_confirmed=True,
               htf_bias="BULLISH", indicators_snapshot={})
    # structure/secondary/htf still pass; vwap/ema/rsi/adx/volume all unavailable -> skipped
    assert s.passed_checks == 3
    assert "STRUCTURE_BREAK_ALIGNED" in s.reasons


def test_reasons_list_names_exactly_the_passed_checks():
    s = compute(direction="BEARISH", structure_direction="BEARISH", secondary_confirmed=True,
               htf_bias="RANGE", indicators_snapshot={"above_vwap": False})
    assert "STRUCTURE_BREAK_ALIGNED" in s.reasons
    assert "SECONDARY_CONFIRMATION_PRESENT" in s.reasons
    assert "HTF_BIAS_ALIGNED" not in s.reasons
    assert "VWAP_ALIGNED" in s.reasons   # bearish + below vwap (False) -> aligned
