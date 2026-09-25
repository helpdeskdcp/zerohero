"""
Hedging Engine API -- Phase 2a + autonomous Phase 3. Read-only status/
positions + emergency EXIT ALL, plus arm/disarm/config for the autonomous
scan (app.hedging.runner, run on a cron -- see scripts/hedging_scan.py).
No broker call anywhere in this file: emergency-exit-all and the
autonomous scan both close/open PAPER positions using real current chain
quotes (app.optionchain.resolve), never a real order. Live trading is
untouched by this router entirely -- this engine has no live-execution
path yet. Autonomous entries stay OFF (disarmed) until POST /arm.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..hedging import capital as _capital
from ..hedging import position as _position
from ..hedging import runner as _runner
from ..optionchain import resolve as _chain_resolve

router = APIRouter(prefix="/api/hedging", tags=["hedging"])

# strong refs to keep in-flight background scans alive -- asyncio only holds
# a weak ref to a bare create_task() result, so an unreferenced task can be
# garbage-collected mid-run
_bg_tasks: set = set()


class HedgingConfigPatch(BaseModel):
    symbols: list[str] | None = None
    min_distance_pct: float | None = None
    min_oi: float | None = None
    max_spread_pct: float | None = None
    max_hedge_cost_pct_of_credit: float | None = None
    max_risk_pct: float | None = None
    max_concurrent_positions: int | None = None
    max_positions_per_symbol: int | None = None


@router.get("/status")
def api_hedging_status():
    cap = _capital.load()
    positions = _position.load_all()
    open_positions = [p for p in positions.values() if p.status == "OPEN"]
    closed_positions = [p for p in positions.values() if p.status == "CLOSED"]
    return {
        "capital": cap.to_dict(),
        "open_position_count": len(open_positions),
        "closed_position_count": len(closed_positions),
        "armed": _runner.is_armed(),
        "config": _runner.get_config(),
        "live_trading": False,
        "paper_mode": True,
    }


@router.post("/arm")
def api_hedging_arm():
    _runner.arm()
    return {"armed": True}


@router.post("/disarm")
def api_hedging_disarm():
    _runner.disarm()
    return {"armed": False}


@router.get("/config")
def api_hedging_get_config():
    return _runner.get_config()


@router.post("/config")
def api_hedging_set_config(patch: HedgingConfigPatch):
    try:
        return _runner.set_config({k: v for k, v in patch.model_dump().items() if v is not None})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/scan-now")
async def api_hedging_scan_now():
    """Manual trigger for one scan tick (same code the cron runs), run in
    the background -- scanning every configured symbol's full option chain
    can take tens of seconds (real measured latency, not a bug), long
    enough that a mobile client's connection drops mid-request before the
    server responds (confirmed live: nginx 499s here from an Android
    client). Returns immediately; poll GET /last-scan for the result.

    Uses asyncio.create_task(), not FastAPI's BackgroundTasks -- this app's
    own auth-gate @app.middleware("http") wraps every response through
    Starlette's BaseHTTPMiddleware, which is documented to silently drop a
    response's attached BackgroundTasks (confirmed live: the task never ran
    via that path). create_task() schedules independently of the response
    object entirely, so it isn't affected."""
    task = asyncio.create_task(asyncio.to_thread(_runner.scan_and_record))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return {"queued": True, "note": "scan running in background -- poll GET /api/hedging/last-scan"}


@router.get("/last-scan")
def api_hedging_last_scan():
    result = _runner.last_scan_result()
    if result is None:
        return {"status": "NONE", "note": "no scan has completed yet"}
    return result


@router.get("/positions")
def api_hedging_positions(status: str | None = None):
    positions = _position.load_all()
    rows = [p.to_dict() for p in positions.values()
           if status is None or p.status.upper() == status.upper()]
    return {"positions": rows, "count": len(rows)}


def _real_quotes_for_open_positions(positions: dict) -> dict:
    """Real current (primary_premium, hedge_premium) per open position id,
    from the live-captured chain -- never a fabricated/last-known-stale
    price presented as current. A position whose symbol/strike can't be
    resolved right now is simply absent from the returned dict (the caller
    then reports it UNRESOLVED rather than closing it blind)."""
    quotes = {}
    chains_by_symbol: dict = {}
    for pid, pos in positions.items():
        if pos.status != "OPEN":
            continue
        if pos.symbol not in chains_by_symbol:
            try:
                chains_by_symbol[pos.symbol] = _chain_resolve.get_chain(pos.symbol, "AUTO")
            except Exception:
                chains_by_symbol[pos.symbol] = None
        chain = chains_by_symbol[pos.symbol]
        if chain is None:
            continue
        side = "ce" if pos.primary_option_type == "CE" else "pe"
        primary_leg = next((getattr(r, side) for r in chain.rows
                            if r.strike == pos.primary_strike and getattr(r, side)), None)
        hedge_leg = next((getattr(r, side) for r in chain.rows
                          if r.strike == pos.hedge_strike and getattr(r, side)), None)
        if primary_leg and primary_leg.ltp is not None and hedge_leg and hedge_leg.ltp is not None:
            quotes[pid] = (primary_leg.ltp, hedge_leg.ltp)
    return quotes


@router.post("/emergency-exit-all")
def api_hedging_emergency_exit_all():
    """EXIT ALL, regardless of P&L/time -- PAPER close only, no broker
    order. A position whose current premium can't be resolved right now is
    reported UNRESOLVED, never closed at a fabricated price."""
    positions = _position.load_all()
    quotes = _real_quotes_for_open_positions(positions)
    results = _position.emergency_exit_all(current_prices=quotes)
    return {"results": results, "live_trading": False}
