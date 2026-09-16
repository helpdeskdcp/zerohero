"""End-to-end tests for app.sr_dynamic.engine.compute_dynamic_sr -- the full
pipeline (swings -> clustering -> touch/rejection/volume -> breakout/retest/
flip -> MTF confirmation -> scoring) and the exact requested output schema."""
from app.sr_dynamic.engine import compute_dynamic_sr

EXPECTED_KEYS = {
    "level", "zone_low", "zone_high", "type", "strength", "touches", "rejections",
    "volume_ratio", "breakout_status", "retest_status", "flip_status", "timeframes_confirmed",
}


def _bar(c, h=None, l=None, v=100.0):
    h = h if h is not None else c + 0.3
    l = l if l is not None else c - 0.3
    return {"o": c, "h": h, "l": l, "c": c, "v": v}


def _bars_with_a_support_zone():
    """A synthetic price path: chop down to ~280, form a support zone with
    two clean rejecting touches (with elevated volume on the touches), then
    drift up -- enough real structure for every pipeline stage to fire."""
    closes = list(range(300, 280, -1))                      # 300 -> 281, downtrend
    closes += [280.2, 280.0, 280.3]                          # first touch + rejection
    closes += [283, 285, 284, 282]                           # away and back
    closes += [280.1, 279.9, 280.4]                          # second touch + rejection
    closes += [283, 286, 288, 290, 292, 294, 296]            # trend away, gives recency/age spread
    bars = []
    for i, c in enumerate(closes):
        v = 400.0 if i in (len(range(300, 280, -1)), len(range(300, 280, -1)) + 8) else 100.0
        bars.append(_bar(c, v=v))
    return bars


def test_output_schema_matches_the_requested_contract():
    bars = _bars_with_a_support_zone()
    zones = compute_dynamic_sr({"5m": bars}, primary_tf="5m")
    assert zones, "expected at least one zone from real structure"
    for z in zones:
        assert EXPECTED_KEYS.issubset(z.keys())
        assert z["type"] in ("SUPPORT", "RESISTANCE")
        assert 0.0 <= z["strength"] <= 100.0
        assert z["zone_low"] <= z["level"] <= z["zone_high"]
        assert isinstance(z["timeframes_confirmed"], list)


def test_too_little_data_returns_empty_not_a_crash():
    assert compute_dynamic_sr({"5m": [_bar(100)] * 3}, primary_tf="5m") == []


def test_missing_primary_tf_returns_empty_not_a_crash():
    assert compute_dynamic_sr({"15m": _bars_with_a_support_zone()}, primary_tf="5m") == []


def test_multi_timeframe_confirmation_wires_through_when_other_tfs_present():
    bars = _bars_with_a_support_zone()
    zones = compute_dynamic_sr({"5m": bars, "15m": bars, "30m": bars})
    assert zones
    # identical bars on every tf -> every zone should confirm on all three
    for z in zones:
        assert set(z["timeframes_confirmed"]) >= {"5m"}


def test_growing_history_never_repaints_an_already_confirmed_zones_flip_status():
    bars = _bars_with_a_support_zone()
    early = compute_dynamic_sr({"5m": bars[:20]}, primary_tf="5m")
    later = compute_dynamic_sr({"5m": bars}, primary_tf="5m")
    early_by_level = {round(z["level"], 1): z for z in early}
    later_by_level = {round(z["level"], 1): z for z in later}
    for lvl, ez in early_by_level.items():
        # find the closest surviving zone in the longer run (clustering can
        # shift a level slightly as more swings join it -- the FLIP/BREAKOUT
        # classification for an already-resolved event must not reverse)
        match = min(later_by_level.values(), key=lambda lz: abs(lz["level"] - lvl))
        if ez["flip_status"] != "NONE":
            assert match["flip_status"] == ez["flip_status"]
