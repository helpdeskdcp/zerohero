import datetime as dt
import math

import pytest

from app.research_engines.hybrid_mp.option_model import (Costs, bs_greeks, bs_price, nifty_weekly_expiry, plan_option_trade,
                                                         premium_stop_from_index_stop, round_trip_cost_rupees, years_to_expiry)


def test_put_call_parity_and_delta_gamma_by_finite_difference():
    S, K, T, s, r = 24000.0, 24050.0, 3 / 365, 0.14, 0.065
    c, p = bs_price(S, K, T, s, True, r), bs_price(S, K, T, s, False, r)
    assert c - p == pytest.approx(S - K * math.exp(-r * T), abs=1e-6)
    g = bs_greeks(S, K, T, s, True, r)
    h = 0.5
    d_num = (bs_price(S + h, K, T, s, True, r) - bs_price(S - h, K, T, s, True, r)) / (2 * h)
    g_num = (bs_price(S + h, K, T, s, True, r) - 2 * c + bs_price(S - h, K, T, s, True, r)) / (h * h)
    assert g["delta"] == pytest.approx(d_num, abs=1e-5) and g["gamma"] == pytest.approx(g_num, rel=1e-3)
    assert bs_greeks(S, K, T, s, False, r)["delta"] == pytest.approx(g["delta"] - 1.0, abs=1e-9)
    assert g["theta_per_day"] < 0


def test_expiry_rule_thursday_then_tuesday():
    assert nifty_weekly_expiry(dt.date(2025, 8, 25)).weekday() == 3          # Monday -> that Thursday
    assert nifty_weekly_expiry(dt.date(2025, 9, 1)) == dt.date(2025, 9, 2)   # Tuesday regime from September 2025
    d = dt.date(2026, 10, 13)                                                 # a Tuesday
    assert nifty_weekly_expiry(d) == d and nifty_weekly_expiry(d, after_close=True) == dt.date(2026, 10, 20)
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    assert years_to_expiry(dt.datetime(2026, 10, 13, 15, 30, tzinfo=ist), d) == 0.0


def test_premium_stop_is_derived_from_delta_gamma_not_from_the_index_stop():
    stop, loss = premium_stop_from_index_stop(entry_ask=100.0, delta=0.5, gamma=0.002, index_risk_points=40.0, side=1)
    assert loss == pytest.approx(0.5 * 40 - 0.5 * 0.002 * 1600) and stop == pytest.approx(100 - loss)
    assert premium_stop_from_index_stop(10.0, 0.9, 0.0, 500.0, 1) == (0.0, 10.0)      # loss capped at the premium paid


def test_sizing_respects_capital_lot_size_and_risk_budget():
    c = Costs()
    p = plan_option_trade(capital=12000, risk_pct=1.0, entry_ask=110.0, lot_size=75, delta=0.5, gamma=0.002, index_risk_points=30, side=1)
    assert not p.feasible and p.reason == "ONE_LOT_EXCEEDS_RISK_BUDGET"               # 1 lot (8,250) fits the capital but risks ~1,144 vs a 120 budget
    ok = plan_option_trade(capital=500000, risk_pct=1.0, entry_ask=110.0, lot_size=75, delta=0.5, gamma=0.002, index_risk_points=30, side=1)
    assert ok.feasible and ok.lots >= 1 and ok.max_loss <= 500000 * 0.01 + 1e-6 and ok.capital_used <= 500000
    assert ok.premium_stop < 110.0 and round(ok.premium_stop / 0.05) == pytest.approx(ok.premium_stop / 0.05, abs=1e-6)     # on the 0.05 tick
    tiny = plan_option_trade(capital=3000, risk_pct=1.0, entry_ask=110.0, lot_size=75, delta=0.5, gamma=0.002, index_risk_points=30, side=-1)
    assert not tiny.feasible and tiny.reason == "ONE_LOT_EXCEEDS_CAPITAL"
    assert plan_option_trade(100000, 1, 110.0, 75, 0.0, 0.0, 30, 1).reason == "DELTA_UNAVAILABLE"
    assert plan_option_trade(100000, 1, 0.0, 75, 0.5, 0.0, 30, 1).reason == "INVALID_CONTRACT_INPUT"
    assert c.stt_sell_pct == 0.10


def test_charges_scale_with_lots_and_include_stt_on_the_sell_leg_only():
    c = Costs()
    one = round_trip_cost_rupees(100.0, 120.0, 1, 75, c)
    two = round_trip_cost_rupees(100.0, 120.0, 2, 75, c)
    assert two > one and two < 2 * one + 1e-9 + 2 * c.brokerage_per_order * 1.18          # brokerage is per order, not per lot
    stt_only = round_trip_cost_rupees(100.0, 200.0, 1, 75, Costs(brokerage_per_order=0, exchange_txn_pct=0, sebi_per_crore=0, stamp_buy_pct=0))
    assert stt_only == pytest.approx(200.0 * 75 * 0.001)
