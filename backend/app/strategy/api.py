"""
Read-only API for the Strategy Verification Engine. No order path, no
live_trading -- every response is a verification result computed on demand
from real captured bars (app.market_hub.session_bars, the same read-only
histcap-backed source app.orderflow's profiling already uses), never a
fabricated one. OI is not yet wired into this live adapter (market_hub's
session bars carry no OI column) -- `oi_chg` is left None here, which the
engine already handles as "degrade, don't crash" (see scoring.py).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query

from .. import market_hub
from .base_strategy import MarketFeatures
from .config import StrategyConfig
from .strategy_verifier import StrategyVerifier

router = APIRouter()

_verifier = StrategyVerifier(StrategyConfig())
_last_result: dict | None = None


def _bars_for(symbol: str) -> list[dict]:
    today = market_hub._now_ist_date()
    rows = market_hub.session_bars(symbol, today, tf="5m")
    return [{"t": r["bar_start"], "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "v": r["v"]}
           for r in rows]


def _raw_signal_for(symbol: str) -> str | None:
    """A thin, read-only stand-in for "the existing signal generator" at the
    API layer: a plain SMA(5) vs SMA(20) cross read on the same bars, so the
    endpoint has something to verify against without importing the live
    autoscalp decision path (scalp_strategy.decide_from_context) here --
    that module has its own heavier dependencies (option chain, calibration)
    this lightweight status endpoint should not need. The backtest script
    instead compares against the REAL production signal generator
    (state_classifier.classify) -- see scripts/strategy_verification_backtest.py."""
    from ..engines.signal_engine import _sma
    bars = _bars_for(symbol)
    closes = [b["c"] for b in bars]
    fast, slow = _sma(closes, 5), _sma(closes, 20)
    if fast is None or slow is None:
        return None
    return "BUY" if fast > slow else "SELL"


def _run(symbol: str) -> dict:
    global _last_result
    bars = _bars_for(symbol)
    raw = _raw_signal_for(symbol)
    feats = MarketFeatures(bars=bars)
    result = _verifier.verify(symbol=symbol, raw_signal=raw, features=feats,
                              timestamp=datetime.utcnow().isoformat())
    _last_result = result.to_dict()
    return _last_result


@router.get("/api/strategy/verify")
def api_strategy_verify(symbol: str = Query("NIFTY")):
    """Runs a fresh verification for `symbol` against the latest captured
    session bars and returns the full audit dict."""
    return _run(symbol.upper())


@router.get("/api/strategy/status")
def api_strategy_status(symbol: str = Query("NIFTY")):
    r = _run(symbol.upper())
    return {
        "symbol": r["symbol"], "raw_signal": r["raw_signal"],
        "ce_score": r["ce_score"], "pe_score": r["pe_score"],
        "direction": r["strategy_direction"], "confidence": r["confidence"],
        "status": r["status"], "state": r["state"],
        "conditions_passed": r["conditions_passed"], "conditions_failed": r["conditions_failed"],
        "reason": r["reason"], "timestamp": r["timestamp"],
    }
