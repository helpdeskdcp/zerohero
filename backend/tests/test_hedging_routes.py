"""app.api.hedging_routes -- read-only status/positions + emergency exit-all.
Calls the route handlers directly (same pattern as test_profile_routes.py),
never a real broker or real network chain fetch."""
import pytest
from fastapi import HTTPException

from app.api import hedging_routes as hr
from app.hedging import capital as C
from app.hedging import position as P


def test_status_reports_default_capital_when_untouched(fresh_db):
    out = hr.api_hedging_status()
    assert out["capital"]["available_capital"] == C.DEFAULT_STARTING_CAPITAL
    assert out["open_position_count"] == 0
    assert out["live_trading"] is False and out["paper_mode"] is True


def test_positions_lists_only_matching_status(fresh_db):
    C.reset(50000.0)
    pos = P.open_position(symbol="NIFTY", primary_strike=24500, primary_option_type="CE",
                          primary_entry_premium=60.0, hedge_strike=24700, hedge_entry_premium=20.0,
                          lots=1, lot_size=65, max_loss_per_lot=10400.0,
                          margin_locked=10400.0, cost_paid=1300.0)
    P.close_position(pos, exit_reason="PROFIT_TARGET", pnl=500.0)

    out_open = hr.api_hedging_positions(status="OPEN")
    assert out_open["count"] == 0
    out_closed = hr.api_hedging_positions(status="CLOSED")
    assert out_closed["count"] == 1
    assert out_closed["positions"][0]["exit_reason"] == "PROFIT_TARGET"


def test_emergency_exit_all_reports_unresolved_when_chain_lookup_fails(fresh_db, monkeypatch):
    C.reset(50000.0)
    P.open_position(symbol="NIFTY", primary_strike=24500, primary_option_type="CE",
                    primary_entry_premium=60.0, hedge_strike=24700, hedge_entry_premium=20.0,
                    lots=1, lot_size=65, max_loss_per_lot=10400.0,
                    margin_locked=10400.0, cost_paid=1300.0)

    def _boom(symbol, expiry):
        raise RuntimeError("no live chain available in this test")
    monkeypatch.setattr(hr._chain_resolve, "get_chain", _boom)

    out = hr.api_hedging_emergency_exit_all()
    assert out["live_trading"] is False
    assert out["results"][0]["status"] == "UNRESOLVED"   # never closed at a fabricated price

    # the position must still be OPEN -- nothing was force-closed blind
    remaining = P.load_all()
    assert list(remaining.values())[0].status == "OPEN"


def test_capital_reset_updates_starting_and_available(fresh_db):
    out = hr.api_hedging_capital_reset(hr.CapitalResetRequest(starting_capital=100000.0))
    assert out["starting_capital"] == 100000.0
    assert out["available_capital"] == 100000.0
    status = hr.api_hedging_status()
    assert status["capital"]["available_capital"] == 100000.0


def test_capital_reset_rejects_non_positive(fresh_db):
    with pytest.raises(HTTPException) as exc:
        hr.api_hedging_capital_reset(hr.CapitalResetRequest(starting_capital=0))
    assert exc.value.status_code == 400


def test_capital_reset_refuses_while_position_open(fresh_db):
    C.reset(50000.0)
    P.open_position(symbol="NIFTY", primary_strike=24500, primary_option_type="CE",
                    primary_entry_premium=60.0, hedge_strike=24700, hedge_entry_premium=20.0,
                    lots=1, lot_size=65, max_loss_per_lot=10400.0,
                    margin_locked=10400.0, cost_paid=1300.0)
    with pytest.raises(HTTPException) as exc:
        hr.api_hedging_capital_reset(hr.CapitalResetRequest(starting_capital=100000.0))
    assert exc.value.status_code == 400
    assert "open position" in exc.value.detail
