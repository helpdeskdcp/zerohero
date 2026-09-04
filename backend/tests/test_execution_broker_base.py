"""
app/execution/broker_base.py — the broker-neutral vocabulary + status mapping
every adapter must agree on. Pure logic, no DB / no network.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.execution.broker_base import (   # noqa: E402
    Side, OStatus, map_broker_status, OrderAck, OrderStatusResult,
)


def test_side_opposite():
    assert Side.opposite(Side.BUY) == Side.SELL
    assert Side.opposite(Side.SELL) == Side.BUY


def test_ostatus_terminal_and_live_overlap_only_on_complete():
    # COMPLETE is the one status that is both TERMINAL (order lifecycle is
    # over) and LIVE ("was actually sent to the broker") -- everything else
    # in TERMINAL (REJECTED/CANCELLED) never reached LIVE.
    assert OStatus.TERMINAL & OStatus.LIVE == {OStatus.COMPLETE}
    assert OStatus.PREARMED not in OStatus.LIVE
    assert OStatus.PREARMED not in OStatus.TERMINAL


def test_map_broker_status_known_values():
    assert map_broker_status("complete") == OStatus.COMPLETE
    assert map_broker_status("rejected") == OStatus.REJECTED
    assert map_broker_status("cancelled") == OStatus.CANCELLED
    assert map_broker_status("canceled") == OStatus.CANCELLED
    assert map_broker_status("trigger pending") == OStatus.OPEN


def test_map_broker_status_unknown_text():
    assert map_broker_status("some new broker phrase") == OStatus.UNKNOWN
    assert map_broker_status(None) == OStatus.UNKNOWN
    assert map_broker_status("") == OStatus.UNKNOWN


def test_map_broker_status_infers_partial_from_quantities():
    # "open" with some but not all filled -> PARTIAL
    assert map_broker_status("open", requested_qty=100, filled_qty=40) == OStatus.PARTIAL
    # "cancelled" but had a partial fill first -> PARTIAL (still holds risk!)
    assert map_broker_status("cancelled", requested_qty=100, filled_qty=25) == OStatus.PARTIAL
    # "complete" with fill < requested is contradictory -- still call it PARTIAL, not COMPLETE
    assert map_broker_status("complete", requested_qty=100, filled_qty=50) == OStatus.PARTIAL
    # complete with full fill stays COMPLETE
    assert map_broker_status("complete", requested_qty=100, filled_qty=100) == OStatus.COMPLETE
    # no quantities given -> no partial inference possible
    assert map_broker_status("open") == OStatus.OPEN


def test_map_broker_status_zero_or_missing_quantities_no_crash():
    assert map_broker_status("open", requested_qty=None, filled_qty=None) == OStatus.OPEN
    assert map_broker_status("open", requested_qty=0, filled_qty=0) == OStatus.OPEN
    assert map_broker_status("open", requested_qty="bad", filled_qty="bad") == OStatus.OPEN


def test_orderack_defaults_are_conservative():
    """A freshly-constructed OrderAck with no explicit fields must default to
    'we don't know anything happened' -- never silently look like a fill."""
    ack = OrderAck(ok=False)
    assert ack.status == OStatus.UNKNOWN
    assert ack.ambiguous is False
    assert ack.broker_order_id == ""
    assert ack.ts  # always stamped


def test_orderstatusresult_defaults_are_unknown_not_complete():
    r = OrderStatusResult()
    assert r.status == OStatus.UNKNOWN
    assert r.filled_qty == 0.0
