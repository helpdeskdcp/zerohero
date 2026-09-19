"""app/liquidity_sweep/probability.py -- calibration wrapper, 3-way split,
confidence score, and the backtest-only forward outcome labeler."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.liquidity_sweep import probability as prob


def _bar(h, l):
    return {"h": h, "l": l}


def test_three_way_sums_to_one_and_splits_correctly_for_bullish():
    t = prob.three_way(0.7, "BULLISH", range_share=0.3)
    assert abs((t.up + t.down + t.range) - 1.0) < 1e-9
    assert t.up == 0.7
    assert t.range == round(0.3 * 0.3, 4)


def test_three_way_splits_correctly_for_bearish():
    t = prob.three_way(0.7, "BEARISH", range_share=0.3)
    assert t.down == 0.7
    assert t.up < t.down


def test_confidence_score_clamped_to_100():
    c = prob.confidence_score(setup_passed_checks=8, setup_max_checks=8,
                              sweep_reaction_atr_ratio=5.0, htf_bias_score=1.0)
    assert c == 100.0


def test_confidence_score_zero_when_nothing_supports_it():
    c = prob.confidence_score(setup_passed_checks=0, setup_max_checks=8,
                              sweep_reaction_atr_ratio=0.0, htf_bias_score=0.0)
    assert c == 0.0


def test_label_outcome_win_when_bullish_target_hit_first():
    bars = [_bar(101, 99), _bar(105, 100), _bar(103, 99)]   # bar 2 hits 105 (target)
    result = prob.label_outcome(bars, entry=100.0, direction="BULLISH", threshold_pts=5.0, horizon=3)
    assert result == "WIN"


def test_label_outcome_loss_when_opposite_threshold_hit_first():
    bars = [_bar(100, 94)]   # hits 95 (down) before ever reaching 105 (up)
    result = prob.label_outcome(bars, entry=100.0, direction="BULLISH", threshold_pts=5.0, horizon=3)
    assert result == "LOSS"


def test_label_outcome_timeout_when_neither_threshold_reached_in_horizon():
    bars = [_bar(101, 99), _bar(102, 98)]
    result = prob.label_outcome(bars, entry=100.0, direction="BULLISH", threshold_pts=5.0, horizon=2)
    assert result == "TIMEOUT"


def test_label_outcome_ambiguous_single_bar_spanning_both_is_a_loss():
    bars = [_bar(110, 90)]   # one bar touches both +5 and -5 thresholds
    result = prob.label_outcome(bars, entry=100.0, direction="BULLISH", threshold_pts=5.0, horizon=1)
    assert result == "LOSS"


def test_label_outcome_never_looks_beyond_the_horizon():
    # the WIN-triggering bar is at index 5, horizon is only 3 -> must time out
    bars = [_bar(101, 99)] * 5 + [_bar(200, 99)]
    result = prob.label_outcome(bars, entry=100.0, direction="BULLISH", threshold_pts=5.0, horizon=3)
    assert result == "TIMEOUT"


def test_calibration_fit_and_predict_roundtrip_with_real_signal():
    # 60 samples where a high score genuinely correlates with winning
    samples = []
    for i in range(60):
        score = 20.0 if i % 2 == 0 else 80.0
        win = (i % 2 == 1)   # high score -> win, low score -> lose, no noise
        samples.append({"score": score, "regime": "BULLISH", "signal_type": "LOWER_SWEEP", "win": win})
    calib = prob.fit_index_calibration(samples)
    assert calib["fitted"] is True
    p_high = prob.predict_directional_probability(calib, 80.0, regime="BULLISH", signal_type="LOWER_SWEEP")
    p_low = prob.predict_directional_probability(calib, 20.0, regime="BULLISH", signal_type="LOWER_SWEEP")
    assert p_high > p_low
