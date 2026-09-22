"""app.hedging.capital -- paper capital ledger. Isolated via the fresh_db
fixture (never the live chanakya.db)."""
import pytest

from app.hedging import capital as C


def test_load_starts_fresh_at_default_capital(fresh_db):
    state = C.load()
    assert state.starting_capital == C.DEFAULT_STARTING_CAPITAL
    assert state.available_capital == C.DEFAULT_STARTING_CAPITAL
    assert state.allocated_margin == 0.0 and state.realized_pnl == 0.0


def test_save_and_reload_round_trips(fresh_db):
    state = C.load()
    state.available_capital = 42000.0
    state.realized_pnl = 1500.0
    C.save(state)
    reloaded = C.load()
    assert reloaded.available_capital == 42000.0
    assert reloaded.realized_pnl == 1500.0


def test_reset_is_explicit_and_overwrites(fresh_db):
    state = C.load()
    state.available_capital = 1000.0
    C.save(state)
    fresh = C.reset(starting_capital=75000.0)
    assert fresh.available_capital == 75000.0
    assert C.load().available_capital == 75000.0


def test_size_position_risk_cap_binds():
    # available 50000, risk 2% = 1000 budget, max_loss_per_lot 10400 -> 0 lots
    out = C.size_position(available_capital=50000, max_loss_per_lot=10400, max_risk_pct=0.02)
    assert out["lots"] == 0
    assert out["risk_budget"] == 1000.0


def test_size_position_allows_lots_when_risk_budget_covers_it():
    # available 500000, risk 2% = 10000 budget, max_loss_per_lot 2000 -> 5 lots
    out = C.size_position(available_capital=500000, max_loss_per_lot=2000, max_risk_pct=0.02)
    assert out["lots"] == 5


def test_size_position_capital_cap_can_be_tighter_than_risk_cap():
    # risk cap allows 5 lots, but hedge cost per lot is huge -> capital cap binds tighter
    out = C.size_position(available_capital=500000, max_loss_per_lot=2000, max_risk_pct=0.02,
                          max_hedge_cost_per_lot=150000)
    assert out["lots"] == 3        # floor(500000/150000) = 3, tighter than risk cap's 5
    assert out["lots_by_capital_cap"] == 3


def test_size_position_never_negative_or_fractional():
    out = C.size_position(available_capital=0, max_loss_per_lot=100)
    assert out["lots"] == 0
    out2 = C.size_position(available_capital=-500, max_loss_per_lot=100)
    assert out2["lots"] == 0


def test_allocate_and_release_round_trip():
    state = C.PaperCapitalState(starting_capital=50000, available_capital=50000)
    C.allocate(state, margin=10400, cost=390)   # margin + hedge premium cost
    assert state.available_capital == 50000 - 10400 - 390
    assert state.allocated_margin == 10400
    C.release(state, margin=10400, realized_pnl=500)
    assert state.allocated_margin == 0.0
    assert state.available_capital == pytest.approx(50000 - 390 + 500)
    assert state.realized_pnl == 500


def test_allocate_refuses_to_go_negative():
    state = C.PaperCapitalState(starting_capital=1000, available_capital=1000)
    with pytest.raises(ValueError):
        C.allocate(state, margin=800, cost=500)   # 1300 > 1000 available
