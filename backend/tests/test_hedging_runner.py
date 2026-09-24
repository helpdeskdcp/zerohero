"""app.hedging.runner -- the autonomous scan connecting primary_selector +
selector + capital + position. get_chain/instrument_profiles/telegram are
stubbed; never calls the real broker or sends a real Telegram message."""
from datetime import datetime

from app.hedging import capital as _capital
from app.hedging import position as _position
from app.hedging import runner
from app.optionchain.chain import OptionChain, OptionLeg, StrikeRow

_MID_SESSION_IST = datetime(2026, 9, 24, 11, 0, 0, tzinfo=runner._position._IST)  # before the 15:15 cutoff


class _FakeParam:
    def __init__(self, value):
        self.value = value


class _FakeProfile:
    def __init__(self, lot_size):
        self.lot_size = _FakeParam(lot_size)


def _good_chain(spot=23000):
    # realistic economics: hedge cheap enough relative to primary credit
    # that it clears max_hedge_cost_pct_of_credit, and a max_loss_per_lot
    # small enough to clear the 2%-of-capital risk cap
    rows = [
        StrikeRow(strike=23400, ce=OptionLeg(ltp=30, oi=5000, bid=29, ask=30.5, volume=1000),
                  pe=OptionLeg(ltp=5, oi=100)),
        StrikeRow(strike=23500, ce=OptionLeg(ltp=8, oi=2000, bid=7.7, ask=8.3, volume=1000),
                  pe=None),
        StrikeRow(strike=22600, ce=None, pe=OptionLeg(ltp=8, oi=100)),
    ]
    return OptionChain(underlying="NIFTY", expiry="29SEP2026", ts="t",
                       source="test", spot=spot, rows=rows)


def _patch_common(monkeypatch, chain=None, lot_size=65, sent=None):
    monkeypatch.setattr(runner, "get_chain", lambda sym, exp, allow_network: chain or _good_chain())
    monkeypatch.setattr(runner.instrument_profiles, "get_instrument_profile",
                        lambda sym: _FakeProfile(lot_size))
    sent = sent if sent is not None else []
    monkeypatch.setattr(runner.telegram_dispatcher, "dispatch",
                        lambda **kw: sent.append(kw) or {"status": "SENT"})
    return sent


def test_disarmed_scan_evaluates_no_new_entries(fresh_db, monkeypatch):
    sent = _patch_common(monkeypatch)
    out = runner.scan()
    assert out["armed"] is False
    assert sent == []


def test_armed_scan_opens_a_position_and_notifies(fresh_db, monkeypatch):
    sent = _patch_common(monkeypatch)
    _capital.reset(500000.0)
    runner.set_config({"symbols": ["NIFTY"], "min_oi": 500, "min_distance_pct": 1.0})
    runner.arm()

    out = runner.scan()
    assert out["armed"] is True
    entry = out["entries"][0]
    assert entry["status"] == "OPENED"
    assert sent and sent[0]["direction"] == "OPEN"

    positions = _position.load_all()
    assert len(positions) == 1
    pos = list(positions.values())[0]
    assert pos.symbol == "NIFTY" and pos.status == "OPEN"
    assert pos.primary_option_type == "CE" and pos.primary_strike == 23400
    assert pos.hedge_strike == 23500


def test_armed_scan_respects_max_positions_per_symbol(fresh_db, monkeypatch):
    _patch_common(monkeypatch)
    _capital.reset(500000.0)
    runner.set_config({"symbols": ["NIFTY"], "max_positions_per_symbol": 1, "min_distance_pct": 1.0})
    runner.arm()
    runner.scan(now=_MID_SESSION_IST)
    out2 = runner.scan(now=_MID_SESSION_IST)
    assert out2["entries"][0]["status"] == "SKIPPED"


def test_no_verified_lot_size_is_no_trade_not_fabricated(fresh_db, monkeypatch):
    _patch_common(monkeypatch, lot_size=None)
    runner.set_config({"symbols": ["NIFTY"]})
    runner.arm()
    out = runner.scan()
    assert out["entries"][0]["status"] == "NO_TRADE"
    assert "lot size" in out["entries"][0]["reason"]


def test_check_exits_closes_and_notifies_on_stop_loss(fresh_db, monkeypatch):
    sent = _patch_common(monkeypatch)
    cap = _capital.reset(50000.0)
    pos = _position.open_position(symbol="NIFTY", primary_strike=23400, primary_option_type="CE",
                                  primary_entry_premium=30.0, hedge_strike=23600, hedge_entry_premium=10.0,
                                  lots=1, lot_size=65, max_loss_per_lot=1300.0,
                                  margin_locked=1300.0, cost_paid=650.0)

    # chain where CE primary premium blew out hugely -> combined pnl breaches stop
    blown = OptionChain(underlying="NIFTY", expiry="29SEP2026", ts="t", source="test", spot=23000, rows=[
        StrikeRow(strike=23400, ce=OptionLeg(ltp=60.0, oi=5000), pe=None),
        StrikeRow(strike=23600, ce=OptionLeg(ltp=12.0, oi=2000), pe=None),
    ])
    monkeypatch.setattr(runner, "get_chain", lambda sym, exp, allow_network: blown)

    results = runner.check_exits()
    assert results[0]["status"] == "CLOSED"
    assert results[0]["reason"] == "STOP_LOSS"
    assert sent and sent[0]["direction"] == "CLOSE"

    reloaded = _position.load_all()[pos.position_id]
    assert reloaded.status == "CLOSED"


def test_check_exits_leaves_position_open_when_no_quote(fresh_db, monkeypatch):
    sent = _patch_common(monkeypatch)
    _capital.reset(50000.0)
    _position.open_position(symbol="NIFTY", primary_strike=23400, primary_option_type="CE",
                            primary_entry_premium=30.0, hedge_strike=23600, hedge_entry_premium=10.0,
                            lots=1, lot_size=65, max_loss_per_lot=1300.0,
                            margin_locked=1300.0, cost_paid=650.0)
    monkeypatch.setattr(runner, "get_chain", lambda sym, exp, allow_network: None)

    results = runner.check_exits()
    assert results[0]["status"] == "NO_QUOTE"
    assert sent == []


def test_bad_symbol_evaluation_is_error_not_crash(fresh_db, monkeypatch):
    def _boom(sym, exp, allow_network):
        raise RuntimeError("boom")
    monkeypatch.setattr(runner, "get_chain", _boom)
    monkeypatch.setattr(runner.instrument_profiles, "get_instrument_profile",
                        lambda sym: _FakeProfile(65))
    runner.set_config({"symbols": ["NIFTY"]})
    runner.arm()
    out = runner.scan()
    assert out["entries"][0]["status"] == "ERROR"
