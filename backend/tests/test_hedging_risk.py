"""app.hedging.risk -- pure math, no I/O. Every case uses concrete numbers
worked out by hand so the assertions prove the formula, not just "a number
came back"."""
import pytest

from app.hedging import risk as R


def test_credit_spread_economics_ce():
    # sold 24500 CE @ 60, bought 24700 CE @ 20 (width 200), lot_size 65
    e = R.credit_spread_economics(primary_premium=60.0, hedge_premium=20.0,
                                  primary_strike=24500, hedge_strike=24700,
                                  option_type="CE", lot_size=65)
    assert e.net_credit_per_unit == 40.0
    assert e.strike_width == 200
    # max_loss = width*lot - net_credit*lot = 200*65 - 40*65 = 13000-2600=10400
    assert e.max_loss_per_lot == 10400.0
    assert e.max_profit_per_lot == 40.0 * 65


def test_credit_spread_economics_pe():
    # sold 24500 PE @ 55, bought 24300 PE @ 18 (width 200), lot_size 65
    e = R.credit_spread_economics(primary_premium=55.0, hedge_premium=18.0,
                                  primary_strike=24500, hedge_strike=24300,
                                  option_type="PE", lot_size=65)
    assert e.strike_width == 200
    assert e.net_credit_per_unit == 37.0
    assert e.max_loss_per_lot == 200 * 65 - 37 * 65


def test_credit_spread_rejects_wrong_direction_hedge():
    # CE hedge strike must be > primary strike -- a lower/equal strike isn't a real hedge
    assert R.credit_spread_economics(primary_premium=60, hedge_premium=90,
                                     primary_strike=24500, hedge_strike=24300,
                                     option_type="CE", lot_size=65) is None
    assert R.credit_spread_economics(primary_premium=60, hedge_premium=90,
                                     primary_strike=24500, hedge_strike=24500,
                                     option_type="CE", lot_size=65) is None


def test_credit_spread_net_debit_still_computes_a_defined_max_loss():
    # hedge more expensive than primary premium (net debit) -- still a valid,
    # if unusual, defined-risk structure; max_loss must never go negative.
    e = R.credit_spread_economics(primary_premium=20.0, hedge_premium=25.0,
                                  primary_strike=24500, hedge_strike=24700,
                                  option_type="CE", lot_size=65)
    assert e.net_credit_per_unit == -5.0
    assert e.max_profit_per_lot == 0.0        # floored, never negative
    assert e.max_loss_per_lot == 200 * 65 - (-5.0) * 65   # == 200*65 + 5*65


def test_credit_spread_invalid_option_type():
    assert R.credit_spread_economics(primary_premium=1, hedge_premium=1,
                                     primary_strike=100, hedge_strike=200,
                                     option_type="FUT", lot_size=1) is None


def test_net_delta_exposure_unavailable_when_missing_inputs():
    assert R.net_delta_exposure(primary_delta=None, hedge_delta=0.1, primary_side="SELL",
                                lot_size=65, lots=1, spot=24500)["status"] == "UNAVAILABLE"
    assert R.net_delta_exposure(primary_delta=0.5, hedge_delta=0.1, primary_side="SELL",
                                lot_size=65, lots=1, spot=None)["status"] == "UNAVAILABLE"


def test_net_delta_exposure_sold_leg_delta_is_negated():
    # sold CE delta 0.5 -> position delta -0.5; bought hedge delta 0.1 -> +0.1
    # net = -0.4 * lot_size(65) * lots(2) = -52; exposure = -52 * spot(24500)
    out = R.net_delta_exposure(primary_delta=0.5, hedge_delta=0.1, primary_side="SELL",
                               lot_size=65, lots=2, spot=24500)
    assert out["status"] == "OK"
    assert out["net_delta"] == pytest.approx(-52.0)
    assert out["exposure_rupees"] == pytest.approx(-52.0 * 24500)


def test_risk_reduction_unbounded_to_defined_when_no_reference():
    out = R.risk_reduction_pct(unhedged_max_loss=None, hedged_max_loss=10400)
    assert out["status"] == "UNBOUNDED_TO_DEFINED"
    assert out["pct"] is None


def test_risk_reduction_with_a_real_reference():
    out = R.risk_reduction_pct(unhedged_max_loss=50000, hedged_max_loss=10400)
    assert out["status"] == "OK"
    assert out["pct"] == pytest.approx((50000 - 10400) / 50000 * 100.0)


def test_risk_reduction_never_negative_when_hedge_somehow_costs_more():
    out = R.risk_reduction_pct(unhedged_max_loss=5000, hedged_max_loss=8000)
    assert out["status"] == "OK"
    assert out["pct"] == 0.0    # floored, never a negative "reduction"
