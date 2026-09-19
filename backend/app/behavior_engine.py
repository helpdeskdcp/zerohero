"""
Phase F/Phase-2 (OpenRouter) -- ProfileBasedBehaviorEngine.

Deliberately does NOT recompute RSI/EMA/MACD/ADX/Bollinger Bands/OI/VWAP
from raw OHLCV -- all of that already exists, is already tested, and
already runs in app.engines.sr_engine / regime_mtf / state_classifier /
option_engine, and its output is already assembled into the `sig` dict
that app.engines.scalp_strategy::decide_from_context() returns. This
module is a thin, pure INTERPRETATION layer on top of that already-real
data, converting it into the compact classification shape an AI layer or
a diagnostics API can consume -- never a second, independent indicator
implementation that could silently drift from the live one.

Pure function, no I/O, no look-ahead: every field it reads is something
the live decision loop already computed for THIS tick, before this
function is ever called.
"""
from __future__ import annotations

from dataclasses import dataclass

# state_classifier.py's 4 real signal_type states -> the TREND/RANGE/
# BREAKOUT/REVERSAL/CHOP vocabulary this layer exposes.
_SIGNAL_TYPE_TO_REGIME_BUCKET = {
    "RESISTANCE_BREAKOUT": "BREAKOUT",
    "SUPPORT_BREAKDOWN": "BREAKOUT",
    "SUPPORT_REVERSAL": "REVERSAL",
    "RESISTANCE_REVERSAL": "REVERSAL",
}


@dataclass
class BehaviorAssessment:
    symbol: str
    profile: str
    regime: str
    trend: str
    volatility: str
    market_state: str
    behavior_confidence: float
    signal_quality: float
    risk_state: str
    ai_required: bool

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "profile": self.profile, "regime": self.regime,
                "trend": self.trend, "volatility": self.volatility,
                "market_state": self.market_state,
                "behavior_confidence": self.behavior_confidence,
                "signal_quality": self.signal_quality, "risk_state": self.risk_state,
                "ai_required": self.ai_required}


def _clamp_0_100(x) -> float:
    try:
        return max(0.0, min(100.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


def analyze_behavior(symbol: str, sig: dict, cost_model_status: str) -> BehaviorAssessment:
    """`sig`: the dict decide_from_context() already returned for this tick.
    `cost_model_status`: "OK" | "UNCALIBRATED" from the instrument profile
    (app.instrument_profiles) -- feeds risk_state, never invented here."""
    mtf_regime = str(sig.get("regime") or "UNKNOWN").upper()
    signal_type = str(sig.get("signal_type") or "NONE").upper()

    if signal_type in _SIGNAL_TYPE_TO_REGIME_BUCKET:
        regime = _SIGNAL_TYPE_TO_REGIME_BUCKET[signal_type]
    elif "TRENDING" in mtf_regime:
        regime = "TREND"
    elif mtf_regime == "RANGE":
        regime = "RANGE"
    elif mtf_regime in ("NONE", "UNKNOWN"):
        regime = "CHOP"
    else:
        regime = "CHOP"

    trend = ("UP" if "UP" in mtf_regime else "DOWN" if "DOWN" in mtf_regime
            else sig.get("direction") or "UNKNOWN")

    atr = sig.get("atr")
    entry = sig.get("entry")
    if atr is not None and entry:
        try:
            atr_pct = abs(float(atr)) / abs(float(entry)) * 100.0
            volatility = "HIGH" if atr_pct > 3.0 else ("LOW" if atr_pct < 0.5 else "NORMAL")
        except (TypeError, ValueError, ZeroDivisionError):
            volatility = "UNKNOWN"
    else:
        volatility = "UNKNOWN"

    market_state = f"{regime}_{trend}"

    mtf_align = sig.get("mtf_alignment")
    signal_score = sig.get("signal_score")
    behavior_confidence = _clamp_0_100(abs(mtf_align)) if mtf_align is not None else 0.0
    signal_quality = _clamp_0_100(signal_score) if signal_score is not None else 0.0

    if cost_model_status != "OK":
        risk_state = "ELEVATED_UNCALIBRATED_COST"
    elif volatility == "HIGH":
        risk_state = "ELEVATED_VOLATILITY"
    else:
        risk_state = "NORMAL"

    # Ambiguous zone -> worth an (optional, rate-limited) AI opinion. Never
    # invoked here -- this only flags the recommendation; the caller decides
    # whether to actually spend an AI call (Phase 14 performance protection).
    ai_required = 35.0 <= behavior_confidence <= 65.0 or regime in ("BREAKOUT", "REVERSAL")

    return BehaviorAssessment(
        symbol=str(symbol or "").upper(), profile=str(symbol or "").upper(),
        regime=regime, trend=str(trend), volatility=volatility, market_state=market_state,
        behavior_confidence=behavior_confidence, signal_quality=signal_quality,
        risk_state=risk_state, ai_required=ai_required,
    )
