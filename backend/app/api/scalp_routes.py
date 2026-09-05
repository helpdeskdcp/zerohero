"""
The (manually-armed) ScalpRunner: raw engine, one-shot pipeline, status/feed
reads, arm/disarm, config, trade history. Split out of app/main.py.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .. import db
from .. import runtime
from ..engines.scalp_engine import run_scalp_engine
from ..scalp_pipeline import run_scalp_pipeline

router = APIRouter()


@router.post("/api/scalp/signal")
def api_scalp_signal(payload: dict):
    """Scalp engine only — raw candles in, scalp decision out."""
    return run_scalp_engine(payload)


@router.post("/api/scalp/run")
async def api_scalp_run(payload: dict):
    """One-shot scalp pipeline: data -> scalp engine -> risk -> gate -> paper trade."""
    result = await asyncio.to_thread(run_scalp_pipeline, payload or {})
    await runtime.manager.broadcast({"type": "scalp_signal", "data": result["contract"]})
    if result.get("trade"):
        await runtime.manager.broadcast({"type": "scalp_open", "data": result["trade"]})
    return result


@router.get("/api/scalp/status")
def api_scalp_status(compact: bool = False):
    st = runtime.scalp_runner.status()
    return runtime._compact(st) if compact else runtime._label_marks(st)


@router.get("/api/scalp/feed")
def api_scalp_feed(compact: bool = False):
    """Angel One WebSocket market-data feed: connection + per-token live marks."""
    st = runtime.scalp_runner.feed.status()
    return runtime._compact({"feed": st})["feed"] if compact else runtime._label_marks(st)


@router.post("/api/scalp/arm")
def api_scalp_arm():
    runtime.scalp_runner.start()
    runtime.scalp_runner.arm()
    return runtime.scalp_runner.status()


@router.post("/api/scalp/disarm")
def api_scalp_disarm():
    runtime.scalp_runner.disarm()
    return runtime.scalp_runner.status()


@router.get("/api/scalp/config")
def api_scalp_get_config():
    return runtime.scalp_runner.get_config()


@router.post("/api/scalp/config")
def api_scalp_set_config(payload: dict):
    try:
        return runtime.scalp_runner.set_config(payload or {})
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/api/scalp/trades")
def api_scalp_trades(status: Optional[str] = None, limit: int = Query(200, le=2000)):
    return db.list_trades(status=status, limit=limit, strategy="SCALP")
