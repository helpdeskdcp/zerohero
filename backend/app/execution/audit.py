"""
Audit trail for the order adapter.

`event()` is one INSERT into order_events — append-only, never updated. It is
cheap enough to call on every state transition but is still invoked from
`asyncio.to_thread` on the runner's hot path so it is never on the critical
Signal->Submit chain.

`snapshot()` assembles the full record the spec asks for (§13) by joining the
broker_orders rows for a trade_id.
"""
from __future__ import annotations

import json
import logging

from .. import db

_log = logging.getLogger(__name__)


def event(trade_id: str, client_tag: str, kind: str, detail: dict | None = None) -> None:
    try:
        db.insert_order_event(trade_id, client_tag, kind,
                              json.dumps(detail or {}, default=str))
    except Exception as e:
        _log.warning("audit.event(%s, %s): failed to write order_events row: %r", trade_id, kind, e)


def snapshot(trade_id: str) -> dict:
    """Everything known about one trade's order lifecycle."""
    orders = db.list_broker_orders(trade_id=trade_id)
    events = db.list_order_events(trade_id=trade_id, limit=300)
    legs = {o["leg"]: o for o in orders}
    entry = legs.get("ENTRY") or {}
    return {
        "trade_id": trade_id,
        "signal_confidence": entry.get("signal_confidence"),
        "signal_ts": entry.get("signal_ts"),
        "market_data_ts": entry.get("market_data_ts"),
        "prearm_ts": entry.get("prearm_ts"),
        "submit_ts": entry.get("submit_ts"),
        "fill_ts": entry.get("fill_ts"),
        "broker_order_id": entry.get("broker_order_id"),
        "unique_order_id": entry.get("unique_order_id"),
        "requested_qty": entry.get("requested_qty"),
        "filled_qty": entry.get("filled_qty"),
        "avg_fill_price": entry.get("avg_fill_price"),
        "entry_status": entry.get("status"),
        "target": (legs.get("TARGET") or {}).get("limit_price")
        or (legs.get("TARGET") or {}).get("trigger_price"),
        "stop_loss": (legs.get("SL") or {}).get("trigger_price"),
        "exit_reason": (legs.get("EXIT") or {}).get("exit_reason") or entry.get("exit_reason"),
        "exit_price": (legs.get("EXIT") or {}).get("avg_fill_price"),
        "errors": [e for e in events if e["kind"] in ("ERROR", "AMBIGUOUS", "FREEZE", "REJECTED")],
        "orders": orders,
        "events": events,
    }
