"""
The autonomous PAPER scalper (P7): status/signals/snapshots/report reads,
watchlist + config management, arm/disarm, and the kill-switch alias.
Split out of app/main.py.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .. import db
from .. import instruments
from .. import runtime
from .schemas import KillSwitchRequest

router = APIRouter()


@router.get("/api/autoscalp/status")
def api_autoscalp_status(compact: bool = False):
    st = runtime.autoscalp.status()
    return runtime._compact(st) if compact else runtime._label_marks(st)


@router.get("/api/autoscalp/signals")
def api_autoscalp_signals(status: Optional[str] = None, symbol: Optional[str] = None,
                          limit: int = Query(200, le=2000)):
    return db.list_scalp_signals(source="LIVE", status=status, symbol=symbol, limit=limit)


@router.get("/api/autoscalp/snapshots")
def api_autoscalp_snapshots(symbol: Optional[str] = None, limit: int = Query(200, le=2000)):
    return db.list_live_snapshots(symbol=symbol, limit=limit)


@router.get("/api/autoscalp/report")
def api_autoscalp_report(day: Optional[str] = None):
    """Per-symbol rollup of an IST trading day (default today): trades, W/L,
    net points, avg R, exit-reason / decision / regime distribution, ZTH legs,
    and why entries were refused."""
    from ..autoscalp import report as _report
    try:
        return _report.session_report(day)
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")


@router.get("/api/autoscalp/selfcheck")
def api_autoscalp_selfcheck():
    """One-glance operational readiness of the autonomous engine."""
    from ..autoscalp import report as _report
    return _report.self_check(runtime.autoscalp)


@router.get("/api/autoscalp/universe")
def api_autoscalp_universe():
    """Grouped, searchable symbol universe for the dashboard picker.
    `watchlist` = symbols the runner actually trades; the groups are for
    viewing S/R / VWAP / regime of any symbol."""
    reg = instruments.registry()
    nse_idx, mcx = [], []
    for k, v in sorted(reg.items()):
        ex = str(v.get("exchange") or "").upper()
        (mcx if ex == "MCX" else nse_idx).append(k)
    equity_fno = []
    try:
        seen = set()
        for r in instruments.master_rows():
            if r.get("exch_seg") == "NFO" and r.get("instrumenttype") == "OPTSTK":
                n = str(r.get("name") or "").upper()
                if n and n not in seen:
                    seen.add(n)
                    equity_fno.append(n)
        equity_fno.sort()
    except Exception:
        equity_fno = []
    wl = []
    try:
        wl = list(runtime.autoscalp.get_config().get("symbols") or [])
    except Exception:
        pass
    return {
        "watchlist": wl,
        "groups": {
            "NSE Index": [s for s in nse_idx if s not in mcx],
            "MCX": mcx,
            "Equity (F&O)": equity_fno,
        },
    }


@router.get("/api/autoscalp/config")
def api_autoscalp_get_config():
    return runtime.autoscalp.get_config()


@router.post("/api/autoscalp/config")
def api_autoscalp_set_config(payload: dict):
    try:
        return runtime.autoscalp.set_config(payload or {})
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/api/autoscalp/watchlist")
def api_autoscalp_watchlist(payload: dict):
    """Add or remove one symbol from the trading watchlist.
    {"symbol": "RELIANCE", "action": "add" | "remove"}"""
    sym = str((payload or {}).get("symbol") or "").strip().upper()
    action = str((payload or {}).get("action") or "").lower()
    if not sym or action not in ("add", "remove"):
        raise HTTPException(422, "symbol and action ('add'|'remove') required")
    cur = list(runtime.autoscalp.get_config().get("symbols") or [])
    if action == "add" and sym not in cur:
        cur.append(sym)
    elif action == "remove":
        cur = [s for s in cur if s != sym]
    if not cur:
        raise HTTPException(422, "watchlist cannot be empty")
    runtime.autoscalp.set_config({"symbols": cur})
    return {"symbols": cur}


@router.post("/api/autoscalp/arm")
def api_autoscalp_arm():
    runtime.autoscalp.start()
    runtime.autoscalp.arm()
    return runtime.autoscalp.status()


@router.post("/api/autoscalp/disarm")
def api_autoscalp_disarm():
    runtime.autoscalp.disarm()
    return runtime.autoscalp.status()


@router.post("/api/autoscalp/kill")
def api_autoscalp_kill(req: KillSwitchRequest):
    """Reuses the global execution kill switch — blocks all new autoscalp
    entries. Open PAPER positions keep being monitored."""
    from ..execution import killswitch
    state = (killswitch.activate(req.reason or "autoscalp-api") if req.active
             else killswitch.deactivate(req.reason or "autoscalp-api"))
    return {"kill_switch": state, "autoscalp": runtime.autoscalp.status()}
