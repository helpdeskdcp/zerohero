"""
External broker position tracking (MONITOR-ONLY -- never places an order),
manual position levels, and multi-leg combos. Split out of app/main.py.
"""
from __future__ import annotations

import math
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .. import combos
from .. import db
from .. import instruments
from .. import runtime
from ..engines.paper_trading import close_trade, open_trade
from .schemas import ComboLevelsRequest, ComboRequest, CloseTradeRequest, LevelsRequest, TrackPositionRequest

router = APIRouter()


def _positive_finite(value, name: str, *, required: bool = False):
    if value is None:
        if required:
            raise HTTPException(422, f"{name} is required")
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(422, f"{name} must be a finite positive number")
    if not math.isfinite(number) or number <= 0:
        raise HTTPException(422, f"{name} must be a finite positive number")
    return number


def _nonnegative_finite(value, name: str):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(422, f"{name} must be a finite non-negative number")
    if not math.isfinite(number) or number < 0:
        raise HTTPException(422, f"{name} must be a finite non-negative number")
    return number


def _validate_monitor_levels(direction, entry, target=None, stop=None):
    """Validate monitor-only levels without inventing optional missing levels."""
    direction = str(direction or "").upper()
    if direction not in ("BUY", "SELL"):
        raise HTTPException(422, "direction must be BUY or SELL")
    entry = _positive_finite(entry, "entry", required=True)
    target = _positive_finite(target, "target")
    stop = _positive_finite(stop, "stop")
    if target is not None and stop is not None:
        valid = (stop < entry < target) if direction == "BUY" else (target < entry < stop)
        if not valid:
            rule = "stop < entry < target" if direction == "BUY" else "target < entry < stop"
            raise HTTPException(422, f"invalid {direction} levels: require {rule}")
    return direction, entry, target, stop


@router.post("/api/positions/track")
async def api_track_position(req: TrackPositionRequest):
    """Register a real broker position for MONITOR-ONLY tracking. The app marks
    it to the live WebSocket feed and alerts (WS + Telegram) on target / stop.
    It NEVER places a broker order."""
    direction, entry, target, stop = _validate_monitor_levels(req.direction, req.entry, req.target, req.stop)
    trailing_stop = _nonnegative_finite(req.trailing_stop, "trailing_stop")
    tok = req.symboltoken
    exch = req.exchange
    if not tok:
        meta = instruments.resolve(req.symbol)
        if meta:
            tok, exch = meta.get("symboltoken"), exch or meta.get("exchange")
    if not tok:
        raise HTTPException(400, f"no symboltoken for '{req.symbol}' — pass symboltoken or add it via /api/instruments")

    # de-dup: if a mirror for this contract already exists (manual or auto-synced),
    # update its levels instead of creating a second OPEN row.
    existing = db.find_open_by_token(str(tok), strategy="MANUAL")
    if existing:
        db.update_trade(existing["trade_id"], {
            "target_1": target, "stop_loss": stop,
            "trailing_stop": trailing_stop or 0})
        row = db.get_trade(existing["trade_id"])
        await runtime.manager.broadcast({"type": "position_update", "data": row})
        return row

    row = open_trade({
        "signal_id": None,
        "market": exch or "", "underlying": req.symbol.upper(),
        "instrument": "OPTION" if req.option_type else "FUT",
        "expiry": req.expiry or "", "strike": req.strike or 0,
        "option_type": (req.option_type or "").upper(),
        "direction": direction, "timeframe": "",
        "entry": entry, "target_1": target, "target_2": None,
        "stop_loss": stop, "trailing_stop": trailing_stop or 0,
        "quantity": (req.lots or 1) * (req.lot_size or 1),
        "probability": None, "confidence": None, "market_regime": "",
        "oi_evidence": "", "reason": "external broker position — monitor only",
        "strategy": "MANUAL", "setup": None, "atr_pct": None,
        "max_hold_sec": None, "symboltoken": str(tok),
    })
    # the active runner picks up the new token on its next tick (it rebuilds the
    # feed subscription from list_open_managed()); don't touch the feed here —
    # a non-leader worker must never start a second WebSocket connection.
    await runtime.manager.broadcast({"type": "position_open", "data": row})
    return row


@router.get("/api/positions")
def api_positions(status: Optional[str] = None, limit: int = Query(200, le=2000)):
    return db.list_trades(status=status, limit=limit, strategy="MANUAL")


@router.get("/api/broker/positions")
def api_broker_positions():
    """Live net positions straight from Angel One (getPosition). The runner also
    auto-registers any of these that aren't tracked yet."""
    from ..connectors import angelone as _a
    return _a.fetch_positions()


@router.post("/api/positions/levels")
async def api_position_levels(req: LevelsRequest):
    """Set / update target, stop, trailing on a tracked (or auto-synced) position."""
    t = db.get_trade(req.trade_id)
    if not t or t.get("status") != "OPEN":
        raise HTTPException(404, "open position not found")
    direction, _entry, target, stop = _validate_monitor_levels(
        t.get("direction"), t.get("entry"),
        req.target if req.target is not None else t.get("target_1"),
        req.stop if req.stop is not None else t.get("stop_loss"),
    )
    fields = {}
    if req.target is not None:
        fields["target_1"] = target
    if req.stop is not None:
        fields["stop_loss"] = stop
    if req.trailing_stop is not None:
        fields["trailing_stop"] = _nonnegative_finite(req.trailing_stop, "trailing_stop")
    if not fields:
        raise HTTPException(400, "nothing to set")
    db.update_trade(req.trade_id, fields)
    updated = db.get_trade(req.trade_id)
    await runtime.manager.broadcast({"type": "position_update", "data": updated})
    return updated


@router.get("/api/positions/combos")
def api_combos():
    """Live combined figures for every strangle/combo: combined debit vs mark,
    pair P&L, expiry break-evens, distance to combined target / stop."""
    return combos.snapshot()


@router.post("/api/positions/combo")
def api_create_combo(req: ComboRequest):
    try:
        return combos.create(req.legs, kind=(req.kind or "STRANGLE"),
                             target_combined=req.target, stop_combined=req.stop,
                             trail_combined=req.trail)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/positions/combo/levels")
def api_combo_levels(req: ComboLevelsRequest):
    try:
        return combos.set_levels(req.combo_id, target_combined=req.target,
                                 stop_combined=req.stop, trail_combined=req.trail)
    except KeyError:
        raise HTTPException(404, "combo not found")


@router.post("/api/positions/untrack")
async def api_untrack_position(req: CloseTradeRequest):
    updated = close_trade(req.trade_id, req.exit_price, exit_reason="UNTRACKED")
    if not updated:
        raise HTTPException(404, "position not found")
    await runtime.manager.broadcast({"type": "position_exit", "data": updated})
    return updated
