"""
Order idempotency.

Every order leg has a stable key: `client_tag = trade_id + ":" + leg`, leg in
{ENTRY, TARGET, SL, EXIT}. The rules:

  * The intent row (broker_orders) is written PREARMED *before* any network
    call. `db.insert_broker_order` is INSERT-OR-IGNORE on client_tag, so a
    duplicate prearm is a silent no-op.
  * `is_live(tag)` — True if that leg is already ACCEPTED/OPEN/PARTIAL/COMPLETE.
    The OrderManager refuses to submit a leg whose tag is already live.
  * On restart, `open_intents()` returns every non-terminal row so the
    OrderManager can reconcile them instead of re-sending.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import db
from .broker_base import OStatus, OrderReq


def tag(trade_id: str, leg: str) -> str:
    return f"{trade_id}:{leg}"


def _now():
    return datetime.now(timezone.utc).isoformat()


def prearm(req: OrderReq, mode: str) -> dict:
    """Persist a PREARMED intent. Idempotent on client_tag. Returns the row."""
    db.insert_broker_order({
        "client_tag": req.client_tag,
        "trade_id": req.trade_id,
        "leg": req.leg,
        "mode": mode,
        "side": req.side,
        "order_type": req.order_type,
        "variety": req.variety,
        "product": req.product,
        "symbol": req.symbol,
        "symboltoken": req.symboltoken,
        "exchange": req.exchange,
        "tradingsymbol": req.tradingsymbol,
        "requested_qty": req.quantity,
        "limit_price": req.limit_price,
        "trigger_price": req.trigger_price,
        "status": OStatus.PREARMED,
        "filled_qty": 0,
        "prearm_ts": _now(),
        "signal_confidence": req.signal_confidence,
        "signal_ts": req.signal_ts,
        "market_data_ts": req.market_data_ts,
    })
    return db.get_broker_order(req.client_tag) or {}


def get(tag_: str) -> dict | None:
    return db.get_broker_order(tag_)


def is_live(tag_: str) -> bool:
    row = db.get_broker_order(tag_)
    return bool(row and row.get("status") in OStatus.LIVE)


def is_terminal(tag_: str) -> bool:
    row = db.get_broker_order(tag_)
    return bool(row and row.get("status") in OStatus.TERMINAL)


def mark_submitted(tag_: str, ack) -> None:
    # An ack is an ACK, never a fill. We record ACCEPTED and let the Reconciler
    # be the only thing that ever writes COMPLETE / PARTIAL (broker = truth).
    db.update_broker_order(tag_, {
        "status": OStatus.ACCEPTED,
        "broker_order_id": ack.broker_order_id or "",
        "unique_order_id": ack.unique_order_id or "",
        "submit_ts": _now(),
        "error": ack.error or "",
    })


def mark_ambiguous(tag_: str, ack) -> None:
    """Submit failed in a way where the order MAY have reached the broker.
    Leave it UNKNOWN so the reconciler owns it; never flip it back to PREARMED
    (that would make it look re-sendable)."""
    db.update_broker_order(tag_, {
        "status": OStatus.UNKNOWN,
        "submit_ts": _now(),
        "error": (ack.error or "ambiguous submit")[:200],
    })


def mark_status(tag_: str, status: str, *, filled_qty=None, avg_price=None,
                broker_order_id=None, unique_order_id=None, text=None,
                exit_reason=None) -> None:
    fields = {"status": status, "last_reconcile_ts": _now()}
    if filled_qty is not None:
        fields["filled_qty"] = filled_qty
    if avg_price is not None:
        fields["avg_fill_price"] = avg_price
    if broker_order_id:
        fields["broker_order_id"] = broker_order_id
    if unique_order_id:
        fields["unique_order_id"] = unique_order_id
    if text is not None:
        fields["error"] = str(text)[:200]
    if exit_reason is not None:
        fields["exit_reason"] = exit_reason
    if status == OStatus.COMPLETE and avg_price is not None:
        fields["fill_ts"] = _now()
    db.update_broker_order(tag_, fields)


def open_intents(trade_id: str | None = None) -> list[dict]:
    """Non-terminal broker_orders rows — what a restarting OrderManager must
    reconcile rather than re-send."""
    rows = db.list_broker_orders(trade_id=trade_id, limit=1000)
    return [r for r in rows if r.get("status") not in OStatus.TERMINAL]
