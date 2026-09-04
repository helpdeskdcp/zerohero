"""
app/execution/idempotency.py — every order leg keyed by client_tag, prearm is
insert-or-ignore, and the lifecycle transitions never let a submitted order
look re-sendable. Offline: fresh temp DB per test.
"""
import os
import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))


def _fresh(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("CHANAKYA_DB_PATH", os.path.join(d, "t.db"))
    import importlib
    import app.db as db
    importlib.reload(db)
    db.init_db()
    return db


def _req(trade_id="T1", leg="ENTRY", **over):
    from app.execution.broker_base import OrderReq
    from app.execution import idempotency
    base = dict(client_tag=idempotency.tag(trade_id, leg), trade_id=trade_id, leg=leg,
                side="BUY", order_type="MARKET", symbol="NIFTY", symboltoken="123",
                exchange="NFO", quantity=50, tradingsymbol="NIFTY24SEP25000CE")
    base.update(over)
    return OrderReq(**base)


def test_tag_format():
    from app.execution import idempotency
    assert idempotency.tag("T1", "ENTRY") == "T1:ENTRY"


def test_prearm_is_idempotent_on_client_tag(monkeypatch):
    _fresh(monkeypatch)
    from app.execution import idempotency
    req = _req()
    row1 = idempotency.prearm(req, mode="PAPER")
    row2 = idempotency.prearm(req, mode="PAPER")   # duplicate prearm, must be a no-op
    assert row1["client_tag"] == row2["client_tag"] == "T1:ENTRY"
    from app.execution.broker_base import OStatus
    assert row2["status"] == OStatus.PREARMED


def test_is_live_and_is_terminal_track_lifecycle(monkeypatch):
    _fresh(monkeypatch)
    from app.execution import idempotency
    from app.execution.broker_base import OStatus, OrderAck
    req = _req()
    idempotency.prearm(req, mode="PAPER")
    assert idempotency.is_live(req.client_tag) is False
    assert idempotency.is_terminal(req.client_tag) is False

    ack = OrderAck(ok=True, client_tag=req.client_tag, broker_order_id="B1",
                   status=OStatus.ACCEPTED)
    idempotency.mark_submitted(req.client_tag, ack)
    assert idempotency.is_live(req.client_tag) is True
    assert idempotency.is_terminal(req.client_tag) is False

    idempotency.mark_status(req.client_tag, OStatus.COMPLETE, filled_qty=50, avg_price=123.4)
    assert idempotency.is_terminal(req.client_tag) is True
    row = idempotency.get(req.client_tag)
    assert row["status"] == OStatus.COMPLETE
    assert row["filled_qty"] == 50
    assert row["fill_ts"] is not None


def test_mark_ambiguous_never_reverts_to_prearmed(monkeypatch):
    _fresh(monkeypatch)
    from app.execution import idempotency
    from app.execution.broker_base import OStatus, OrderAck
    req = _req()
    idempotency.prearm(req, mode="PAPER")
    ack = OrderAck(ok=False, client_tag=req.client_tag, ambiguous=True, error="timeout")
    idempotency.mark_ambiguous(req.client_tag, ack)
    row = idempotency.get(req.client_tag)
    assert row["status"] == OStatus.UNKNOWN
    # UNKNOWN is neither live nor terminal -- the OrderManager must reconcile, not re-send
    assert idempotency.is_live(req.client_tag) is False
    assert idempotency.is_terminal(req.client_tag) is False


def test_open_intents_excludes_terminal_rows(monkeypatch):
    _fresh(monkeypatch)
    from app.execution import idempotency
    from app.execution.broker_base import OStatus

    idempotency.prearm(_req(trade_id="T1", leg="ENTRY"), mode="PAPER")
    idempotency.prearm(_req(trade_id="T2", leg="ENTRY"), mode="PAPER")
    idempotency.mark_status(idempotency.tag("T2", "ENTRY"), OStatus.REJECTED)

    open_ = idempotency.open_intents()
    tags = {r["client_tag"] for r in open_}
    assert "T1:ENTRY" in tags
    assert "T2:ENTRY" not in tags   # REJECTED is terminal -- must not be re-sent on restart


def test_open_intents_scoped_to_trade_id(monkeypatch):
    _fresh(monkeypatch)
    from app.execution import idempotency
    idempotency.prearm(_req(trade_id="T1", leg="ENTRY"), mode="PAPER")
    idempotency.prearm(_req(trade_id="T2", leg="ENTRY"), mode="PAPER")
    only_t1 = idempotency.open_intents(trade_id="T1")
    assert {r["trade_id"] for r in only_t1} == {"T1"}


def test_get_missing_tag_returns_none(monkeypatch):
    _fresh(monkeypatch)
    from app.execution import idempotency
    assert idempotency.get("no-such-tag") is None
    assert idempotency.is_live("no-such-tag") is False
    assert idempotency.is_terminal("no-such-tag") is False
