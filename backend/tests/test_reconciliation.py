"""
Reconciliation — broker order status + broker position are the source of truth.

Covers: partial fill (size exits from ACTUAL filled qty), rejected, cancelled,
UNKNOWN -> FREEZE, position mismatch -> FREEZE, ambiguous submit is reconciled
not re-sent, REST failure / session expiry / feed-disconnect degrade safely,
and "accepted != filled" stays provisional until confirmed.
"""
from datetime import datetime, timezone

from app.execution import OrderManager
from app.execution.reconciler import Reconciler
from app.execution.paper_broker import PaperBroker
from app.execution.broker_base import (
    BrokerBase, OrderStatusResult, PositionSnapshot, OStatus, Side,
)
from app.execution.staleness import Clocks


def _iso():
    return datetime.now(timezone.utc).isoformat()


def _contract(tid="T1", qty=75):
    return {"trade_id": tid, "signal_id": tid, "created_ts": _iso(),
            "symbol": "NATURALGAS", "market": "MCX", "symboltoken": "TOK",
            "direction": "BUY", "option_type": "", "entry_ref": 100.0,
            "target_1": 104.0, "stop_loss": 98.0, "allowed_quantity": qty,
            "confidence": 70, "market_data_ts": _iso()}


def _clocks():
    return Clocks(signal_ts=_iso(), market_data_ts=_iso(), last_reconcile_ts=_iso())


def _mgr(fresh_db, **scenario):
    return OrderManager(mode="PAPER", config={"paper_scenario": scenario or {"fill_mode": "FULL"}},
                        ltp_provider=lambda t: 100.0)


# ---------------------------------------------------------------- partial fill
def test_partial_fill_sizes_monitor_and_exits_from_actual_qty(fresh_db):
    om = _mgr(fresh_db, fill_mode="PARTIAL", partial_ratio=1 / 3)
    st = om.prearm(_contract(qty=75))
    res = om.submit(st, clocks=_clocks())
    rr = om.reconcile("T1")
    assert rr.action == "PARTIAL" and rr.filled_qty == 25
    assert st.filled_qty == 25 and st.monitor_qty == 25
    # a target exit now would be for 25, never 75
    dec = om.on_ltp("T1", 104.5)
    assert dec.reason == "TARGET" and dec.quantity == 25


def test_accepted_is_not_filled_until_reconciled(fresh_db):
    om = _mgr(fresh_db, fill_mode="WORKING")
    st = om.prearm(_contract())
    om.submit(st, clocks=_clocks())
    assert st.is_provisional and st.avg_fill_price is None
    row = fresh_db.get_broker_order("T1:ENTRY")
    assert row["status"] == OStatus.ACCEPTED          # ack, not a fill
    assert row["filled_qty"] in (0, 0.0, None)


# ---------------------------------------------------------------- terminal states
def test_rejected_reconcile_kills_monitor(fresh_db):
    om = _mgr(fresh_db, fill_mode="WORKING")
    st = om.prearm(_contract())
    om.submit(st, clocks=_clocks())
    # broker now reports the order was rejected
    om.broker._orders[list(om.broker._orders)[0]]["status_text"] = "rejected"
    rr = om.reconcile("T1")
    assert rr.action == "DEAD" and rr.order_status == OStatus.REJECTED
    assert om.monitors["T1"].closed


def test_cancelled_reconcile_marks_dead(fresh_db):
    om = _mgr(fresh_db, fill_mode="WORKING")
    st = om.prearm(_contract())
    om.submit(st, clocks=_clocks())
    om.cancel("T1")
    rr = om.reconcile("T1")
    assert rr.order_status == OStatus.CANCELLED and rr.action == "DEAD"


# ---------------------------------------------------------------- UNKNOWN / mismatch
class _UnknownBroker(PaperBroker):
    def get_order_status(self, broker_order_id="", unique_order_id="", client_tag=""):
        return OrderStatusResult(status=OStatus.UNKNOWN, text="broker timeout")


def test_unknown_status_freezes_never_acts(fresh_db):
    om = OrderManager(mode="PAPER", broker=_UnknownBroker(ltp_provider=lambda t: 100.0),
                      config={}, ltp_provider=lambda t: 100.0)
    st = om.prearm(_contract())
    om.submit(st, clocks=_clocks())
    rr = om.reconcile("T1")
    assert rr.action == "FREEZE" and om.frozen
    # frozen: a new entry is refused
    st2 = om.prearm(_contract(tid="T2"))
    assert om.submit(st2, clocks=_clocks()).status == "BLOCKED_FROZEN"


def test_position_mismatch_freezes(fresh_db):
    om = OrderManager(
        mode="PAPER",
        broker=PaperBroker(ltp_provider=lambda t: 100.0,
                           scenario={"fill_mode": "FULL", "position_override": {"TOK": 10}}),
        config={}, ltp_provider=lambda t: 100.0)
    st = om.prearm(_contract(qty=75))
    om.submit(st, clocks=_clocks())
    rr = om.reconcile("T1")
    assert rr.position_match == "MISMATCH" and rr.action == "FREEZE"


def test_position_flat_when_expected_flat_is_ok(fresh_db):
    om = OrderManager(
        mode="PAPER",
        broker=PaperBroker(ltp_provider=lambda t: 100.0,
                           scenario={"fill_mode": "WORKING", "position_override": {}}),
        config={}, ltp_provider=lambda t: 100.0)
    st = om.prearm(_contract())
    om.submit(st, clocks=_clocks())
    rr = om.reconcile("T1")                # still OPEN, nothing filled -> no mismatch
    assert rr.action == "OK" and not om.frozen


# ---------------------------------------------------------------- ambiguous -> reconcile
def test_ambiguous_submit_is_reconciled_not_resent(fresh_db):
    om = _mgr(fresh_db, fill_mode="AMBIGUOUS")
    st = om.prearm(_contract())
    res = om.submit(st, clocks=_clocks())
    assert res.status == "AMBIGUOUS"
    row = fresh_db.get_broker_order("T1:ENTRY")
    assert row["status"] == OStatus.UNKNOWN            # not re-armed, not re-sent
    # the order actually landed at the broker; reconcile discovers it
    om.broker.scenario["fill_mode"] = "FULL"
    rr = om.reconcile("T1")
    assert rr.action == "FILLED" and rr.order_status == OStatus.COMPLETE
    assert len([r for r in fresh_db.list_broker_orders(trade_id="T1") if r["leg"] == "ENTRY"]) == 1


# ---------------------------------------------------------------- degrade safely
class _DeadBroker(BrokerBase):
    """Every call fails the way a disconnected session / dead REST would."""
    name = "dead"

    def get_order_status(self, *a, **k):
        raise ConnectionError("REST unreachable")

    def get_positions(self):
        return PositionSnapshot(ok=False, error="session expired")

    def reconcile_position(self, token):
        return None


def test_rest_failure_freezes_without_crashing(fresh_db):
    om = OrderManager(mode="PAPER", broker=_DeadBroker(), config={}, ltp_provider=lambda t: 100.0)
    st = om.prearm(_contract())
    # submit path not exercised (dead broker has no entry method) — go straight to reconcile
    om.states["T1"] = st
    om.monitors["T1"] = __import__("app.execution.trade_monitor", fromlist=["TradeMonitor"]).TradeMonitor(st, provisional_ltp=100.0)
    rr = om.reconcile("T1")
    assert rr.action == "FREEZE" and om.frozen


def test_reconciler_handles_positions_unavailable(fresh_db):
    r = Reconciler(_DeadBroker())
    om = _mgr(fresh_db, fill_mode="FULL")
    st = om.prearm(_contract())
    # positions unavailable -> UNCHECKED, not a false mismatch
    match = r._check_position(st, 75, [])
    assert match == "UNCHECKED"
