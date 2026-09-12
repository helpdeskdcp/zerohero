"""
app/institutional_edge/costs.py -- itemized realistic cost model.
Pure functions, no DB.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.institutional_edge.costs import estimate_cost, known_profiles  # noqa: E402


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
        assert "validated_note" in p and p["validated_note"]
