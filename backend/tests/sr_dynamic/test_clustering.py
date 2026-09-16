"""Requirement 3: price-distance clustering of swing pivots into zones."""
from app.sr_dynamic.pivots import SwingPivot
from app.sr_dynamic.clustering import cluster_swings


def test_two_nearby_swings_merge_into_one_zone():
    swings = [SwingPivot(index=3, price=282.20, kind="L"),
              SwingPivot(index=20, price=282.35, kind="L")]
    zones = cluster_swings(swings, n_bars=30, merge=0.5)
    assert len(zones) == 1
    z = zones[0]
    assert z.zone_low <= 282.20 and z.zone_high >= 282.35
    assert z.swing_count == 2


def test_far_apart_swings_stay_separate_zones():
    swings = [SwingPivot(index=3, price=280.0, kind="L"),
              SwingPivot(index=20, price=300.0, kind="H")]
    zones = cluster_swings(swings, n_bars=30, merge=0.5)
    assert len(zones) == 2


def test_empty_input_is_empty_not_a_crash():
    assert cluster_swings([], n_bars=10, merge=0.5) == []


def test_merge_distance_scales_the_grouping():
    swings = [SwingPivot(index=3, price=280.0, kind="L"),
              SwingPivot(index=20, price=281.0, kind="L")]
    tight = cluster_swings(swings, n_bars=30, merge=0.1)
    wide = cluster_swings(swings, n_bars=30, merge=2.0)
    assert len(tight) == 2
    assert len(wide) == 1


def test_zones_are_returned_sorted_by_level():
    swings = [SwingPivot(index=3, price=300.0, kind="H"),
              SwingPivot(index=20, price=280.0, kind="L")]
    zones = cluster_swings(swings, n_bars=30, merge=0.5)
    assert [z.level for z in zones] == sorted(z.level for z in zones)
