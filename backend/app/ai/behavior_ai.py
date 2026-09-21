"""
Phase 6 -- OpenRouter AI behavior analysis. Sends a COMPACT structured
context (never a raw DB dump) and requires strict JSON output. Every
failure mode degrades to a safe, clearly-labeled "AI did not weigh in"
result -- the caller (app.ai.fusion) treats that identically to "AI said
nothing alarming," never as "AI said trade."
"""
from __future__ import annotations

from dataclasses import dataclass

from . import ai_client as _client

_SYSTEM_PROMPT = (
    "You are a market-regime classification assistant for an options scalping "
    "system. You NEVER issue trade commands. You classify the CURRENT market "
    "behavior against a stated instrument profile and flag anomalies. If an "
    "orderflow snapshot is included, note whether it agrees or conflicts with "
    "the deterministic signal's direction -- set orderflow_conflict=true only "
    "when they clearly disagree, false when they agree or no orderflow data "
    "was provided. Respond with ONLY a JSON object, no prose, matching exactly "
    "this schema: "
    '{"regime": "TREND|RANGE|BREAKOUT|REVERSAL|CHOP", "profile_match": true|false, '
    '"profile_match_confidence": 0-100, "signal_validation": "PASS|FAIL|UNCERTAIN", '
    '"confidence": 0-100, "risk": "LOW|MEDIUM|HIGH", "orderflow_conflict": true|false, '
    '"warnings": [string,...], "reason_codes": [string,...]} '
    "STRICT ENUM RULES (a response using any other word for these fields is "
    "invalid and will be discarded, wasting the call): "
    '"risk" must be EXACTLY one of LOW, MEDIUM, HIGH -- never NORMAL, ELEVATED, '
    'MODERATE, or any other synonym. "regime" must be EXACTLY one of TREND, '
    'RANGE, BREAKOUT, REVERSAL, CHOP. "signal_validation" must be EXACTLY one '
    "of PASS, FAIL, UNCERTAIN. Use these exact uppercase strings with no "
    "variation."
)

_REQUIRED_KEYS = {"regime", "profile_match", "profile_match_confidence",
                  "signal_validation", "confidence", "risk", "warnings", "reason_codes"}
# orderflow_conflict is NOT in _REQUIRED_KEYS -- a model that predates this
# addition, or one that omits it despite the prompt, must not fail schema
# validation over a field this codebase can default safely (see analyze_with_ai).
_VALID_SIGNAL_VALIDATION = {"PASS", "FAIL", "UNCERTAIN"}
_VALID_RISK = {"LOW", "MEDIUM", "HIGH"}


@dataclass
class AIBehaviorResult:
    status: str              # OK | UNAVAILABLE | TIMEOUT | HTTP_ERROR | INVALID_JSON | ERROR | SCHEMA_INVALID
    regime: str | None
    profile_match: bool | None
    profile_match_confidence: float | None
    signal_validation: str | None
    confidence: float | None
    risk: str
    warnings: list
    reason_codes: list
    model_used: str | None
    latency_ms: float | None
    orderflow_conflict: bool | None = None   # None = no orderflow data was supplied to AI

    def to_dict(self) -> dict:
        return {"status": self.status, "regime": self.regime,
                "profile_match": self.profile_match,
                "profile_match_confidence": self.profile_match_confidence,
                "signal_validation": self.signal_validation, "confidence": self.confidence,
                "risk": self.risk, "warnings": self.warnings, "reason_codes": self.reason_codes,
                "model_used": self.model_used, "latency_ms": self.latency_ms,
                "orderflow_conflict": self.orderflow_conflict}


def _unavailable(status: str, error: str | None = None) -> AIBehaviorResult:
    warnings = [error] if error else []
    return AIBehaviorResult(status=status, regime=None, profile_match=None,
                            profile_match_confidence=None, signal_validation=None,
                            confidence=None, risk="UNKNOWN", warnings=warnings,
                            reason_codes=[], model_used=None, latency_ms=None,
                            orderflow_conflict=None)


def build_ai_context(symbol: str, behavior: dict, effective_profile: dict, sig: dict, *,
                     spot: float | None = None, orderflow: dict | None = None) -> dict:
    """Compact, bounded context -- numeric/categorical fields only, not a
    raw signal/chain/DB dump. `spot`/`orderflow` are optional (callers that
    don't have them, e.g. existing tests, still get a valid context -- both
    fields are simply omitted rather than sent as a fabricated null)."""
    ctx = {
        "symbol": symbol,
        "behavior_regime": behavior.get("regime"),
        "trend": behavior.get("trend"),
        "volatility": behavior.get("volatility"),
        "behavior_confidence": behavior.get("behavior_confidence"),
        "signal_quality": behavior.get("signal_quality"),
        "risk_state": behavior.get("risk_state"),
        "deterministic_signal_type": sig.get("signal_type"),
        "deterministic_direction": sig.get("direction"),
        "deterministic_score": sig.get("signal_score"),
        "deterministic_decision": sig.get("decision"),
        "cost_model_status": effective_profile.get("cost_model_status"),
        "instrument_validation_status": effective_profile.get("instrument_validation_status"),
        "regime_profile": effective_profile.get("regime"),
        # Real, already-computed fields from decide_from_context's own
        # output -- not a fresh read, not fabricated when absent.
        "support": sig.get("support"), "resistance": sig.get("resistance"),
        "vwap": sig.get("vwap"), "atr": sig.get("atr"),
        "mtf_alignment": sig.get("mtf_alignment"),
        "option_ltp": sig.get("entry"), "option_strike": sig.get("strike"),
        "option_expiry": sig.get("expiry"),
    }
    if spot is not None:
        ctx["spot"] = spot
    if orderflow is not None:
        ctx["orderflow"] = orderflow
    return ctx


def analyze_with_ai(context: dict, *, model: str | None = None) -> AIBehaviorResult:
    """Never raises. Any failure (unavailable/timeout/malformed/schema
    violation) returns a status != OK result with safe UNKNOWN/empty
    fields -- the fusion layer must treat that as neutral, not permissive."""
    result = _client.chat_completion_json(
        messages=[{"role": "system", "content": _SYSTEM_PROMPT},
                 {"role": "user", "content": str(context)}],
        model=model, max_tokens=1500)
    if result.status != "OK":
        return _unavailable(result.status, result.error)

    data = result.data or {}
    if not _REQUIRED_KEYS.issubset(data.keys()):
        return _unavailable("SCHEMA_INVALID", f"missing keys: {_REQUIRED_KEYS - data.keys()}")
    if data.get("signal_validation") not in _VALID_SIGNAL_VALIDATION:
        return _unavailable("SCHEMA_INVALID", f"bad signal_validation: {data.get('signal_validation')!r}")
    if data.get("risk") not in _VALID_RISK:
        return _unavailable("SCHEMA_INVALID", f"bad risk: {data.get('risk')!r}")
    try:
        conf = max(0.0, min(100.0, float(data.get("confidence"))))
        pm_conf = max(0.0, min(100.0, float(data.get("profile_match_confidence"))))
    except (TypeError, ValueError):
        return _unavailable("SCHEMA_INVALID", "confidence fields not numeric")

    # orderflow_conflict is optional in the response (see _REQUIRED_KEYS note
    # above) -- default False (no conflict asserted) rather than fail schema
    # validation over a field an older/smaller model might omit.
    of_conflict = data.get("orderflow_conflict")
    of_conflict = bool(of_conflict) if isinstance(of_conflict, bool) else False

    return AIBehaviorResult(
        status="OK", regime=str(data.get("regime") or ""),
        profile_match=bool(data.get("profile_match")),
        profile_match_confidence=pm_conf, signal_validation=data["signal_validation"],
        confidence=conf, risk=data["risk"],
        warnings=list(data.get("warnings") or []), reason_codes=list(data.get("reason_codes") or []),
        model_used=result.model_used, latency_ms=result.latency_ms,
        orderflow_conflict=of_conflict,
    )
