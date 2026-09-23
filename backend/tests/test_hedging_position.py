"""app.hedging.position -- paper position tracking + exit rules. Isolated
via the fresh_db fixture (never the live chanakya.db). `now` is always
passed explicitly so these tests never depend on the real wall clock."""
from datetime import datetime, timezone, timedelta

import pytest

from app.hedging import capital as C
from app.hedging import position as P

_IST = timezone(timedelta(hours=5, minutes=30))


def _open(fresh_db, *, lots=1, profit_target=None, max_loss_per_lot=10400.0):
    C.reset(50000.0)
    return P.open_position(
        symbol="NIFTY", primary_strike=24500, primary_option_type="CE",
        primary_entry_premium=60.0, hedge_strike=24700, hedge_entry_premium=20.0,
        lots=lots, lot_size=65, max_loss_per_lot=max_loss_per_lot,
        margin_locked=max_loss_per_lot * lots, cost_paid=20.0 * 65 * lots,
        profit_target=profit_target)


def test_open_position_allocates_capital(fresh_db):
    pos = _open(fresh_db, lots=1)
    cap = C.load()
    # 50000 - margin(10400) - cost(20*65=1300) = 38300
    assert cap.available_capital == pytest.approx(50000 - 10400 - 1300)
    assert cap.allocated_margin == pytest.approx(10400)
    assert pos.status == "OPEN"


def test_combined_pnl_profitable_when_primary_falls_and_hedge_rises(fresh_db):
    pos = _open(fresh_db, lots=1)
    # primary premium fell 60->40 (short leg gains 20/unit), hedge rose 20->30 (long leg gains 10/unit)
    pnl = P.combined_pnl(pos, current_primary_premium=40.0, current_hedge_premium=30.0)
    assert pnl == pytest.approx((20.0 + 10.0) * 65)


def test_combined_pnl_losing_when_primary_rises(fresh_db):
    pos = _open(fresh_db, lots=1)
    pnl = P.combined_pnl(pos, current_primary_premium=80.0, current_hedge_premium=25.0)
    # primary: (60-80)*65 = -1300; hedge: (25-20)*65 = 325 -> net -975
    assert pnl == pytest.approx(-1300 + 325)


def test_check_exit_none_when_nothing_triggers(fresh_db):
    pos = _open(fresh_db, lots=1, profit_target=5000)
    now = datetime(2026, 9, 23, 11, 0, tzinfo=_IST)   # well before 15:15, mild P&L
    out = P.check_exit(pos, current_primary_premium=58.0, current_hedge_premium=21.0, now=now)
    assert out["should_exit"] is False


def test_check_exit_time_cutoff(fresh_db):
    pos = _open(fresh_db, lots=1)
    now = datetime(2026, 9, 23, 15, 20, tzinfo=_IST)   # past 15:15
    out = P.check_exit(pos, current_primary_premium=58.0, current_hedge_premium=21.0, now=now)
    assert out["should_exit"] is True and out["reason"] == "TIME_CUTOFF"


def test_check_exit_profit_target(fresh_db):
    pos = _open(fresh_db, lots=1, profit_target=1000.0)
    now = datetime(2026, 9, 23, 11, 0, tzinfo=_IST)
    # primary premium 60->10 -> (60-10)*65=3250 gain, hedge roughly flat -> target cleared
    out = P.check_exit(pos, current_primary_premium=10.0, current_hedge_premium=20.0, now=now)
    assert out["should_exit"] is True and out["reason"] == "PROFIT_TARGET"


def test_check_exit_stop_loss(fresh_db):
    pos = _open(fresh_db, lots=1, max_loss_per_lot=10400.0)
    now = datetime(2026, 9, 23, 11, 0, tzinfo=_IST)
    # force a loss >= max_loss(10400): primary premium spikes hugely, hedge premium collapses
    out = P.check_exit(pos, current_primary_premium=250.0, current_hedge_premium=5.0, now=now)
    assert out["should_exit"] is True and out["reason"] == "STOP_LOSS"


def test_check_exit_priority_emergency_beats_everything(fresh_db):
    pos = _open(fresh_db, lots=1, profit_target=1000.0)
    now = datetime(2026, 9, 23, 11, 0, tzinfo=_IST)
    out = P.check_exit(pos, current_primary_premium=10.0, current_hedge_premium=20.0,
                       now=now, emergency=True)
    assert out["reason"] == "EMERGENCY"


def test_close_position_releases_margin_and_books_pnl(fresh_db):
    pos = _open(fresh_db, lots=1)
    P.close_position(pos, exit_reason="PROFIT_TARGET", pnl=1500.0)
    cap = C.load()
    assert cap.allocated_margin == 0.0
    assert cap.realized_pnl == 1500.0
    assert pos.status == "CLOSED" and pos.gross_pnl == 1500.0


def test_close_position_is_idempotent(fresh_db):
    pos = _open(fresh_db, lots=1)
    P.close_position(pos, exit_reason="PROFIT_TARGET", pnl=1500.0)
    cap_after_first = C.load()
    P.close_position(pos, exit_reason="PROFIT_TARGET", pnl=1500.0)   # closing again must be a no-op
    cap_after_second = C.load()
    assert cap_after_first.realized_pnl == cap_after_second.realized_pnl == 1500.0


def test_emergency_exit_all_closes_positions_with_real_quotes(fresh_db):
    pos1 = _open(fresh_db, lots=1)
    pos2 = P.open_position(
        symbol="NIFTY", primary_strike=24500, primary_option_type="CE",
        primary_entry_premium=60.0, hedge_strike=24700, hedge_entry_premium=20.0,
        lots=1, lot_size=65, max_loss_per_lot=10400.0, margin_locked=10400.0, cost_paid=1300.0)
    results = P.emergency_exit_all(current_prices={pos1.position_id: (55.0, 22.0)})
    by_id = {r["position_id"]: r for r in results}
    assert by_id[pos1.position_id]["status"] == "CLOSED"
    assert by_id[pos2.position_id]["status"] == "UNRESOLVED"   # no quote supplied -- never fabricated

    remaining = P.load_all()
    assert remaining[pos1.position_id].status == "CLOSED"
    assert remaining[pos2.position_id].status == "OPEN"   # untouched, honestly left open
