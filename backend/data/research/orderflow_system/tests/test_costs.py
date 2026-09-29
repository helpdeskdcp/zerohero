"""engine.costs.estimate_index_future_cost -- hand-computed component checks,
same discipline as backend/tests/test_institutional_edge_costs.py."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

from engine.costs import FuturesRateCard, estimate_index_future_cost


def test_never_labeled_ok():
    r = estimate_index_future_cost(22000.0, 22100.0, 65)
    assert r["status"] == "FUTURES_FORMULA_ESTIMATE"


def test_matches_hand_computed_components():
    entry, exit_, lot_size = 22000.0, 22100.0, 65
    r = estimate_index_future_cost(entry, exit_, lot_size, lots=1, slippage_points=0.0)
    rates = FuturesRateCard()
    buy_notional, sell_notional = entry * lot_size, exit_ * lot_size

    expected_brokerage = rates.brokerage_per_order * 2
    expected_exchange = (buy_notional + sell_notional) * rates.exchange_txn_pct
    expected_stt = sell_notional * rates.stt_sell_pct
    expected_stamp = buy_notional * rates.stamp_duty_buy_pct
    expected_sebi = (buy_notional + sell_notional) * rates.sebi_turnover_pct
    expected_gst = (expected_brokerage + expected_exchange + expected_sebi) * rates.gst_pct

    # abs=1e-2 -- the function itself rounds every component to 2 decimals
    # before returning; a tighter tolerance would fail on that rounding, not
    # on an actual calculation error.
    assert r["brokerage"] == pytest.approx(expected_brokerage, abs=1e-2)
    assert r["exchange_txn"] == pytest.approx(expected_exchange, abs=1e-2)
    assert r["stt"] == pytest.approx(expected_stt, abs=1e-2)
    assert r["stamp_duty"] == pytest.approx(expected_stamp, abs=1e-2)
    assert r["sebi_fee"] == pytest.approx(expected_sebi, abs=1e-2)
    assert r["gst"] == pytest.approx(expected_gst, abs=1e-2)


def test_gst_never_applied_to_stt_or_stamp_duty():
    """GST is a tax on services (brokerage/exchange/SEBI fees); STT and
    stamp duty are themselves taxes -- GST-on-a-tax would be a real,
    material overstatement of cost."""
    r = estimate_index_future_cost(22000.0, 21900.0, 65)
    rates = FuturesRateCard()
    non_gst_base = r["stt"] + r["stamp_duty"]
    max_possible_gst = (r["brokerage"] + r["exchange_txn"] + r["sebi_fee"]) * rates.gst_pct
    assert r["gst"] == pytest.approx(max_possible_gst, abs=1e-2)
    assert r["gst"] < non_gst_base * 10  # sanity: gst isn't accidentally scaled off stt/stamp at all


def test_stt_is_sell_side_only():
    """Same entry/exit price -- STT should be based on the SELL leg's
    notional only, not double-counted on both legs."""
    r = estimate_index_future_cost(22000.0, 22000.0, 65, lots=1)
    rates = FuturesRateCard()
    assert r["stt"] == pytest.approx(22000.0 * 65 * rates.stt_sell_pct, abs=1e-6)


def test_stamp_duty_is_buy_side_only():
    r = estimate_index_future_cost(22000.0, 25000.0, 65, lots=1)
    rates = FuturesRateCard()
    assert r["stamp_duty"] == pytest.approx(22000.0 * 65 * rates.stamp_duty_buy_pct, abs=1e-6)


def test_cost_scales_with_lots():
    one_lot = estimate_index_future_cost(22000.0, 22100.0, 65, lots=1)
    two_lots = estimate_index_future_cost(22000.0, 22100.0, 65, lots=2)
    assert two_lots["total_cost"] > one_lot["total_cost"]
    # brokerage is flat per order regardless of lot count -- per-point cost
    # should DECREASE with more lots (brokerage amortized over more quantity),
    # same real economy-of-scale already confirmed for the options cost model.
    assert two_lots["total_cost_points"] < one_lot["total_cost_points"]


def test_slippage_cost_is_linear_in_points_and_quantity():
    r0 = estimate_index_future_cost(22000.0, 22100.0, 65, lots=1, slippage_points=0.0)
    r1 = estimate_index_future_cost(22000.0, 22100.0, 65, lots=1, slippage_points=1.0)
    assert r1["slippage_cost"] - r0["slippage_cost"] == pytest.approx(65.0, abs=1e-6)


def test_rate_card_can_be_overridden_for_sensitivity_checks():
    custom = FuturesRateCard(brokerage_per_order=0.0, exchange_txn_pct=0.0, stt_sell_pct=0.0,
                             stamp_duty_buy_pct=0.0, sebi_turnover_pct=0.0, gst_pct=0.0)
    r = estimate_index_future_cost(22000.0, 22100.0, 65, slippage_points=0.0, rates=custom)
    assert r["total_cost"] == 0.0
