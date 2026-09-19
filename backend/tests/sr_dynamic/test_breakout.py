"""Requirements 5-7: confirmed breakout (close-only), retest (successful/
failed), and support<->resistance flip. Requirement 12/13: never repaint,
no future candles."""
from app.sr_dynamic.breakout import (
    CONFIRMED,
    FAILED,
    NONE,
    PENDING,
    RESISTANCE_TO_SUPPORT,
    SUCCESSFUL,
    SUPPORT_TO_RESISTANCE,
    analyze_breakout_retest_flip,
)

ZONE_LOW, ZONE_HIGH, ATR = 100.0, 101.0, 1.0


def test_no_data_degrades_without_crashing():
    r = analyze_breakout_retest_flip(ZONE_LOW, ZONE_HIGH, [], ATR)
    assert r.breakout_status == NONE


def test_price_below_zone_breaking_up_is_a_confirmed_breakout():
    closes = [98.0, 98.5, 99.0, 99.5, 102.0]     # last close clears zone_high+buffer
    r = analyze_breakout_retest_flip(ZONE_LOW, ZONE_HIGH, closes, ATR)
    assert r.breakout_status == CONFIRMED
    assert r.breakout_direction == "UP"
    assert r.breakout_count == 1
    assert r.retest_status == PENDING


def test_breakout_up_then_pullback_then_continuation_is_a_successful_retest_and_flip():
    closes = [98.0, 99.0, 102.0,   # breakout UP confirmed
              100.5,                # pulls back INTO the zone (retest)
              102.5]                # continues up -> successful retest, flip
    r = analyze_breakout_retest_flip(ZONE_LOW, ZONE_HIGH, closes, ATR)
    assert r.retest_status == SUCCESSFUL
    assert r.flip_status == RESISTANCE_TO_SUPPORT
    assert r.successful_retest_count == 1


def test_breakout_up_then_full_reversal_below_zone_is_a_failed_breakout():
    closes = [98.0, 99.0, 102.0,    # breakout UP confirmed
              98.5]                 # reverses straight back below the zone
    r = analyze_breakout_retest_flip(ZONE_LOW, ZONE_HIGH, closes, ATR)
    assert r.retest_status == FAILED
    assert r.flip_status == NONE
    assert r.failed_breakout_count == 1


def test_breakout_up_then_retest_then_reversal_is_also_a_failed_breakout():
    closes = [98.0, 99.0, 102.0,   # breakout UP
              100.5,                # retest touch
              98.5]                 # reverses below after the retest -> failed
    r = analyze_breakout_retest_flip(ZONE_LOW, ZONE_HIGH, closes, ATR)
    assert r.retest_status == FAILED
    assert r.failed_breakout_count == 1
    assert r.successful_retest_count == 0


def test_breakdown_then_retest_then_continuation_is_support_to_resistance_flip():
    closes = [103.0, 102.0, 98.0,   # breakdown confirmed (was support)
              100.5,                 # retest touch from below
              97.5]                  # continues down -> successful retest
    r = analyze_breakout_retest_flip(ZONE_LOW, ZONE_HIGH, closes, ATR)
    assert r.breakout_direction == "DOWN"
    assert r.retest_status == SUCCESSFUL
    assert r.flip_status == SUPPORT_TO_RESISTANCE


def test_no_breakout_no_events():
    closes = [100.2, 100.5, 100.3, 100.6]   # hovers inside the zone the whole time
    r = analyze_breakout_retest_flip(ZONE_LOW, ZONE_HIGH, closes, ATR)
    assert r.breakout_status == NONE
    assert r.breakout_count == 0


def test_growing_history_never_repaints_a_confirmed_event():
    """Two branches identical up to the point of divergence: the ALREADY
    confirmed breakout+successful-retest+flip must stay identical whether or
    not more (unrelated, later) bars are appended afterward -- the same
    anti-repaint methodology used for app.strategy_mtf.htf_resample."""
    closes = [98.0, 99.0, 102.0, 100.5, 102.5]
    early = analyze_breakout_retest_flip(ZONE_LOW, ZONE_HIGH, closes, ATR)
    assert early.flip_status == RESISTANCE_TO_SUPPORT
    later = analyze_breakout_retest_flip(ZONE_LOW, ZONE_HIGH, closes + [103.0, 104.0, 105.0], ATR)
    assert later.retest_status == early.retest_status
    assert later.flip_status == early.flip_status
    assert later.successful_retest_count == early.successful_retest_count
    assert later.breakout_count == early.breakout_count
