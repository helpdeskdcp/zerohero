"""Arena equity (NSE cash, intraday): ledger validation, BUY / SELL settlement (SELL = the long engine on the mirrored price), charges, amendments. Option behaviour is covered by test_arena.py."""
import pytest

from app.arena import settle as S, submit as SU
from tests.test_arena import ep, ev, snaps

H = 0.0005


def eq(**kw):
    d = {"id": "q", "participant": "P", "instrument": "EQUITY", "side": "BUY", "underlying": "RELIANCE", "expiry": "", "strike": 0.0, "option_type": "EQ", "entry_mode": "NEAR", "entry": 1000.0, "entry_hi": 1000.0,
         "sl": 990.0, "targets": [1010.0], "t": ep("10:00"), "lots": 100}
    d.update(kw); return d


def sn(rows): return snaps(rows)


# ---------------- validation ----------------
def test_validate_equity_ok_and_rejects():
    ok = SU.validate({"instrument": "EQUITY", "participant": "A", "underlying": "reliance", "side": "buy", "entry": 1000, "sl": 990, "targets": [1010, 1020], "qty": 50})
    assert ok["instrument"] == "EQUITY" and ok["underlying"] == "RELIANCE" and ok["lots"] == 50 and ok["expiry"] == "" and ok["option_type"] == "EQ"
    sell = SU.validate({"instrument": "EQUITY", "participant": "A", "underlying": "INFY", "side": "SELL", "entry": 1500, "sl": 1510, "targets": [1490, 1480], "qty": 10})
    assert sell["targets"] == [1490.0, 1480.0] or sorted(sell["targets"]) == [1480.0, 1490.0]
    bad = [dict(side="BUY", sl=1010), dict(side="BUY", targets=[990]), dict(side="SELL", sl=990, targets=[]), dict(side="SELL", sl=None, targets=[1010]), dict(qty=0), dict(qty=1.5), dict(qty=100001),
           dict(qty=100000, entry=1000), dict(underlying="BAD SYMBOL"), dict(side="HOLD")]
    for b in bad:
        d = {"instrument": "EQUITY", "participant": "A", "underlying": "RELIANCE", "side": "BUY", "entry": 1000, "sl": 990, "targets": [1010], "qty": 10}; d.update(b)
        with pytest.raises(SU.SubmissionError): SU.validate(d)


def test_option_validation_unchanged():
    o = SU.validate({"participant": "A", "underlying": "NIFTY", "expiry": "2026-10-29", "strike": 25000, "option_type": "CE", "entry": 100, "sl": 90, "targets": [120]})
    assert "instrument" not in o and o["underlying"] == "NIFTY"


# ---------------- BUY ----------------
def test_buy_fills_at_ask_exits_at_bid_with_equity_charges():
    q = sn([("10:01", 1000, 999.9, 1000.1), ("10:03", 1005, 1004.9, 1005.1), ("10:05", 1011, 1010.9, 1011.1)])
    r = S.settle(eq(), q, half_spread=H)
    assert r["status"] == "SETTLED" and r["fill"] == 1000.1 and r["exit"] == 1010.9 and r["exit_reason"] == "TARGET" and r["lots"] == 100
    cost = S.equity_cost_rs("BUY", 1000.1, 1010.9, 100)
    assert r["gross_rs"] == pytest.approx((1010.9 - 1000.1) * 100) and r["cost_rs"] == pytest.approx(cost) and r["net_rs"] == pytest.approx(r["gross_rs"] - cost)


def test_buy_stop_loss():
    q = sn([("10:01", 1000, 999.9, 1000.1), ("10:03", 992, 991.9, 992.1), ("10:05", 989, 988.9, 989.1)])
    r = S.settle(eq(), q, half_spread=H)
    assert r["status"] == "SETTLED" and r["exit_reason"] == "SL" and r["exit"] == 988.9 and r["net_rs"] < 0


# ---------------- SELL (short) ----------------
def test_sell_fills_at_bid_covers_at_ask_on_target():
    q = sn([("10:01", 1000, 999.9, 1000.1), ("10:03", 995, 994.9, 995.1), ("10:05", 989, 988.9, 989.1)])
    r = S.settle(eq(side="SELL", sl=1010.0, targets=[990.0]), q, half_spread=H)
    assert r["status"] == "SETTLED" and r["exit_reason"] == "TARGET"
    assert r["fill"] == pytest.approx(999.9) and r["exit"] == pytest.approx(989.1)                    # sold at the bid, bought back at the ask
    cost = S.equity_cost_rs("SELL", 999.9, 989.1, 100)
    assert r["gross_rs"] == pytest.approx((999.9 - 989.1) * 100) and r["net_rs"] == pytest.approx(r["gross_rs"] - cost) and r["net_rs"] > 0


def test_sell_stop_loss_above_entry_is_a_loss():
    q = sn([("10:01", 1000, 999.9, 1000.1), ("10:03", 1005, 1004.9, 1005.1), ("10:05", 1011, 1010.9, 1011.1)])
    r = S.settle(eq(side="SELL", sl=1010.0, targets=[980.0]), q, half_spread=H)
    assert r["status"] == "SETTLED" and r["exit_reason"] == "SL" and r["exit"] == pytest.approx(1011.1) and r["gross_rs"] == pytest.approx((999.9 - 1011.1) * 100) and r["net_rs"] < r["gross_rs"]


def test_sell_limit_waits_for_the_bid_to_rise():
    q = sn([("10:01", 995, 994.9, 995.1), ("10:03", 1001, 1000.9, 1001.1), ("10:05", 989, 988.9, 989.1)])
    r = S.settle(eq(side="SELL", entry_mode="LIMIT", entry=1000.0, sl=1010.0, targets=[990.0]), q, half_spread=H)
    assert r["status"] == "SETTLED" and r["fill"] == pytest.approx(1000.9) and r["fill_t"] == ep("10:03")
    assert S.settle(eq(side="SELL", entry_mode="LIMIT", entry=1100.0, sl=1110.0, targets=[1000.0]), q, half_spread=H)["status"] == "NOT_TRIGGERED"


def test_sell_events_use_real_prices_and_open_position_maps_back():
    q = sn([("10:01", 1000, 999.9, 1000.1), ("10:03", 997, 996.9, 997.1)])
    r = S.settle(eq(side="SELL", sl=1010.0, targets=[980.0], events=[ev("10:01", "AMEND_SL", sl=1003.0), ev("10:01", "AMEND_TARGETS", targets=[985.0, 975.0])]), q, half_spread=H)
    assert r["status"] == "NO_DATA:no_exit_snapshot" and r["fill"] == pytest.approx(999.9)
    assert r["sl_now"] == pytest.approx(1003.0) and r["targets_now"] == pytest.approx([985.0, 975.0])
    assert r["last"]["bid"] == pytest.approx(996.9) and r["last"]["ask"] == pytest.approx(997.1) and r["last"]["ltp"] == pytest.approx(997.0)


def test_sell_trailing_stop_in_points():
    q = sn([("10:01", 1000, 999.9, 1000.1), ("10:03", 990, 989.9, 990.1), ("10:05", 994, 993.9, 994.1)])
    r = S.settle(eq(side="SELL", sl=1010.0, targets=[900.0], events=[ev("10:01", "TRAIL_SL", trail_points=3.0)]), q, half_spread=H)
    assert r["status"] == "SETTLED" and r["exit_reason"] == "TRAIL" and r["exit"] == pytest.approx(994.1)       # lowest ask 990.1 + 3 = 993.1 stop; bought back at 994.1


def test_amend_entry_works_for_an_equity_buy_limit():
    q = sn([("10:01", 1010, 1009.9, 1010.1), ("10:05", 1004, 1003.9, 1004.1), ("10:09", 1030, 1029.9, 1030.1)])
    base = S.settle(eq(entry_mode="LIMIT", entry=1000.0, sl=990.0, targets=[1020.0]), q, half_spread=H)
    assert base["status"] == "NOT_TRIGGERED"
    r = S.settle(eq(entry_mode="LIMIT", entry=1000.0, sl=990.0, targets=[1020.0], events=[ev("10:02", "AMEND_ENTRY", entry=1005.0)]), q, half_spread=H)
    assert r["status"] == "SETTLED" and r["fill"] == pytest.approx(1004.1)


def test_amend_lots_allows_share_quantities():
    assert SU.validate_event({"signal_id": "q", "action": "AMEND_LOTS", "lots": 250}, equity=True)["lots"] == 250
    with pytest.raises(SU.SubmissionError): SU.validate_event({"signal_id": "q", "action": "AMEND_LOTS", "lots": 250})
    with pytest.raises(SU.SubmissionError): SU.validate_event({"signal_id": "q", "action": "TRAIL_SL", "trail_pct": 1.0}, equity=True, side="SELL")
