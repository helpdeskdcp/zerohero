"""
Phase 9 -- Groq independent verification for the confirmation engine's
output. Reuses app.ai.groq_client (the ONLY Groq HTTP client in this
codebase) -- no second client is built here.

AUTHORITY RULE (mirrors app.ai.fusion's downgrade-only guarantee, but this
is an INDEPENDENT pipeline -- fusion.py itself is not imported or
modified): Groq can only confirm the confirmation engine's state, or
flag a conflict that forces the result toward NO_TRADE/REVIEW. It can
never upgrade WATCH/SETUP_FORMING into CONFIRMED, and can never override
an already-INVALIDATED state.
"""
from __future__ import annotations

from ..ai import groq_client as _client

_SYSTEM_PROMPT = (
    "You are an independent verification and contradiction-detection assistant "
    "for a market research confirmation engine. You NEVER issue a trade command "
    "and you NEVER upgrade a WATCH or SETUP_FORMING state into CONFIRMED. Your "
    "job is to check the given evidence for internal contradictions and note "
    "anything the deterministic engine may have missed. Respond with ONLY a "
    "JSON object, no prose, matching exactly this schema: "
    '{"agrees_with_engine": true|false, "contradiction_found": true|false, '
    '"contradiction_note": string, "confidence": 0-100}'
)

_REQUIRED_KEYS = {"agrees_with_engine", "contradiction_found", "contradiction_note", "confidence"}


def build_verification_payload(confirmation_result: dict, *, regime: str | None = None,
                               underlying: str | None = None) -> dict:
    """Compact -- state, confidence bucket, and the evidence/conflict/missing
    LISTS only (group names, not raw feature values). Never a raw event
    dump."""
    return {
        "underlying": underlying, "regime": regime,
        "engine_state": confirmation_result.get("state"),
        "engine_confidence_bucket": confirmation_result.get("confidence_bucket"),
        "evidence_for": confirmation_result.get("evidence_for"),
        "evidence_against": confirmation_result.get("evidence_against"),
        "missing_evidence": confirmation_result.get("missing_evidence"),
    }


def verify(confirmation_result: dict, *, regime: str | None = None,
          underlying: str | None = None) -> dict:
    """Never raises (mirrors app.ai.behavior_ai.analyze_with_ai's contract).
    UNAVAILABLE/malformed Groq output is neutral -- the confirmation
    engine's own state stands unchanged."""
    if not _client.is_available():
        return {"status": "CONFIG_REQUIRED", "final_state": confirmation_result.get("state")}

    payload = build_verification_payload(confirmation_result, regime=regime, underlying=underlying)
    result = _client.chat_completion_json(
        messages=[{"role": "system", "content": _SYSTEM_PROMPT},
                 {"role": "user", "content": str(payload)}],
        max_tokens=500)
    if result.status != "OK":
        return {"status": result.status, "final_state": confirmation_result.get("state")}

    data = result.data or {}
    if not _REQUIRED_KEYS.issubset(data.keys()):
        return {"status": "SCHEMA_INVALID", "final_state": confirmation_result.get("state")}

    return merge_verification(confirmation_result, data)


def merge_verification(confirmation_result: dict, groq_data: dict) -> dict:
    """Pure merge -- the only function that decides the final state, so it
    can be unit-tested without a network call. `groq_data` is Groq's
    already-schema-checked response dict."""
    engine_state = confirmation_result.get("state")
    contradiction = bool(groq_data.get("contradiction_found"))
    final_state = engine_state

    if engine_state in ("CONFIRMED", "SETUP_FORMING") and contradiction:
        # Downgrade only -- never the reverse.
        final_state = "REVIEW"
    # Every other case (agreement, or contradiction on an already
    # NO_TRADE/INVALIDATED/WATCH state) leaves engine_state exactly as-is --
    # there is no code path here that can move a state TOWARD CONFIRMED.

    return {
        "status": "OK", "final_state": final_state, "engine_state": engine_state,
        "groq_agrees": groq_data.get("agrees_with_engine"),
        "groq_contradiction_found": contradiction,
        "groq_contradiction_note": groq_data.get("contradiction_note"),
        "groq_confidence": groq_data.get("confidence"),
    }
