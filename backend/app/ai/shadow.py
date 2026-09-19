"""
Phase 12 -- shadow-mode comparison logging. Computes and PERSISTS
(never acts on) the profile-engine + AI-fusion result for every tick,
for later comparison against OLD ENGINE outcomes. Opt-in, default OFF
(app.autoscalp.runner's ai_shadow_mode config gate) -- and even when on,
NEVER raises into the caller and NEVER influences the real paper trade,
matching the exact pattern already established for fsg_shadow_log.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .. import db
from ..behavior_engine import analyze_behavior
from . import behavior_ai
from . import fusion as _fusion
from . import metrics as _metrics
from . import openrouter_client as _client

_log = logging.getLogger("chanakya.ai.shadow")


def run_shadow_decision(symbol: str, sig: dict, effective_profile: dict) -> dict | None:
    """Best-effort. Returns the fused decision dict for callers that want
    it (e.g. tests), or None on any internal failure -- but a failure here
    must NEVER propagate to the caller's own decision flow."""
    try:
        cost_status = effective_profile.get("cost_model_status", "UNCALIBRATED")
        behavior = analyze_behavior(symbol, sig, cost_status)
        _metrics.record_profile_usage(symbol)
        _metrics.record_regime(behavior.regime)

        # Distinguish "AI isn't configured at all" from "AI is configured
        # but this tick didn't need it" (Phase 14 performance protection --
        # never call AI on every tick).
        ai_result = ({"status": "CONFIG_REQUIRED"} if not _client.is_available()
                    else {"status": "SKIPPED_NOT_REQUIRED"})
        if behavior.ai_required and _client.is_available():
            ctx = behavior_ai.build_ai_context(symbol, behavior.to_dict(), effective_profile, sig)
            ai_result = behavior_ai.analyze_with_ai(ctx).to_dict()

        fused = _fusion.fuse_decision(
            deterministic_decision=sig.get("decision"),
            deterministic_score=sig.get("signal_score"),
            behavior=behavior.to_dict(), ai_result=ai_result, cost_model_status=cost_status)

        _metrics.record_decision(
            ai_disagreed=(ai_result.get("status") == "OK"
                         and ai_result.get("signal_validation") == "FAIL"),
            is_no_trade=(fused.final_state == "NO_TRADE"),
            ai_rejected=(ai_result.get("signal_validation") == "FAIL"))

        db.insert_shadow_decision({
            "ts": datetime.now(timezone.utc).isoformat(), "symbol": symbol,
            "profile": behavior.profile, "regime": behavior.regime,
            "deterministic_decision": sig.get("decision"),
            "deterministic_score": sig.get("signal_score"),
            "behavior_json": json.dumps(behavior.to_dict(), default=str),
            "ai_status": ai_result.get("status"),
            "ai_json": json.dumps(ai_result, default=str),
            "fused_final_state": fused.final_state,
            "fused_confidence": fused.final_confidence,
            "reason_codes": json.dumps(fused.reason_codes),
        })
        return fused.to_dict()
    except Exception as e:
        _log.warning("ai shadow decision failed for %s (non-fatal, trading unaffected): %r",
                     symbol, e)
        return None
