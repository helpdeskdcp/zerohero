"""
Phase F / Phase 14 -- read-only instrument-profile / calibration / AI
diagnostics. No broker calls, no order path, no secrets exposed (the AI
metrics endpoint reports counts/status only, never a key or a raw prompt).
Mounted from app.main like every other router.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db
from .. import instrument_profiles as _ip
from .. import regime_profiles as _rp
from ..ai import metrics as _ai_metrics
from ..ai import groq_client as _client
from ..autoscalp import trade_contamination as _contam
from ..effective_profile import select_effective_profile

router = APIRouter(prefix="/api", tags=["profiles"])

_WATCHLIST = ("NIFTY", "BANKNIFTY", "SENSEX", "NATURALGAS", "CRUDEOIL")


@router.get("/profiles")
def api_profiles():
    return {"symbols": {sym: _ip.get_instrument_profile(sym).to_dict() for sym in _WATCHLIST}}


@router.get("/profiles/{symbol}")
def api_profile_one(symbol: str):
    sym = symbol.upper()
    if sym not in _ip.REGISTRY:
        raise HTTPException(404, f"no instrument profile for {sym} "
                                 "(common-core fallback available via /effective)")
    return _ip.get_instrument_profile(sym).to_dict()


@router.get("/profiles/{symbol}/effective")
def api_profile_effective(symbol: str, regime: str = "NORMAL_DAY", is_expiry_day: bool = False):
    return select_effective_profile(symbol.upper(), regime.upper(), is_expiry_day).to_dict()


@router.get("/regimes")
def api_regimes():
    return {"regimes": {r: _rp.get_regime_profile(r).to_dict() for r in _rp.REGIMES}}


@router.get("/calibration/status")
def api_calibration_status():
    out = {}
    for sym in _WATCHLIST:
        prof = _ip.get_instrument_profile(sym)
        out[sym] = {"cost_model_status": prof.cost_model_status,
                    "validation_status": prof.validation_status.value,
                    "sample_note": prof.sample_note}
    return {"symbols": out}


@router.get("/data-quality/{symbol}")
def api_data_quality(symbol: str, limit: int = 500):
    """Retrospective contamination classification over real CLOSED AUTOSCALP
    trades for `symbol` -- never invents or silently drops a row, see
    app.autoscalp.trade_contamination."""
    sym = symbol.upper()
    trades = [t for t in db.list_trades(strategy="AUTOSCALP", limit=limit)
             if t.get("status") == "CLOSED" and t.get("underlying") == sym]
    return _contam.data_quality_report(trades)


@router.get("/ai/status")
def api_ai_status():
    """Never returns the API key or any header -- only config status/model
    name/counters (see app.ai.metrics docstring: resets on restart, not a
    persisted audit trail -- the persisted trail is /api/... shadow_decisions
    via db.list_shadow_decisions)."""
    diag = _client.diagnostics()
    m = _ai_metrics.snapshot()
    return {
        "available": diag["groq_available"],
        "ai_provider": "groq",
        "ai_model": diag["groq_model"],
        "ai_config_status": diag["ai_config_status"],
        "ai_request_count": m["ai_requests"],
        "ai_success_count": m["ai_success"],
        "ai_failure_count": m["ai_failures"],
        "ai_avg_latency_ms": m["ai_avg_latency_ms"],
        "metrics": m,
    }
