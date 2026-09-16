"""Requirement 4: touch count, rejection count, rejection strength, volume ratio."""
from app.sr_dynamic.touches import analyze_touches


def test_no_data_degrades_without_crashing():
    r = analyze_touches(100, 101, [], [], [], [], atr=1.0)
    assert r.touches == 0 and r.rejections == 0 and r.volume_ratio is None


def test_zero_atr_degrades_without_crashing():
    r = analyze_touches(100, 101, [101, 102], [99, 100], [100, 101], [10, 10], atr=0.0)
    assert r.touches == 0


def test_counts_a_touch_and_a_clean_rejection():
    # zone at 100-101; bar 2 pierces it then bar 3 closes well away -> rejection
    H = [105, 105, 101.2, 106, 107]
    L = [104, 104, 99.8, 105, 106]
    C = [104.5, 104.5, 100.5, 106, 107]   # bar3 close 106 -> 5 ATR away (atr=1) -> rejection
    V = [100, 100, 100, 100, 100]
    r = analyze_touches(100, 101, H, L, C, V, atr=1.0)
    assert r.touches == 1
    assert r.rejections == 1
    assert r.rejection_strength > 0


def test_a_touch_with_no_follow_through_is_not_a_rejection():
    # price hovers glued to the zone -- every bar overlaps the touch band
    # (since staying within the 0.3*ATR "not a rejection" range necessarily
    # overlaps the wider +/-0.15*ATR touch band), so this correctly counts
    # as several touches with zero rejections, not one isolated touch.
    H = [105, 105, 101.2, 100.55, 100.5]
    L = [104, 104, 99.8, 100.45, 100.4]
    C = [104.5, 104.5, 100.5, 100.5, 100.4]
    V = [100, 100, 100, 100, 100]
    r = analyze_touches(100, 101, H, L, C, V, atr=1.0)
    assert r.touches == 3
    assert r.rejections == 0
    assert r.rejection_strength == 0.0


def test_volume_ratio_flags_above_average_volume_on_the_touch():
    H = [105, 105, 101.2, 106, 107]
    L = [104, 104, 99.8, 105, 106]
    C = [104.5, 104.5, 100.5, 106, 107]
    V = [100, 100, 500, 100, 100]   # the touch bar has 5x the baseline volume
    r = analyze_touches(100, 101, H, L, C, V, atr=1.0)
    assert r.volume_ratio is not None and r.volume_ratio > 2.0


def test_no_real_volume_data_returns_none_not_a_fabricated_ratio():
    H = [101.2, 106]
    L = [99.8, 105]
    C = [100.5, 106]
    V = [0.0, 0.0]
    r = analyze_touches(100, 101, H, L, C, V, atr=1.0)
    assert r.volume_ratio is None
