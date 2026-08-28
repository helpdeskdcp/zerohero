"""
Auto-live order flow — the lifecycle a real Angel One test order (and every
AUTO LIVE entry) goes through, exercised end to end against a fake broker:

    submit -> broker acknowledgement -> status polling -> cancel -> reconciliation

plus the freeze guards (kill switch / stale / daily risk halt / position
mismatch / UNKNOWN) and restart recovery with no duplicate submission.
No real broker, no network.
"""
from datetime import datetime, timezone, timedelta

from app.execution import OrderManager, killswitch
from app.execution.paper_broker import PaperBroker
from app.execution.staleness import Clocks
from app.execution.broker_base import OStatus, Side, Leg, OrderType, OrderReq
from app.execution import idempotency as idem


def _iso(dt=None):
    return (dt or datetime.now(timezone.utc)).isoformat()


def _contract(tid="LT1", qty=1, entry=100.0):
    return {"trade_id": tid, "signal_id": tid, "created_ts": _iso(),
            "symbol": "IDEA", "market": "NSE", "symboltoken": "14366",
            "tradingsymbol": "IDEA-EQ", "direction": "BUY", "option_type": "",
            "entry_ref": entry, "target_1": entry * 1.04, "stop_loss": entry * 0.98,
            "allowed_quantity": qty, "confidence": 66, "market_data_ts": _iso()}


def _clocks():
    return Clocks(signal_ts=_iso(), market_data_ts=_iso(), last_reconcile_ts=_iso())


def _mgr(fresh_db, scenario=None, broker=None):
    return OrderManager(mode="PAPER", broker=broker,
                        config={"paper_scenario": scenario or {"fill_mode": "WORKING"}},
                        ltp_provider=lambda t: 100.0)


# ---------------------------------------------------------------- full lifecycle: cancel
def test_submit_ack_poll_cancel_reconcile(fresh_db):
    om = _mgr(fresh_db, scenario={"fill_mode": "WORKING"})     # resting limit, no fill
    st = om.prearm(_contract())
    st.entry_type = OrderType.LIMIT
    st.limit_price = 90.0

    res = om.submit(st, clocks=_clocks())
    assert res.status == "SUBMITTED"
    row = fresh_db.get_broker_order("LT1:ENTRY")
    assert row["status"] == OStatus.ACCEPTED          # ack, NOT a fill
    assert row["broker_order_id"] and row["prearm_ts"] and row["submit_ts"]

    # poll: broker still OPEN
    r1 = om.reconcile("LT1")
    assert r1.action == "OK" and r1.order_status == OStatus.OPEN

    # cancel path
    ack = om.cancel("LT1", Leg.ENTRY)
    assert ack.ok
    r2 = om.reconcile("LT1")
    assert r2.order_status == OStatus.CANCELLED and r2.action == "DEAD"
    assert fresh_db.get_broker_order("LT1:ENTRY")["status"] == OStatus.CANCELLED
    # reconciliation: broker flat, local expects flat -> match
    assert r2.position_match in ("FLAT", "UNCHECKED", "MATCH")
    snap = __import__("app.execution.audit", fromlist=["snapshot"]).snapshot("LT1")
    kinds = {e["kind"] for e in snap["events"]}
    assert {"PREARM", "SUBMITTED", "CANCEL"} <= kinds


# ---------------------------------------------------------------- full lifecycle: fill
def test_submit_poll_fill_position_monitor_reconcile(fresh_db):
    om = _mgr(fresh_db, scenario={"fill_mode": "FULL"})
    st = om.prearm(_contract(qty=1))
    res = om.submit(st, clocks=_clocks())
    assert res.monitor is not None and st.is_provisional      # 200 != filled

    rr = om.reconcile("LT1")
    assert rr.action == "FILLED" and rr.order_status == OStatus.COMPLETE
    assert st.avg_fill_price == 100.0 and not st.is_provisional
    assert rr.position_match in ("MATCH", "UNCHECKED")
    # monitor now runs off the ACTUAL fill price
    mon = om.monitors["LT1"]
    assert mon.st.entry_ref == 100.0 and mon.st.state == "CONFIRMED"
    dec = om.on_ltp("LT1", 104.5)
    assert dec.reason == "TARGET" and dec.quantity == 1


# ---------------------------------------------------------------- never trust HTTP 200
def test_ambiguous_submit_not_retried_then_reconciled(fresh_db):
    om = _mgr(fresh_db, scenario={"fill_mode": "AMBIGUOUS"})
    st = om.prearm(_contract())
    res = om.submit(st, clocks=_clocks())
    assert res.status == "AMBIGUOUS"
    assert fresh_db.get_broker_order("LT1:ENTRY")["status"] == OStatus.UNKNOWN
    om.broker.scenario["fill_mode"] = "FULL"
    rr = om.reconcile("LT1")
    assert rr.action == "FILLED"
    entries = [r for r in fresh_db.list_broker_orders(trade_id="LT1") if r["leg"] == "ENTRY"]
    assert len(entries) == 1                                   # never re-sent


# ---------------------------------------------------------------- restart recovery
def test_restart_recovers_without_duplicate(fresh_db):
    shared = PaperBroker(ltp_provider=lambda t: 100.0, scenario={"fill_mode": "WORKING"})
    om = _mgr(fresh_db, broker=shared)
    st = om.prearm(_contract())
    st.entry_type = OrderType.LIMIT
    st.limit_price = 90.0
    om.submit(st, clocks=_clocks())

    om2 = _mgr(fresh_db, broker=shared)                        # "process restart"
    recovered = om2.recover()
    assert recovered.get("LT1") in ("OK", "FILLED", "PARTIAL")
    entries = [r for r in fresh_db.list_broker_orders(trade_id="LT1") if r["leg"] == "ENTRY"]
    assert len(entries) == 1
    # a submit for the recovered trade is now idempotently suppressed
    assert om2.submit(om2.states["LT1"], clocks=_clocks()).status == "DUPLICATE_SUPPRESSED"


# ---------------------------------------------------------------- guards freeze NEW entries
def test_kill_switch_blocks_new_entry(fresh_db):
    om = _mgr(fresh_db)
    killswitch.activate("halt")
    st = om.prearm(_contract())
    assert om.submit(st, clocks=_clocks()).status == "BLOCKED_KILLSWITCH"
    killswitch.deactivate("x")


def test_stale_feed_blocks_new_entry(fresh_db):
    om = _mgr(fresh_db)
    st = om.prearm(_contract())
    stale = Clocks(signal_ts=_iso(),
                   market_data_ts=_iso(datetime.now(timezone.utc) - timedelta(seconds=120)),
                   last_reconcile_ts=_iso())
    assert om.submit(st, clocks=stale).status == "BLOCKED_STALE"


def test_daily_risk_halt_blocks_new_entry(fresh_db):
    om = _mgr(fresh_db)
    om.set_risk_halt(True, "daily realised -12000 <= limit -10000")
    st = om.prearm(_contract())
    res = om.submit(st, clocks=_clocks())
    assert res.status == "BLOCKED_RISK" and "daily risk halt" in res.reasons[0]
    assert om.status()["entries_allowed"] is False
    om.set_risk_halt(False)
    assert om.submit(st, clocks=_clocks()).status == "SUBMITTED"


def test_position_mismatch_freezes(fresh_db):
    om = _mgr(fresh_db, scenario={"fill_mode": "FULL", "position_override": {"14366": 999}})
    st = om.prearm(_contract(qty=1))
    om.submit(st, clocks=_clocks())
    # broker shows 999 on our token but we only booked 1 -> that's MORE, not a
    # shortfall, so it's tolerated; force the shortfall direction instead
    om.broker.scenario["position_override"] = {"14366": 0}
    rr = om.reconcile("LT1")
    assert rr.position_match == "MISMATCH" and rr.action == "FREEZE" and om.frozen
    st2 = om.prearm(_contract(tid="LT2"))
    assert om.submit(st2, clocks=_clocks()).status == "BLOCKED_FROZEN"


def test_unknown_broker_state_freezes(fresh_db):
    class _Unknown(PaperBroker):
        def get_order_status(self, *a, **k):
            from app.execution.broker_base import OrderStatusResult
            return OrderStatusResult(status=OStatus.UNKNOWN, text="broker timeout")

    om = _mgr(fresh_db, broker=_Unknown(ltp_provider=lambda t: 100.0))
    st = om.prearm(_contract())
    om.submit(st, clocks=_clocks())
    rr = om.reconcile("LT1")
    assert rr.action == "FREEZE" and om.frozen
