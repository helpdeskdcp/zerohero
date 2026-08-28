"""
OrderManager lifecycle + safety guards.

Covers: pre-arm, immediate local monitor, market/limit entry, duplicate-order
suppression, kill switch, stale data, target hit, SL hit, trailing SL, time
stop, monitor-only vs auto-exit, LIVE-disabled exit, and "accepted != filled".
"""
import time
from datetime import datetime, timezone, timedelta

import pytest

from app.execution import OrderManager
from app.execution import killswitch
from app.execution.staleness import Clocks
from app.execution.trade_state import TradeState, PrearmRejected
from app.execution.broker_base import OStatus, Side, Leg


def _iso(dt=None):
    return (dt or datetime.now(timezone.utc)).isoformat()


def _contract(tid="T1", direction="BUY", entry=100.0, t1=104.0, sl=98.0,
              qty=75, opt="", **kw):
    c = {
        "trade_id": tid, "signal_id": tid, "created_ts": _iso(),
        "symbol": "NATURALGAS", "underlying": "NATURALGAS", "market": "MCX",
        "symboltoken": "TOK", "direction": direction, "option_type": opt,
        "entry_ref": entry, "target_1": t1, "stop_loss": sl,
        "allowed_quantity": qty, "confidence": 70, "strategy": "SCALP",
        "market_data_ts": _iso(),
    }
    c.update(kw)
    return c


def _mgr(fresh_db, ltp=100.0, scenario=None, config=None, alerts=None, broker=None):
    store = {"NATURALGAS": ltp, "TOK": ltp}
    cfg = {"paper_scenario": scenario or {"fill_mode": "FULL"}}
    cfg.update(config or {})
    om = OrderManager(mode="PAPER", broker=broker, config=cfg,
                      ltp_provider=lambda t: store.get(t),
                      on_alert=((lambda k, p: alerts.append((k, p))) if alerts is not None else None))
    om._store = store
    return om


def _fresh_clocks():
    return Clocks(signal_ts=_iso(), market_data_ts=_iso(), last_reconcile_ts=_iso())


# ---------------------------------------------------------------- pre-arm
def test_prearm_builds_complete_plan(fresh_db):
    om = _mgr(fresh_db)
    st = om.prearm(_contract())
    assert st.side == Side.BUY and st.quantity == 75
    assert st.target.price == 104.0 and st.stop.price == 98.0
    assert st.expected_entry_price == 100.0 and st.is_provisional
    row = fresh_db.get_broker_order("T1:ENTRY")
    assert row["status"] == OStatus.PREARMED and row["signal_confidence"] == 70


def test_prearm_derives_missing_target_and_stop(fresh_db):
    om = _mgr(fresh_db)
    st = om.prearm(_contract(t1=None, sl=None, atr_pct=1.0))
    assert st.stop.price < 100.0 < st.target.price          # BUY: stop below, target above
    assert st.target.rr and st.target.rr > 1.0


def test_prearm_rejects_bad_side_and_qty(fresh_db):
    om = _mgr(fresh_db)
    with pytest.raises(PrearmRejected):
        om.prearm(_contract(direction="NONE"))
    with pytest.raises(PrearmRejected):
        om.prearm(_contract(qty=0))
    with pytest.raises(PrearmRejected):
        om.prearm(_contract(direction="BUY", sl=101.0))     # BUY stop above entry


# ---------------------------------------------------------------- submit + monitor
def test_submit_creates_local_monitor_immediately_provisional(fresh_db):
    om = _mgr(fresh_db, scenario={"fill_mode": "WORKING"})   # broker not confirming yet
    st = om.prearm(_contract())
    res = om.submit(st, clocks=_fresh_clocks())
    assert res.status == "SUBMITTED"
    assert res.monitor is not None and not res.monitor.closed
    # accepted != filled: monitor is provisional, qty is the *requested* qty
    assert st.is_provisional and res.monitor.st.state == "PROVISIONAL"
    assert res.monitor.st.entry_ref == 100.0 and res.monitor.st.quantity == 75


def test_submit_is_idempotent_duplicate_suppressed(fresh_db):
    om = _mgr(fresh_db)
    st = om.prearm(_contract())
    r1 = om.submit(st, clocks=_fresh_clocks())
    r2 = om.submit(st, clocks=_fresh_clocks())
    assert r1.status == "SUBMITTED" and r2.status == "DUPLICATE_SUPPRESSED"
    assert len(fresh_db.list_broker_orders(trade_id="T1")) == 1


def test_kill_switch_blocks_new_entries_only(fresh_db):
    om = _mgr(fresh_db)
    killswitch.activate("test halt")
    st = om.prearm(_contract())
    res = om.submit(st, clocks=_fresh_clocks())
    assert res.status == "BLOCKED_KILLSWITCH"
    killswitch.deactivate("test")
    assert om.submit(st, clocks=_fresh_clocks()).status == "SUBMITTED"


def test_stale_market_data_blocks_entry(fresh_db):
    om = _mgr(fresh_db)
    st = om.prearm(_contract())
    stale = Clocks(signal_ts=_iso(),
                   market_data_ts=_iso(datetime.now(timezone.utc) - timedelta(seconds=120)),
                   last_reconcile_ts=_iso())
    res = om.submit(st, clocks=stale)
    assert res.status == "BLOCKED_STALE" and any("old" in r for r in res.reasons)


def test_stale_reconcile_freezes_entry(fresh_db):
    om = _mgr(fresh_db)
    st = om.prearm(_contract())
    stale = Clocks(signal_ts=_iso(), market_data_ts=_iso(),
                   last_reconcile_ts=_iso(datetime.now(timezone.utc) - timedelta(seconds=600)))
    assert om.submit(st, clocks=stale).status == "BLOCKED_STALE"


# ---------------------------------------------------------------- monitor exits
def test_target_hit_alerts_monitor_only_by_default(fresh_db):
    alerts = []
    om = _mgr(fresh_db, alerts=alerts)
    st = om.prearm(_contract())
    om.submit(st, clocks=_fresh_clocks())
    om.reconcile("T1")                        # confirm the fill
    dec = om.on_ltp("T1", 104.2)
    assert dec.reason == "TARGET" and not dec.provisional
    kinds = [k for k, _ in alerts]
    assert "exit_signal" in kinds
    # monitor-only: no EXIT order was sent
    assert fresh_db.get_broker_order("T1:EXIT") is None


def test_sl_hit_fires_stop(fresh_db):
    om = _mgr(fresh_db)
    st = om.prearm(_contract())
    om.submit(st, clocks=_fresh_clocks())
    om.reconcile("T1")
    dec = om.on_ltp("T1", 97.9)
    assert dec.reason == "STOP" and dec.price == 97.9


def test_trailing_sl_ratchets_and_never_loosens(fresh_db):
    om = _mgr(fresh_db)
    # trailing_stop distance = 2.0 ; stop distance 2.0 (entry100/sl98)
    st = om.prearm(_contract(trailing_stop=2.0))
    om.submit(st, clocks=_fresh_clocks())
    om.reconcile("T1")
    mon = om.monitors["T1"]
    om.on_ltp("T1", 103.0)                    # +3 favourable -> BE then trail arms
    trailed = mon.st.stop
    assert trailed >= 100.0                    # moved to at least breakeven
    assert mon.st.trail_armed
    om.on_ltp("T1", 102.5)                     # pull back but above the trailed stop
    assert mon.st.stop == trailed             # never loosens
    dec = om.on_ltp("T1", trailed - 0.1)       # cross the trailed stop
    assert dec is not None and dec.reason in ("TRAIL", "STOP")


def test_time_stop_fires_even_while_provisional(fresh_db):
    om = _mgr(fresh_db, scenario={"fill_mode": "WORKING"})
    st = om.prearm(_contract(max_hold_sec=1))
    om.submit(st, clocks=_fresh_clocks())
    mon = om.monitors["T1"]
    mon.st.opened_ts = _iso(datetime.now(timezone.utc) - timedelta(seconds=5))
    dec = om.on_ltp("T1", 100.5)
    assert dec.reason == "TIME" and dec.provisional


def test_auto_exit_places_broker_order_when_live_and_confirmed(fresh_db, monkeypatch):
    monkeypatch.setenv("CHANAKYA_ALLOW_LIVE", "1")
    # LIVE mode but use a PaperBroker under the hood by forcing mode back for the
    # broker while keeping auto_exit semantics: emulate via PAPER + auto_exit + mode LIVE
    om = OrderManager(mode="PAPER", config={"auto_exit": True, "paper_scenario": {"fill_mode": "FULL"}},
                      ltp_provider=lambda t: 100.0)
    om.mode = "LIVE"                            # exercise the auto-exit branch
    st = om.prearm(_contract())
    om.submit(st, clocks=_fresh_clocks())
    om.reconcile("T1")                          # -> not provisional
    om.on_ltp("T1", 104.5)
    assert fresh_db.get_broker_order("T1:EXIT") is not None


def test_feed_disconnect_ltp_none_is_safe_noop(fresh_db):
    om = _mgr(fresh_db)
    st = om.prearm(_contract())
    om.submit(st, clocks=_fresh_clocks())
    om.reconcile("T1")
    mon = om.monitors["T1"]
    before = mon.snapshot()
    assert om.on_ltp("T1", None) is None            # WS disconnected -> no tick
    assert om.on_ltp("T1", 0) is None
    assert mon.snapshot()["stop"] == before["stop"] and not mon.closed


def test_feed_staleness_report_flags_and_blocks(fresh_db):
    from app.execution.staleness import assess
    fresh = assess(_fresh_clocks())
    assert fresh.allow_new_entries and not fresh.feed_stale
    stale = assess(Clocks(market_data_ts=_iso(datetime.now(timezone.utc) - timedelta(seconds=45)),
                          last_reconcile_ts=_iso()), max_ltp_age=20)
    assert stale.feed_stale and not stale.allow_new_entries


def test_recover_reconciles_open_intents_without_resubmitting(fresh_db):
    from app.execution.paper_broker import PaperBroker
    shared = PaperBroker(ltp_provider=lambda t: 100.0, scenario={"fill_mode": "FULL"})
    om = _mgr(fresh_db, broker=shared)
    st = om.prearm(_contract())
    om.submit(st, clocks=_fresh_clocks())
    # a brand-new manager (process restart) — same DB, broker still holds the order
    om2 = _mgr(fresh_db, broker=shared)
    recovered = om2.recover()
    assert recovered.get("T1") in ("FILLED", "OK", "PARTIAL")
    # still exactly one ENTRY row — nothing was re-sent
    assert len([r for r in fresh_db.list_broker_orders(trade_id="T1") if r["leg"] == "ENTRY"]) == 1
    # and the recovered state picked up the real fill
    assert om2.states["T1"].avg_fill_price == 100.0
