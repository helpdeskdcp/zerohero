"""
app/institutional_edge/costs.py -- itemized realistic cost model.
Pure functions, no DB.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.institutional_edge.costs import (
    RegulatoryRateCard, estimate_cost, estimate_index_option_cost, known_profiles,
)


def test_naturalgas_round_trip_matches_the_validated_audit_number():
    c = estimate_cost("MCX", "NATURALGAS_OPTION")
    assert c.status == "OK"
    assert c.round_trip_cost == 113.50
    assert c.total_cost == 113.50   # no slippage requested


def test_crudeoil_round_trip_matches_the_validated_audit_number():
    c = estimate_cost("MCX", "CRUDEOIL_OPTION")
    assert c.status == "OK"
    assert c.round_trip_cost == 184.50


def test_slippage_points_are_converted_to_rupees_via_lot_size():
    c = estimate_cost("MCX", "CRUDEOIL_OPTION", slippage_points=1.0)
    assert c.slippage_cost == 100.0   # lot size 100
    assert c.total_cost == 184.50 + 100.0


def test_unknown_instrument_is_honestly_uncalibrated_not_guessed():
    c = estimate_cost("NSE", "NIFTY_OPTION")
    assert c.status == "UNCALIBRATED"
    assert c.round_trip_cost is None
    assert c.total_cost is None
    assert "no validated cost profile" in c.note


def test_lookup_is_case_insensitive():
    c1 = estimate_cost("mcx", "naturalgas_option")
    c2 = estimate_cost("MCX", "NATURALGAS_OPTION")
    assert c1.status == c2.status == "OK"
    assert c1.round_trip_cost == c2.round_trip_cost


def test_total_cost_points_converts_rupees_to_the_scalp_signals_points_unit():
    c = estimate_cost("MCX", "NATURALGAS_OPTION")
    assert c.lot_size == 1250
    assert abs(c.total_cost_points - (113.50 / 1250)) < 1e-9


def test_known_profiles_lists_exactly_the_validated_instruments():
    profiles = known_profiles()
    keys = {(p["exchange"], p["segment"]) for p in profiles}
    assert keys == {("MCX", "NATURALGAS_OPTION"), ("MCX", "CRUDEOIL_OPTION")}
    for p in profiles:
        assert p["round_trip_total"] > 0
        assert p.get("validated_note")


# ---------------------------------------------------------------------------
# estimate_index_option_cost() -- NSE index options (NIFTY/BANKNIFTY/
# FINNIFTY), formula-based from published regulatory rates. Never "OK".
# ---------------------------------------------------------------------------
def test_index_option_cost_is_never_labeled_ok():
    r = estimate_index_option_cost(entry_premium=100.0, exit_premium=95.0, lot_size=65)
    assert r["status"] == "FORMULA_ESTIMATE"
    assert r["status"] != "OK"


def test_index_option_cost_matches_hand_computed_components_nifty_lot_size():
    r = estimate_index_option_cost(entry_premium=100.0, exit_premium=95.0, lot_size=65,
                                   slippage_points=0.75)
    entry_value, exit_value = 100.0 * 65, 95.0 * 65
    assert r["brokerage"] == 40.0  # Rs20 flat x 2 legs
    assert abs(r["exchange_txn"] - (entry_value + exit_value) * 0.0355299 / 100) < 0.01
    assert abs(r["stt"] - exit_value * 0.15 / 100) < 0.01          # sell-side only
    assert abs(r["stamp_duty"] - entry_value * 0.003 / 100) < 0.01  # buy-side only
    assert r["slippage_cost"] == 0.75 * 65
    assert r["total_cost"] > 0
    assert abs(r["total_cost_points"] - r["total_cost"] / 65) < 1e-4


def test_index_option_cost_stt_is_zero_on_a_worthless_expiry():
    """Sell side premium = 0 (expired worthless) -> STT component is 0, not
    a division error or a fabricated charge on nothing."""
    r = estimate_index_option_cost(entry_premium=50.0, exit_premium=0.0, lot_size=65)
    assert r["stt"] == 0.0


def test_index_option_cost_scales_with_lots():
    one_lot = estimate_index_option_cost(entry_premium=100.0, exit_premium=95.0, lot_size=65, lots=1)
    two_lots = estimate_index_option_cost(entry_premium=100.0, exit_premium=95.0, lot_size=65, lots=2)
    # ad-valorem components double, brokerage (per ORDER, not per lot) does not
    assert two_lots["brokerage"] == one_lot["brokerage"]
    assert abs(two_lots["stt"] - 2 * one_lot["stt"]) < 0.02
    # per-point cost DROPS with more lots -- the flat Rs20x2 brokerage is
    # amortized over a bigger denominator (lot_size*lots), a real economy of
    # scale, not a bug
    assert two_lots["total_cost_points"] < one_lot["total_cost_points"]


def test_index_option_cost_works_for_any_index_lot_size_banknifty_finnifty():
    banknifty = estimate_index_option_cost(entry_premium=200.0, exit_premium=180.0, lot_size=30)
    finnifty = estimate_index_option_cost(entry_premium=80.0, exit_premium=70.0, lot_size=40)
    assert banknifty["status"] == finnifty["status"] == "FORMULA_ESTIMATE"
    assert banknifty["lot_size"] == 30 and finnifty["lot_size"] == 40


def test_index_option_cost_default_slippage_is_the_disclosed_midpoint():
    r = estimate_index_option_cost(entry_premium=100.0, exit_premium=95.0, lot_size=65)
    assert r["slippage_points"] == 0.75  # midpoint of the disclosed 0.5-1.0 range


def test_index_option_rate_card_can_be_overridden_for_sensitivity_checks():
    custom = RegulatoryRateCard(stt_sell_pct=0.10 / 100)  # e.g. testing the PRE-2026-04-01 rate
    r_new = estimate_index_option_cost(entry_premium=100.0, exit_premium=95.0, lot_size=65)
    r_old = estimate_index_option_cost(entry_premium=100.0, exit_premium=95.0, lot_size=65, rates=custom)
    assert r_old["stt"] < r_new["stt"]
