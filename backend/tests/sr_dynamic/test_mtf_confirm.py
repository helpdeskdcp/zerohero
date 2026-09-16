"""Requirement 9: multi-timeframe confirmation."""
from app.sr_dynamic.clustering import SRZone
from app.sr_dynamic.mtf_confirm import confirmed_timeframes


def _zone(level):
    return SRZone(level=level, zone_low=level - 0.1, zone_high=level + 0.1)


def test_confirms_only_timeframes_with_a_nearby_independent_zone():
    zones_by_tf = {
        "5m": [_zone(282.20)],
        "15m": [_zone(282.30)],
        "30m": [_zone(290.00)],     # far away -- not a confirmation
    }
    tfs = confirmed_timeframes(282.20, zones_by_tf, atr=1.0, tolerance_atr_mult=0.3)
    assert tfs == ["5m", "15m"]


def test_missing_timeframe_data_is_just_absent_not_a_crash():
    tfs = confirmed_timeframes(282.20, {"5m": [_zone(282.20)]}, atr=1.0)
    assert tfs == ["5m"]


def test_zero_or_missing_atr_returns_empty():
    assert confirmed_timeframes(282.20, {"5m": [_zone(282.20)]}, atr=0.0) == []


def test_order_follows_tf_order_not_dict_insertion_order():
    zones_by_tf = {
        "30m": [_zone(282.20)],
        "5m": [_zone(282.20)],
        "15m": [_zone(282.20)],
    }
    assert confirmed_timeframes(282.20, zones_by_tf, atr=1.0) == ["5m", "15m", "30m"]
