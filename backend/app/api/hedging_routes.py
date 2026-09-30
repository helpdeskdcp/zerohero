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

router = APIRouter(prefix="/api/hedging", tags=["hedging"])

# strong refs to keep in-flight background scans alive -- asyncio only holds
# a weak ref to a bare create_task() result, so an unreferenced task can be
# garbage-collected mid-run
_bg_tasks: set = set()


class HedgingConfigPatch(BaseModel):
    symbols: list[str] | None = None
    min_distance_pct: float | None = None
    min_oi: float | None = None
    min_volume: float | None = None
    max_spread_pct: float | None = None
    max_hedge_cost_pct_of_credit: float | None = None
    max_risk_pct: float | None = None
    max_concurrent_positions: int | None = None
    max_positions_per_symbol: int | None = None
    max_portfolio_margin_pct: float | None = None


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


class CapitalResetRequest(BaseModel):
    starting_capital: float


@router.post("/capital/reset")
def api_hedging_capital_reset(req: CapitalResetRequest):
    """PAPER capital only -- never touches a real account. Refuses while
    any position is OPEN so the reset ledger never desyncs from
    already-locked margin (close/emergency-exit-all first)."""
    if req.starting_capital <= 0:
        raise HTTPException(status_code=400, detail="starting_capital must be positive")
    open_positions = [p for p in _position.load_all().values() if p.status == "OPEN"]
    if open_positions:
        raise HTTPException(status_code=400,
                            detail=f"{len(open_positions)} open position(s) -- close or emergency-exit-all first")
    return _capital.reset(req.starting_capital).to_dict()


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
    """Attaches LIVE (not <=5-min-stale-captured) current premiums + a
    live unrealized P&L to every OPEN position, using the same
    live-quote-first path app.hedging.runner's own exit-checks use
    (app.hedging.runner._current_quotes(), which tries a genuinely live
    broker LTP per leg before falling back to the captured chain). Found
    2026-09-29 (user report: no live data on the positions table): this
    route previously returned only the position's own stored fields --
    entry premiums, max_loss, and a gross_pnl that stays null until the
    position actually closes -- with nothing showing what an OPEN
    position is worth right now."""
    positions = _position.load_all()
    quotes = _runner._current_quotes(positions)
    rows = []
    for p in positions.values():
        if status is not None and p.status.upper() != status.upper():
            continue
        d = p.to_dict()
        if p.status == "OPEN":
            q = quotes.get(p.position_id)
            if q is not None:
                cur_primary, cur_hedge = q
                d["current_primary_premium"] = cur_primary
                d["current_hedge_premium"] = cur_hedge
                d["unrealized_pnl"] = _position.combined_pnl(
                    p, current_primary_premium=cur_primary, current_hedge_premium=cur_hedge)
            else:
                d["current_primary_premium"] = None
                d["current_hedge_premium"] = None
                d["unrealized_pnl"] = None
        rows.append(d)
    return {"positions": rows, "count": len(rows)}


@router.post("/emergency-exit-all")
def api_hedging_emergency_exit_all():
    """EXIT ALL, regardless of P&L/time -- PAPER close only, no broker
    order. A position whose current premium can't be resolved right now is
    reported UNRESOLVED, never closed at a fabricated price.

    Shares app.hedging.runner._current_quotes() (live-quote-first, falls
    back to the captured chain) with GET /positions above -- this route
    used to have its own, near-identical captured-chain-only copy
    (DUPLICATE_LOGIC.md item to watch for: this is exactly the pattern
    that let the K8 calibration fix miss a duplicate elsewhere).

    MCP 3.1.2 hard-safety-state requirement: EMERGENCY EXIT ALL must
    freeze new entries and never auto-re-arm. Disarmed FIRST, before any
    exit is attempted, so a scan racing this call can't open a new
    position while the emergency exit is still in flight -- and because
    arm() is only ever called from the explicit POST /arm route, disarming
    here already guarantees the "manual re-arm required" requirement with
    no further state machine needed. A ledger reconcile runs after, same
    as every routine scan tick, so a partial/UNRESOLVED exit can never
    leave the capital ledger silently desynced from the position store."""
    _runner.disarm()
    positions = _position.load_all()
    quotes = _runner._current_quotes(positions)
    results = _position.emergency_exit_all(current_prices=quotes)
    reconciliation = _runner._reconcile_capital()
    unresolved = [r for r in results if r["status"] == "UNRESOLVED"]
    return {"results": results, "live_trading": False, "armed": _runner.is_armed(),
           "state": "RECONCILIATION_REQUIRED" if unresolved else "EMERGENCY_MODE",
           "reconciliation": reconciliation}
