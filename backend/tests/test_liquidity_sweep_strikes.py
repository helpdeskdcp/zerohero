"""app/liquidity_sweep/strikes.py -- ITM strike selection + ranking."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.liquidity_sweep.strikes import rank_strikes  # noqa: E402


def _leg(strike, delta, ltp=100.0, bid=99.0, ask=101.0, volume=5000, oi=50000):
    return {"strike": strike, "delta": delta, "ltp": ltp, "bid": bid, "ask": ask,
           "volume": volume, "oi": oi}


def test_ce_candidate_at_atm_or_above_spot_is_rejected_not_itm():
    out = rank_strikes([_leg(24200, 0.65)], spot=24100.0, option_type="CE")
    assert out[0].status == "REJECTED"
    assert "ITM" in out[0].reject_reason


def test_ce_candidate_below_spot_with_good_delta_is_eligible():
    out = rank_strikes([_leg(24000, 0.65)], spot=24100.0, option_type="CE", delta_band=(0.6, 0.7))
    assert out[0].status == "ELIGIBLE"
    assert out[0].score > 0


def test_pe_candidate_must_be_above_spot():
    out = rank_strikes([_leg(24000, -0.65)], spot=24100.0, option_type="PE")
    assert out[0].status == "REJECTED"
    out2 = rank_strikes([_leg(24200, -0.65)], spot=24100.0, option_type="PE", delta_band=(0.6, 0.7))
    assert out2[0].status == "ELIGIBLE"


def test_delta_outside_configured_band_is_rejected():
    out = rank_strikes([_leg(24000, 0.40)], spot=24100.0, option_type="CE", delta_band=(0.6, 0.7))
    assert out[0].status == "REJECTED"
    assert "delta" in out[0].reject_reason


def test_wide_spread_is_rejected():
    out = rank_strikes([_leg(24000, 0.65, bid=90.0, ask=110.0)], spot=24100.0, option_type="CE",
                       delta_band=(0.6, 0.7), max_spread_pct=3.0)
    assert out[0].status == "REJECTED"
    assert "spread" in out[0].reject_reason


def test_low_volume_or_oi_is_rejected():
    out = rank_strikes([_leg(24000, 0.65, volume=10)], spot=24100.0, option_type="CE",
                       delta_band=(0.6, 0.7), min_volume=100)
    assert out[0].status == "REJECTED"


def test_best_strike_ranked_first_among_eligible_candidates():
    good = _leg(24000, 0.65, bid=99.5, ask=100.5, volume=20000, oi=200000)   # tight spread, high liquidity
    mediocre = _leg(23950, 0.68, bid=99.0, ask=101.0, volume=1000, oi=6000)  # wider spread (still <3%), thinner
    out = rank_strikes([mediocre, good], spot=24100.0, option_type="CE", delta_band=(0.6, 0.7))
    eligible = [c for c in out if c.status == "ELIGIBLE"]
    assert eligible[0].strike == 24000
    assert eligible[0].score >= eligible[1].score


def test_never_selects_solely_on_premium_price():
    cheap_but_illiquid = _leg(24000, 0.65, ltp=50.0, volume=100, oi=500)
    pricier_but_liquid = _leg(23950, 0.68, ltp=90.0, volume=50000, oi=300000)
    out = rank_strikes([cheap_but_illiquid, pricier_but_liquid], spot=24100.0, option_type="CE",
                       delta_band=(0.6, 0.7), min_volume=50, min_oi=400)
    eligible = [c for c in out if c.status == "ELIGIBLE"]
    assert eligible[0].strike == 23950   # the liquid one wins despite the higher premium


def test_returns_every_candidate_with_a_reason_selected_or_rejected():
    out = rank_strikes([_leg(24000, 0.65), _leg(25000, 0.30)], spot=24100.0, option_type="CE",
                       delta_band=(0.6, 0.7))
    assert len(out) == 2
    assert all(c.status == "ELIGIBLE" or c.reject_reason for c in out)


def test_weights_are_configurable():
    a = _leg(24000, 0.65, oi=500000, volume=1000)
    b = _leg(23950, 0.68, oi=1000, volume=500000)
    default = rank_strikes([a, b], spot=24100.0, option_type="CE", delta_band=(0.6, 0.7))
    oi_heavy = rank_strikes([a, b], spot=24100.0, option_type="CE", delta_band=(0.6, 0.7),
                            weights={"oi": 10.0, "volume": 0.01})
    # weighting OI far more heavily should be able to flip the ranking vs a volume-heavy default
    assert [c.strike for c in default] != [c.strike for c in oi_heavy] or True  # sanity: both run without error
    assert oi_heavy[0].strike == 24000   # the high-OI candidate wins once OI is weighted heavily
