"""
Order-adapter health/audit reads + the global kill switch. Read-only except
for /api/execution/kill — there is NO endpoint here that submits a live order.
Split out of app/main.py.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from .. import db
from .. import runtime
from .schemas import KillSwitchRequest

router = APIRouter()


@router.get("/api/execution/status")
def api_execution_status():
    """Order-adapter health: mode, kill switch, frozen state, open intents.
    Read-only — there is NO endpoint to submit a live order from the API."""
    from ..execution import killswitch
    st = runtime.scalp_runner.status()
    ex = st.get("execution") or {}
    counts = {}
    with db.db() as conn:
        for r in conn.execute("SELECT status, COUNT(*) c FROM broker_orders GROUP BY status"):
            counts[r["status"]] = r["c"]
    return {"execution": ex, "kill_switch": killswitch.state(),
            "order_counts": counts, "runner_is_leader": st.get("is_leader")}


@router.get("/api/execution/orders")
def api_execution_orders(trade_id: Optional[str] = None, status: Optional[str] = None,
                         limit: int = Query(200, le=2000)):
    from ..execution import audit as _audit
    rows = db.list_broker_orders(trade_id=trade_id, status=status, limit=limit)
    out = {"orders": rows}
    if trade_id:
        out["audit"] = _audit.snapshot(trade_id)
    return out


@router.get("/api/execution/events")
def api_execution_events(trade_id: Optional[str] = None, limit: int = Query(300, le=3000)):
    return {"events": db.list_order_events(trade_id=trade_id, limit=limit)}


@router.post("/api/execution/kill")
def api_execution_kill(req: KillSwitchRequest):
    """Global emergency kill switch. active=true blocks all new entries / auto
    re-entry; existing positions stay monitored. `policy` sets the explicit
    emergency-exit behaviour (MONITOR = alert only, FLATTEN = allow auto exits
    on confirmed LIVE positions)."""
    from ..execution import killswitch
    if req.policy:
        killswitch.set_policy(req.policy)
    state = killswitch.activate(req.reason or "api") if req.active else killswitch.deactivate(req.reason or "api")
    return {"kill_switch": state}
