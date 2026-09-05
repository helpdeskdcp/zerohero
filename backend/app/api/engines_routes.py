"""
Raw engine-level endpoints (single-engine, no orchestration) + the full
signal->risk->paper-trade pipeline. Split out of app/main.py.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter

from .. import runtime
from ..engines.signal_engine import run_signal_engine
from ..engines.oi_options_engine import run_oi_options_engine
from ..engines.risk_engine import run_risk_engine
from ..orchestrator import run_pipeline
from .schemas import SignalRequest

_log = logging.getLogger("chanakya.api")

router = APIRouter()


@router.post("/api/engine/signal")
def api_signal_engine(payload: dict):
    return run_signal_engine(payload)


@router.post("/api/engine/oi")
def api_oi_engine(payload: dict):
    return run_oi_options_engine(payload)


@router.post("/api/engine/risk")
def api_risk_engine(payload: dict):
    return run_risk_engine(payload)


@router.post("/api/run")
async def api_run_pipeline(req: SignalRequest):
    try:
        # run_pipeline() makes blocking broker / network calls; run it off the
        # event loop so /ws broadcasts and the in-process ScalpRunner keep
        # ticking while it is in flight.
        result = await asyncio.to_thread(run_pipeline, req.model_dump(exclude_none=True))
        await runtime.manager.broadcast({"type": "signal", "data": result.get("contract") or {}})
        if result.get("trade"):
            await runtime.manager.broadcast({"type": "trade_open", "data": result["trade"]})
        return result
    except Exception as exc:
        # A pipeline failure (broker outage, malformed payload, or an unexpected
        # bug) must never become an opaque HTTP 500, leak broker credentials or
        # internals to the client, or drop the fail-closed NO_TRADE contract.
        # Log the full traceback server-side; return only a coarse error class.
        _log.exception("api_run_pipeline failed")
        expected = isinstance(exc, (ConnectionError, TimeoutError, OSError))
        return {"contract": {"decision": "NO_TRADE", "approved": False,
                             "final_decision": "NO_TRADE",
                             "data_status": "DATA_UNAVAILABLE",
                             "reason": "MARKET_DATA_UNAVAILABLE"},
                "trade": None, "error": "DATA_UNAVAILABLE",
                "error_class": "UPSTREAM_DATA_UNAVAILABLE" if expected else "INTERNAL_ERROR"}
