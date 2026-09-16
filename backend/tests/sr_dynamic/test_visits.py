"""Requirements 4/5 of the live-wiring spec + test list items 1,2,3,4,9,10,15:
support/resistance touch, rejection (N-bar confirmation window), no
tick/bar-overlap double counting within one visit, zone re-entry, no-lookahead."""
from app.sr_dynamic.visits import latest_touch_state

ZONE_LOW, ZONE_HIGH, ATR = 279.70, 280.10, 1.0


def test_no_data_degrades_without_crashing():
    r = latest_touch_state(ZONE_LOW, ZONE_HIGH, [], [], [], ATR)
    assert r.touching is False and r.visit_count == 0


def test_support_touch_true_while_price_is_inside_the_zone():
    # 280.30 -> 280.05 -> 279.90 (matches the spec's own worked example)
    H = [280.35, 280.10, 279.95]
    L = [280.25, 280.00, 279.85]
    C = [280.30, 280.05, 279.90]
    r = latest_touch_state(ZONE_LOW, ZONE_HIGH, H, L, C, ATR)
    assert r.touching is True
    assert r.visit_count == 1


def test_resistance_touch_symmetric_case():
    zlo, zhi = 288.0, 288.4
    H = [287.8, 288.1, 288.3]
    L = [287.6, 287.9, 288.1]
    C = [287.7, 288.0, 288.2]
    r = latest_touch_state(zlo, zhi, H, L, C, ATR)
    assert r.touching is True


def test_repeated_bars_inside_the_same_zone_count_as_one_visit_not_several():
    H = [280.30, 280.05, 279.95, 279.90, 280.00]
    L = [280.20, 279.95, 279.85, 279.80, 279.90]
    C = [280.25, 280.00, 279.90, 279.85, 279.95]
    r = latest_touch_state(ZONE_LOW, ZONE_HIGH, H, L, C, ATR)
    assert r.visit_count == 1          # NOT 5 -- one continuous visit


def test_leaving_and_returning_counts_a_second_visit():
    # inside, then clearly away, then back inside
    H = [280.00, 283.0, 284.0, 280.00]
    L = [279.90, 282.8, 283.8, 279.90]
    C = [279.95, 283.0, 284.0, 279.95]
    r = latest_touch_state(ZONE_LOW, ZONE_HIGH, H, L, C, ATR)
    assert r.visit_count == 2


def test_support_rejection_confirmed_within_the_window():
    # touch at bar 0, then bar 1 closes >= 0.3 ATR away -> rejection
    H = [280.00, 281.5]
    L = [279.90, 281.3]
    C = [279.95, 281.4]
    r = latest_touch_state(ZONE_LOW, ZONE_HIGH, H, L, C, ATR, confirmation_bars=2)
    assert r.rejected is True
    assert r.rejection_pending is False


def test_rejection_is_pending_not_fabricated_when_too_few_bars_have_passed():
    # touch on the LAST bar -- no bars yet exist to judge a reaction
    H = [285.0, 280.00]
    L = [284.8, 279.90]
    C = [284.9, 279.95]
    r = latest_touch_state(ZONE_LOW, ZONE_HIGH, H, L, C, ATR, confirmation_bars=2)
    assert r.touching is True
    assert r.rejection_pending is True
    assert r.rejected is False


def test_a_touch_with_no_reaction_is_not_a_fabricated_rejection():
    H = [280.00, 280.05, 280.02]
    L = [279.90, 279.95, 279.92]
    C = [279.95, 280.00, 279.98]     # stays glued -- never moves 0.3 ATR away
    r = latest_touch_state(ZONE_LOW, ZONE_HIGH, H, L, C, ATR, confirmation_bars=2)
    assert r.touching is True    # still inside -> pending, not "not rejected forever"
    assert r.rejection_pending is True


def test_growing_history_never_repaints_an_already_resolved_rejection():
    H = [280.00, 281.5, 282.0, 283.0]
    L = [279.90, 281.3, 281.8, 282.8]
    C = [279.95, 281.4, 282.0, 283.0]
    early = latest_touch_state(ZONE_LOW, ZONE_HIGH, H[:2], L[:2], C[:2], ATR, confirmation_bars=2)
    later = latest_touch_state(ZONE_LOW, ZONE_HIGH, H, L, C, ATR, confirmation_bars=2)
    assert early.rejected == later.rejected == True
    assert early.visit_count == later.visit_count == 1
