"""Regression test for the 2026-09-24 incident: pipeline_core.open_from_contract()
never set symboltoken, so ScalpRunner's price-monitor loop (token-first lookup,
fragile watchlist-name fallback) couldn't reliably price 2 real INDEX-scalp
paper trades -- they sat OPEN for 3 days past their own target/stop.
"""
from app import pipeline_core as pc


def _contract(**over):
    base = {
        "signal_id": "SCL-test-1", "market": "MCX", "underlying": "NATURALGAS",
        "instrument": "INDEX", "expiry": "", "strike": 0.0, "option_type": "",
        "direction": "BUY", "timeframe": "1m", "entry_ref": 276.5,
        "target_1": 276.63, "target_2": 276.8, "stop_loss": 276.39,
        "trailing_stop": None, "allowed_quantity": 1, "probability": 0.6,
        "confidence": 0.6, "market_regime": "TRENDING",
    }
    base.update(over)
    return base


def test_index_contract_resolves_a_real_symboltoken(fresh_db):
    trade = pc.open_from_contract(_contract(), reason="test")
    assert trade["symboltoken"] == "568245"  # real NATURALGAS token via instruments.resolve


def test_future_contract_resolves_a_symboltoken_too(fresh_db):
    trade = pc.open_from_contract(_contract(underlying="NATGASMINI", instrument="FUTURE"),
                                   reason="test")
    assert trade["symboltoken"] == "568246"


def test_unresolvable_underlying_leaves_symboltoken_empty_not_broken(fresh_db):
    trade = pc.open_from_contract(_contract(underlying="NOT_A_REAL_SYMBOL_XYZ"), reason="test")
    assert trade["symboltoken"] in (None, "")
    assert trade["status"] == "OPEN"  # resolution failure never blocks opening the trade


def test_option_contract_is_left_untouched_no_resolution_attempted(fresh_db):
    trade = pc.open_from_contract(_contract(instrument="OPTION", strike=24500, option_type="CE"),
                                   reason="test")
    assert trade["symboltoken"] in (None, "")


def test_existing_token_on_contract_is_passed_through_not_overwritten(fresh_db):
    trade = pc.open_from_contract(_contract(symboltoken="999999"), reason="test")
    assert trade["symboltoken"] == "999999"
