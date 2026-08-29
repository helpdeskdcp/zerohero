"""
Broker adapter — PaperBroker fill models + AngelOneBroker LIVE guard.

Covers: market entry, limit entry, SL-M, SL-L, target exit, rejected order,
timeout, session/login, order book, positions, and the triple-gated
LiveDisabled guard.
"""
import os
import pytest

from app.execution.paper_broker import PaperBroker, BrokerTimeout
from app.execution.angelone_broker import AngelOneBroker
from app.execution.broker_base import (
    OrderReq, Side, OrderType, Leg, OStatus, LiveDisabled, map_broker_status,
)


def _req(leg=Leg.ENTRY, side=Side.BUY, ot=OrderType.MARKET, qty=75,
         limit=None, trigger=None, tag="T1:ENTRY", tid="T1"):
    return OrderReq(client_tag=tag, trade_id=tid, leg=leg, side=side, order_type=ot,
                    symbol="NATURALGAS", symboltoken="TOK", exchange="MCX",
                    quantity=qty, tradingsymbol="NATURALGAS25AUGFUT",
                    limit_price=limit, trigger_price=trigger)


# ---------------------------------------------------------------- status mapping
def test_status_mapping_partial_inferred():
    assert map_broker_status("complete") == OStatus.COMPLETE
    assert map_broker_status("rejected") == OStatus.REJECTED
    assert map_broker_status("open") == OStatus.OPEN
    assert map_broker_status("open", 75, 25) == OStatus.PARTIAL
    assert map_broker_status("cancelled", 75, 40) == OStatus.PARTIAL   # cancelled after part fill
    assert map_broker_status("something weird") == OStatus.UNKNOWN


# ---------------------------------------------------------------- paper: entries
def test_market_entry_fills_at_ltp():
    b = PaperBroker(ltp_provider=lambda t: 101.5, scenario={"fill_mode": "FULL"})
    ack = b.market_entry(_req())
    assert ack.ok and ack.status == OStatus.COMPLETE
    st = b.get_order_status(ack.broker_order_id)
    assert st.status == OStatus.COMPLETE and st.filled_qty == 75 and st.avg_price == 101.5


def test_limit_entry_is_working_until_ltp_crosses():
    price = {"v": 102.0}
    b = PaperBroker(ltp_provider=lambda t: price["v"])
    ack = b.limit_entry(_req(ot=OrderType.LIMIT, limit=100.0))
    assert ack.ok and ack.status in (OStatus.OPEN, OStatus.ACCEPTED)
    assert b.get_order_status(ack.broker_order_id).status == OStatus.OPEN
    price["v"] = 99.5                                    # limit touched
    st = b.get_order_status(ack.broker_order_id)
    assert st.status == OStatus.COMPLETE and st.avg_price == 100.0


def test_stoploss_market_triggers_on_cross():
    price = {"v": 100.0}
    b = PaperBroker(ltp_provider=lambda t: price["v"])
    ack = b.stoploss_market(_req(leg=Leg.SL, side=Side.SELL, ot=OrderType.SL_M, trigger=98.0))
    assert b.get_order_status(ack.broker_order_id).status == OStatus.OPEN
    price["v"] = 97.9
    assert b.get_order_status(ack.broker_order_id).status == OStatus.COMPLETE


def test_stoploss_limit_uses_limit_price():
    price = {"v": 100.0}
    b = PaperBroker(ltp_provider=lambda t: price["v"])
    ack = b.stoploss_limit(_req(leg=Leg.SL, side=Side.SELL, ot=OrderType.SL,
                                trigger=98.0, limit=97.5))
    price["v"] = 97.0
    st = b.get_order_status(ack.broker_order_id)
    assert st.status == OStatus.COMPLETE and st.avg_price == 97.5


def test_target_exit_immediate():
    b = PaperBroker(ltp_provider=lambda t: 105.0, scenario={"fill_mode": "FULL"})
    ack = b.target_exit(_req(leg=Leg.EXIT, side=Side.SELL))
    assert ack.ok and b.get_order_status(ack.broker_order_id).status == OStatus.COMPLETE


# ---------------------------------------------------------------- paper: failures
def test_rejected_order_returns_not_ok():
    b = PaperBroker(scenario={"fill_mode": "REJECT", "reject_reason": "RMS:blocked"})
    ack = b.market_entry(_req())
    assert not ack.ok and ack.status == OStatus.REJECTED and "RMS" in ack.error


def test_timeout_raises_broker_timeout():
    b = PaperBroker(scenario={"fill_mode": "TIMEOUT"})
    with pytest.raises(BrokerTimeout):
        b.market_entry(_req())


def test_ambiguous_lands_order_but_reports_failure():
    b = PaperBroker(ltp_provider=lambda t: 100.0, scenario={"fill_mode": "AMBIGUOUS"})
    ack = b.market_entry(_req())
    assert not ack.ok and ack.ambiguous
    # the order is still discoverable by tag
    st = b.get_order_status(client_tag="T1:ENTRY")
    assert st.status == OStatus.COMPLETE


def test_partial_fill_reports_remaining():
    b = PaperBroker(ltp_provider=lambda t: 100.0,
                    scenario={"fill_mode": "PARTIAL", "partial_ratio": 1 / 3})
    ack = b.market_entry(_req(qty=75))
    st = b.get_order_status(ack.broker_order_id)
    assert st.status == OStatus.PARTIAL and st.filled_qty == 25 and st.pending_qty == 50


def test_positions_aggregate_entry_minus_exit():
    b = PaperBroker(ltp_provider=lambda t: 100.0, scenario={"fill_mode": "FULL"})
    b.market_entry(_req(qty=75, tag="T1:ENTRY"))
    b.target_exit(_req(leg=Leg.EXIT, side=Side.SELL, qty=25, tag="T1:EXIT"))
    snap = b.get_positions()
    assert snap.ok and snap.by_token("TOK").net_qty == 50


def test_cancel_marks_cancelled():
    b = PaperBroker(ltp_provider=lambda t: 200.0)
    ack = b.limit_entry(_req(ot=OrderType.LIMIT, limit=100.0))
    c = b.cancel_order("T1:ENTRY", ack.broker_order_id)
    assert c.ok and b.get_order_status(ack.broker_order_id).status == OStatus.CANCELLED


# ---------------------------------------------------------------- angel: live gate
def test_angelone_live_guard_blocks_by_default(monkeypatch):
    monkeypatch.delenv("CHANAKYA_ALLOW_LIVE", raising=False)
    monkeypatch.setenv("CHANAKYA_LIVE_CONFIRM_TOKEN", "x")
    b = AngelOneBroker({"execution_mode": "LIVE"})
    assert not b.live_enabled
    for fn in ("market_entry", "limit_entry", "stoploss_market", "target_exit"):
        with pytest.raises(LiveDisabled):
            getattr(b, fn)(_req())


def test_angelone_live_needs_all_three_gates(monkeypatch):
    monkeypatch.setenv("CHANAKYA_ALLOW_LIVE", "1")
    monkeypatch.setenv("CHANAKYA_LIVE_CONFIRM_TOKEN", "x")
    # env set, but mode not LIVE -> still disabled
    assert not AngelOneBroker({"execution_mode": "PAPER"}).live_enabled
    # env + mode, but no confirm token -> still disabled
    monkeypatch.delenv("CHANAKYA_LIVE_CONFIRM_TOKEN")
    assert not AngelOneBroker({"execution_mode": "LIVE"}).live_enabled
    # all three -> enabled
    monkeypatch.setenv("CHANAKYA_LIVE_CONFIRM_TOKEN", "yes")
    assert AngelOneBroker({"execution_mode": "LIVE"}).live_enabled


def test_angelone_reads_work_without_live(monkeypatch):
    monkeypatch.delenv("CHANAKYA_ALLOW_LIVE", raising=False)
    monkeypatch.setattr("app.execution.angelone_broker.angelone.fetch_positions",
                        lambda: {"status": "CONFIG_REQUIRED"})
    monkeypatch.setattr("app.execution.angelone_broker.angelone_orders.order_details",
                        lambda *_a, **_k: {"status": "CONFIG_REQUIRED"})
    monkeypatch.setattr("app.execution.angelone_broker.angelone_orders.find_in_book",
                        lambda **_k: {"status": "CONFIG_REQUIRED"})
    b = AngelOneBroker({"execution_mode": "SHADOW"})
    # no network in the test env — must degrade, not raise
    snap = b.get_positions()
    assert snap.ok is False
    st = b.get_order_status(broker_order_id="NOPE")
    assert st.status == OStatus.UNKNOWN


def test_live_config_never_persists_or_exposes_confirmation_secret(fresh_db, monkeypatch):
    from app.scalper import ScalpRunner

    runner = ScalpRunner()
    with pytest.raises(ValueError, match="server safeguards"):
        runner.set_config({"execution_mode": "LIVE", "execution_enabled": True})

    monkeypatch.setenv("CHANAKYA_API_TOKEN", "api-token")
    monkeypatch.setenv("CHANAKYA_ALLOW_LIVE", "1")
    monkeypatch.setenv("CHANAKYA_LIVE_CONFIRM_TOKEN", "server-secret")
    cfg = runner.set_config({"execution_mode": "LIVE", "execution_enabled": True})
    assert cfg["execution_mode"] == "LIVE" and cfg["execution_enabled"] is True
    assert "live_confirm_token" not in cfg
    assert "server-secret" not in fresh_db.get_setting("scalp_config")


def test_legacy_live_confirmation_is_removed_from_stored_config(fresh_db):
    from app.scalper import ScalpRunner

    fresh_db.set_setting("scalp_config", '{"live_confirm_token":"old-secret"}')
    cfg = ScalpRunner().get_config()
    assert "live_confirm_token" not in cfg
    assert "old-secret" not in fresh_db.get_setting("scalp_config")


def test_execution_config_rejects_invalid_mode_and_auto_exit_outside_live(fresh_db):
    from app.scalper import ScalpRunner

    runner = ScalpRunner()
    with pytest.raises(ValueError, match="execution_mode"):
        runner.set_config({"execution_mode": "REAL"})
    with pytest.raises(ValueError, match="automatic broker exits"):
        runner.set_config({"execution_mode": "PAPER", "execution_auto_exit": True})


def test_execution_mode_cannot_change_while_enabled(fresh_db):
    from app.scalper import ScalpRunner

    runner = ScalpRunner()
    runner.set_config({"execution_mode": "PAPER", "execution_enabled": True})
    with pytest.raises(ValueError, match="disable execution"):
        runner.set_config({"execution_mode": "SHADOW"})
