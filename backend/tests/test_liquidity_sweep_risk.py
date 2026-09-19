"""app/liquidity_sweep/risk.py -- SL/target/2R gate + position sizing
(wrapping the existing app.engines.risk_engine.run_risk_engine)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.liquidity_sweep.risk import MIN_RR, build_plan, size_position


def test_bullish_plan_sl_below_sweep_extreme_and_2r_target():
    plan = build_plan(direction="BULLISH", entry=100.0, sweep_extreme=95.0, buffer_pts=1.0)
    assert plan.status == "OK"
    assert plan.stop_loss == 94.0                 # sweep_extreme - buffer
    assert plan.risk_distance == 6.0               # entry - sl
    assert plan.target_1 == 100.0 + 2 * 6.0        # exactly 2R when no better liquidity target given
    assert plan.rr == MIN_RR


def test_bearish_plan_sl_above_sweep_extreme():
    plan = build_plan(direction="BEARISH", entry=100.0, sweep_extreme=105.0, buffer_pts=1.0)
    assert plan.status == "OK"
    assert plan.stop_loss == 106.0
    assert plan.target_1 == 100.0 - 2 * 6.0


def test_a_farther_real_liquidity_target_is_preferred_over_the_bare_2r():
    plan = build_plan(direction="BULLISH", entry=100.0, sweep_extreme=95.0, buffer_pts=1.0,
                      next_liquidity_target=130.0)   # far beyond the 2R minimum of 112
    assert plan.status == "OK"
    assert plan.target_1 == 130.0
    assert plan.rr > MIN_RR


def test_a_closer_liquidity_target_than_2r_is_ignored_not_used():
    plan = build_plan(direction="BULLISH", entry=100.0, sweep_extreme=95.0, buffer_pts=1.0,
                      next_liquidity_target=103.0)   # closer than the 2R minimum
    assert plan.status == "OK"
    assert plan.target_1 == 100.0 + 2 * 6.0   # falls back to the 2R floor, not the closer pool


def test_non_positive_stop_distance_is_no_trade():
    plan = build_plan(direction="BULLISH", entry=100.0, sweep_extreme=105.0, buffer_pts=1.0)
    # sweep_extreme above entry for a BULLISH plan -> SL would be above entry -> invalid
    assert plan.status == "NO_TRADE"


def test_unknown_direction_is_no_trade():
    plan = build_plan(direction="SIDEWAYS", entry=100.0, sweep_extreme=95.0, buffer_pts=1.0)
    assert plan.status == "NO_TRADE"


def test_size_position_approves_within_risk_budget():
    out = size_position(option_entry=20.0, option_stop=16.0, capital=200000, risk_pct=1.0, lot_size=50)
    assert out["risk_status"] == "APPROVED"
    assert out["allowed_quantity"] > 0
    assert out["allowed_quantity"] % 50 == 0


def test_size_position_rejects_when_risk_budget_smaller_than_one_lot():
    out = size_position(option_entry=1000.0, option_stop=990.0, capital=1000, risk_pct=1.0, lot_size=50)
    assert out["risk_status"] == "REJECTED"


def test_size_position_never_exceeds_the_configured_risk_percent():
    out = size_position(option_entry=20.0, option_stop=15.0, capital=100000, risk_pct=2.0, lot_size=50)
    max_risk = 100000 * 0.02
    actual_risk = out["allowed_quantity"] * abs(20.0 - 15.0)
    assert actual_risk <= max_risk
