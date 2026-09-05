"""
Signal/trade history reads + the research aggregator + manual trade
mark-price/close mutations. Split out of app/main.py.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .. import db
from .. import runtime
from ..engines.paper_trading import close_trade, update_trade_price
from ..research import aggregate_research
from .schemas import CloseTradeRequest, MarkPriceRequest

router = APIRouter()


@router.get("/api/signals")
def api_signals(limit: int = Query(200, le=2000)):
    return db.list_signals(limit=limit)


@router.get("/api/trades")
def api_trades(status: Optional[str] = None, limit: int = Query(200, le=2000)):
    return db.list_trades(status=status, limit=limit)


@router.get("/api/research")
def api_research():
    return aggregate_research()


@router.post("/api/trades/mark")
async def api_mark_price(req: MarkPriceRequest):
    updated = update_trade_price(req.trade_id, req.ltp)
    if not updated:
        raise HTTPException(404, "trade not found")
    if updated.get("status") == "CLOSED":
        await runtime.manager.broadcast({"type": "trade_closed", "data": updated})
    else:
        await runtime.manager.broadcast({"type": "trade_update", "data": updated})
    return updated


@router.post("/api/trades/close")
async def api_close_trade(req: CloseTradeRequest):
    updated = close_trade(req.trade_id, req.exit_price)
    if not updated:
        raise HTTPException(404, "trade not found")
    await runtime.manager.broadcast({"type": "trade_closed", "data": updated})
    return updated
