"""Requirement 10-11: 0-100 strength score (never called a probability),
weighted with renormalization over missing components."""
import pytest

from app.sr_dynamic.scoring import score_zone, WEIGHTS


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_strong_zone_scores_high():
    r = score_zone(swing_count=4, touches=5, rejections=3, volume_ratio=2.5,
                   last_touch_index=99, n_bars=100, retest_status="SUCCESSFUL",
                   confirmed_other_tfs=2, n_other_tfs_available=2)
    assert r.strength >= 80.0
    assert 0.0 <= r.strength <= 100.0


def test_untested_zone_with_no_touches_excludes_touch_component():
    r = score_zone(swing_count=1, touches=0, rejections=0, volume_ratio=None,
                   last_touch_index=None, n_bars=100, retest_status="NONE",
                   confirmed_other_tfs=None, n_other_tfs_available=None)
    assert "touch_rejection" not in r.components
    assert "volume_confirmation" not in r.components
    assert "recency" not in r.components
    assert "mtf_confirmation" not in r.components
    # only swing_strength and breakout_retest (neutral 0.5) survive
    assert set(r.components) == {"swing_strength", "breakout_retest"}
    assert 0.0 <= r.strength <= 100.0


def test_missing_components_never_crash_and_stay_in_range():
    r = score_zone(swing_count=0, touches=0, rejections=0, volume_ratio=None,
                   last_touch_index=None, n_bars=0, retest_status="NONE",
                   confirmed_other_tfs=None, n_other_tfs_available=None)
    assert 0.0 <= r.strength <= 100.0


def test_failed_breakout_scores_lower_than_successful_retest_all_else_equal():
    base = dict(swing_count=2, touches=3, rejections=1, volume_ratio=1.0,
               last_touch_index=50, n_bars=100, confirmed_other_tfs=1, n_other_tfs_available=1)
    ok = score_zone(retest_status="SUCCESSFUL", **base)
    bad = score_zone(retest_status="FAILED", **base)
    assert ok.strength > bad.strength


def test_more_swings_never_lowers_the_score_all_else_equal():
    base = dict(touches=2, rejections=1, volume_ratio=1.0, last_touch_index=50, n_bars=100,
               retest_status="NONE", confirmed_other_tfs=1, n_other_tfs_available=1)
    low = score_zone(swing_count=1, **base)
    high = score_zone(swing_count=4, **base)
    assert high.strength >= low.strength
