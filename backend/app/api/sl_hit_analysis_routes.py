"""Stop-Loss Hit Analysis -- read-only, descriptive. Reads already-closed
ai_paper_trades rows; no order path, no gating, nothing else consults this."""
from fastapi import APIRouter

from ..sl_hit_analysis import engine

router = APIRouter(prefix="/api/sl-hit-analysis", tags=["sl-hit-analysis"])


@router.get("/summary")
def api_summary():
    return engine.summary()


@router.get("/by-underlying")
def api_by_underlying():
    return {"rows": engine.by_underlying()}


@router.get("/by-regime")
def api_by_regime():
    return {"rows": engine.by_regime()}


@router.get("/by-strategy")
def api_by_strategy():
    return {"rows": engine.by_strategy()}


@router.get("/by-hour")
def api_by_hour():
    return {"rows": engine.by_hour_ist()}


@router.get("/mae-overshoot")
def api_mae_overshoot():
    return engine.mae_overshoot()


@router.get("/probability-vs-stop-rate")
def api_probability_vs_stop_rate():
    return {"rows": engine.probability_vs_stop_rate()}


@router.get("")
def api_full_report():
    return engine.full_report()
