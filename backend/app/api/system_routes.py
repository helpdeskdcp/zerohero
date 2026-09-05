"""
Health / diagnostics / calibration-readiness / market-calendar / credential
presence reads. Split out of app/main.py.

api_diag() needs histcap_worker, which main.py still owns (its wiring stays
there — see app/runtime.py's module docstring) -- so this module reaches back
into app.main for it rather than app.runtime. That's the one intentional
exception to "route modules only import from app.runtime".
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Query

from .. import db
from .. import runtime

router = APIRouter()


@router.get("/api/health")
def api_health():
    return {"status": "ok", "live_trading": False, "paper_mode": True}


@router.get("/api/diag")
def api_diag():
    """Read-only runtime diagnostic (PHASE 0): worker_count, leader_state,
    feed_state, last_tick/snapshot/candle/persist times. No side effects."""
    from .. import main as _main    # histcap_worker is wired in main.py, not runtime.py
    from ..diagnostics import runtime_diag
    return runtime_diag(scalp_runner=runtime.scalp_runner, autoscalp=runtime.autoscalp,
                        histcap_worker=_main.histcap_worker)


@router.get("/api/autoscalp/trade-features")
def api_trade_features(trade_id: Optional[str] = None, limit: int = Query(50, le=500)):
    """PHASE 8/9 — the immutable entry snapshot for one trade, or the most
    recent N (joined with outcome when resolved)."""
    with db.db() as conn:
        if trade_id:
            r = conn.execute("SELECT * FROM trade_entry_features WHERE trade_id=?", (trade_id,)).fetchone()
            return dict(r) if r else {"status": "NOT_FOUND"}
        rows = conn.execute(
            "SELECT f.trade_id, f.captured_ts, f.underlying, f.option_type, f.strike, "
            "f.entry_price, f.probability, f.confidence, f.greeks_source, f.data_quality_score, "
            "f.pcr_quality, f.oi_coverage, o.outcome, o.r_multiple, o.exit_reason "
            "FROM trade_entry_features f LEFT JOIN trade_exit_outcomes o USING (trade_id) "
            "ORDER BY f.captured_ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


@router.get("/api/autoscalp/calibration-report")
def api_calibration_report(limit: int = Query(5000, le=20000)):
    """PHASE 14 — Brier / log-loss / ECE / reliability table / win-rate / PF /
    expectancy / max-drawdown / false-signal-rate from RESOLVED paper trades.
    Read-only; nothing is trained."""
    from ..autoscalp.calibration_report import calibration_report
    return calibration_report(limit)


@router.get("/api/autoscalp/training-status")
def api_training_status():
    """PHASE 13/16 — how close the clean labelled dataset is to a size where
    XGBoost vs the logistic baseline can be compared. Nothing is trained here."""
    with db.db() as conn:
        n_entry = conn.execute("SELECT COUNT(*) FROM trade_entry_features").fetchone()[0]
        n_out = conn.execute("SELECT COUNT(*) FROM trade_exit_outcomes").fetchone()[0]
        clean = conn.execute(
            "SELECT COUNT(*) FROM trade_entry_features f JOIN trade_exit_outcomes o "
            "USING (trade_id) WHERE o.outcome IN ('WIN','LOSS','FLAT')").fetchone()[0]
        by_sym = {r[0]: r[1] for r in conn.execute(
            "SELECT f.underlying, COUNT(*) FROM trade_entry_features f JOIN trade_exit_outcomes o "
            "USING (trade_id) WHERE o.outcome IN ('WIN','LOSS','FLAT') GROUP BY f.underlying").fetchall()}
    target = 500
    return {"entry_snapshots": n_entry, "outcome_rows": n_out,
            "clean_labelled": clean, "by_underlying": by_sym,
            "target_for_ml_comparison": target,
            "ready_for_xgboost_eval": clean >= target,
            "note": "XGBoost training is deferred until clean_labelled >= target "
                    "with a chronological holdout; the logistic baseline stays primary."}


@router.get("/api/market/calendar")
def api_market_calendar():
    """NSE / MCX / BSE segment status + whether a restart is currently allowed."""
    from .. import market_calendar
    return market_calendar.status_all()


@router.get("/api/env-check")
def api_env_check():
    """Reports which credentials are configured WITHOUT ever revealing values."""
    keys = [
        "ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_SIGNALS_CHANNEL_ID",
    ]
    return {k: bool(os.environ.get(k)) for k in keys}
