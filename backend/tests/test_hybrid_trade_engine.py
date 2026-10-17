import copy

import pytest

from app.hybrid_orderflow.policy import PolicyError, load_policy, validate
from app.hybrid_orderflow.trade_engine import PaperPosition, RiskManager, build_plan, charges_inr

P = load_policy()
KW = dict(side="CE", symbol="NIFTY23000CE", bid=99.0, ask=100.0, tick=0.05, lot_size=75, delta=0.5, underlying_entry=23000.0, underlying_stop=22960.0,
          equity=200000.0, policy=P)


def plan(**kw):
    return build_plan(**{**KW, **kw})


# ---- planning ------------------------------------------------------------------------------------------------------------
def test_plan_geometry_and_sizing():
    p = plan(equity=1_000_000.0)
    assert p.status == "PLANNED" and p.entry == 100.05                  # ask + 1 tick slippage
    assert p.stop == 80.05                                              # entry - |delta| * 40 pts = 100.05 - 20
    assert p.targets == [130.05, 150.05, 180.05]                         # entry + 1.5R / 2.5R / 4R, R = 20 premium points
    assert p.lots >= 1 and p.risk_inr <= 1_000_000 * 0.5 / 100 + 1e-6   # never exceeds the risk budget (incl. slippage + charges)
    assert p.lots * 75 * p.entry <= 1_000_000 * 0.30 + 1e-6             # premium exposure cap


def test_small_account_cannot_size_a_nifty_lot_at_half_percent_risk():
    p = plan()                                                          # 2 lakh, 0.5% = 1000 < ~1563 loss per lot
    assert p.status == "NO_TRADE" and p.reasons == ["RISK_BUDGET_TOO_SMALL"] and p.detail["loss_per_lot"] > p.detail["budget"]


def test_put_stop_is_above_entry_on_the_underlying():
    p = plan(side="PE", underlying_stop=23040.0, equity=1_000_000.0)
    assert p.status == "PLANNED" and p.stop < p.entry
    bad = plan(side="PE", underlying_stop=22960.0)                      # stop on the wrong side of the entry for a put
    assert bad.status == "NO_TRADE" and bad.reasons == ["INVALID_STOP"]


@pytest.mark.parametrize("kw,reason", [({"bid": 101.0, "ask": 100.0}, "CROSSED_OR_LOCKED_BOOK"), ({"bid": 0.0, "ask": 0.0}, "DATA_MISSING"),
                                       ({"delta": None}, "DATA_MISSING"), ({"lot_size": 0}, "DATA_UNMAPPED"), ({"underlying_stop": 23010.0}, "INVALID_STOP"),
                                       ({"underlying_stop": 22925.0, "equity": 1_000_000.0}, "STOP_TOO_WIDE"), ({"underlying_stop": 22800.0}, "INVALID_STOP"), ({"equity": 5000.0}, "RISK_BUDGET_TOO_SMALL")])
def test_unsafe_or_unsizeable_setups_are_rejected_not_adjusted(kw, reason):
    p = plan(**kw)
    assert p.status == "NO_TRADE" and reason in p.reasons


def test_lot_allocation_never_creates_fractional_lots():
    from app.hybrid_orderflow.trade_engine import allocate_lots
    f = P["trade"]["partial_fractions"]
    assert allocate_lots(1, f) == [0, 1, 0] or sum(allocate_lots(1, f)) == 1
    for n in range(1, 40):
        a = allocate_lots(n, f)
        assert sum(a) == n and all(x >= 0 for x in a)


def test_small_positions_exit_in_whole_lots_only():
    from app.hybrid_orderflow.trade_engine import allocate_lots
    f = P["trade"]["partial_fractions"]
    assert allocate_lots(1, f) == [1, 0, 0] and allocate_lots(2, f) == [1, 1, 0] and allocate_lots(5, f) == [3, 1, 1]
    p = plan(equity=1_000_000.0)
    one = type(p)(**{**p.__dict__, "lots": 1})
    pos = PaperPosition(one, 0.0, P)
    pos.on_bar(60, 100, one.targets[0] + 0.5, 99, one.targets[0])
    assert pos.closed and pos.close_reason == "T1" and pos.qty_open == 0 and pos.pnl_inr() > 0     # one lot cannot be scaled out: exits at T1


def test_charges_are_positive_and_scale_with_turnover():
    a, b = charges_inr(100, 120, 75, P), charges_inr(100, 120, 750, P)
    assert 0 < a < b and a > 2 * P["trade"]["charges"]["brokerage_per_order_inr"]


# ---- position state machine ------------------------------------------------------------------------------------------------
def open_pos():
    return PaperPosition(plan(equity=1_000_000.0), 0.0, P)


def test_stop_loss_fills_with_slippage_and_loses():
    pos = open_pos()
    pos.on_bar(60, 100, 101, pos.stop - 1, 90)
    assert pos.closed and pos.close_reason == "STOP" and pos.fills[0][1] < pos.stop and pos.pnl_inr() < 0


def test_gap_through_the_stop_fills_at_the_open_not_the_stop():
    pos = open_pos()
    pos.on_bar(60, pos.stop - 10, pos.stop - 5, pos.stop - 12, pos.stop - 8)
    assert pos.fills[0][1] <= pos.stop - 10


def test_bar_touching_stop_and_target_is_a_stop_pessimistic():
    pos = open_pos()
    pos.on_bar(60, 100, pos.plan.targets[0] + 5, pos.stop - 1, 100)
    assert pos.close_reason == "STOP" and pos.hit == 0


def test_t1_partial_then_break_even_stop_then_stopped_flat_ish():
    pos = open_pos()
    pos.on_bar(60, 100, pos.plan.targets[0] + 0.5, 99, pos.plan.targets[0])
    assert pos.hit == 1 and pos.qty_open < pos.qty_total and pos.stop == pos.plan.entry          # BE after T1
    pos.on_bar(120, 105, 106, pos.plan.entry - 0.5, 100)
    assert pos.closed and pos.close_reason == "STOP_AFTER_TARGET" and pos.pnl_inr() > -pos.plan.risk_inr


def test_all_targets_close_the_position_with_profit_and_stop_only_moves_up():
    pos = open_pos()
    stops = [pos.stop]
    for i, tgt in enumerate(pos.plan.targets):
        pos.on_bar(60 * (i + 1), tgt - 1, tgt + 0.5, tgt - 2, tgt)
        stops.append(pos.stop)
    assert pos.closed and pos.close_reason == "T3" and pos.pnl_inr() > 0 and stops == sorted(stops)


def test_time_exit_and_structure_invalidation_and_force_close():
    pos = open_pos()
    pos.on_bar(P["trade"]["max_hold_minutes"] * 60, 100, 101, 99, 100.5)
    assert pos.close_reason == "TIME_EXIT"
    pos2 = open_pos()
    pos2.on_bar(60, 100, 101, 99, 100, structure_invalidated=True)
    assert pos2.close_reason == "STRUCTURE_INVALIDATION"
    pos3 = open_pos()
    pos3.force_close(100.0)
    assert pos3.closed and pos3.close_reason == "FORCED"


def test_position_round_trips_through_dict_for_restart_recovery():
    pos = open_pos()
    pos.on_bar(60, 100, pos.plan.targets[0] + 0.5, 99, 120)
    back = PaperPosition.from_dict(pos.to_dict(), P)
    assert back.to_dict() == pos.to_dict() and back.stop == pos.stop and back.qty_open == pos.qty_open


# ---- risk manager ------------------------------------------------------------------------------------------------------------
def test_risk_manager_daily_loss_trade_cap_cooldown_and_single_position():
    rm = RiskManager(P)
    ok, why = rm.can_open("2026-10-19", 1000.0)
    assert ok and why == []
    rm.on_open()
    assert rm.can_open("2026-10-19", 1001.0)[1] == ["POSITION_OPEN"]
    rm.on_close(-1000.0, 2000.0)
    assert rm.can_open("2026-10-19", 2100.0)[1] == ["COOLDOWN"]
    assert rm.can_open("2026-10-19", 2000.0 + 15 * 60 + 1)[0] is True
    rm.on_close(-5000.0, 3000.0)                                          # -6000 <= -2% of 200000 (= -4000)
    assert "DAILY_LOSS_LIMIT" in rm.can_open("2026-10-19", 9999.0)[1]
    assert rm.can_open("2026-10-20", 9999.0)[0] is True                   # new day resets realized/trades
    rm.kill(True)
    assert rm.can_open("2026-10-20", 9999.0)[1] == ["KILL_SWITCH"]


def test_risk_state_survives_restart_and_max_trades():
    rm = RiskManager(P)
    for _ in range(P["risk"]["max_trades_per_day"]):
        rm.can_open("2026-10-19", 1.0)
        rm.on_open(); rm.on_close(10.0, 2.0)
    assert RiskManager(P, copy.deepcopy(rm.s)).can_open("2026-10-19", 99999.0)[1] == ["MAX_TRADES"]


@pytest.mark.parametrize("mutate,needle", [
    (lambda p: p["trade"].__setitem__("partial_fractions", [0.5, 0.3, 0.3]), "sum to 1"),
    (lambda p: p["trade"].__setitem__("targets_r", [1.0, 2.5, 4.0]), "min_reward_risk_t1"),
    (lambda p: p["trade"].__setitem__("intrabar_conflict", "TARGET_FIRST"), "STOP_FIRST"),
    (lambda p: p["trade"].__setitem__("slippage_ticks", 0), "slippage_ticks"),
    (lambda p: p["trade"].__setitem__("max_stop_premium_pct", 90), "max_stop_premium_pct"),
])
def test_trade_policy_fails_closed(mutate, needle):
    p = copy.deepcopy(P)
    mutate(p)
    with pytest.raises(PolicyError, match=needle):
        validate(p)
