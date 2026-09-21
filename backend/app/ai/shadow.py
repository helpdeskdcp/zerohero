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
from ..orderflow import depth as _depth
from . import behavior_ai
from . import fusion as _fusion
from . import metrics as _metrics
from . import ai_client as _client
from . import shadow_notify as _notify

_log = logging.getLogger("chanakya.ai.shadow")


def run_shadow_decision(symbol: str, sig: dict, effective_profile: dict, *,
                        telegram_cfg: dict | None = None,
                        bars_by_tf: dict | None = None) -> dict | None:
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
            # Real spot (same bars decide_from_context already saw -- no
            # fresh read) + a real resting order-book snapshot (informational
            # only, see app.orderflow.depth's own module docstring for why
            # this is labeled honestly as "resting", not "orderflow delta").
            # This is called from the LIVE runner only (never the backtest/
            # replay harness -- ai_shadow_mode is never enabled there), so
            # "latest available" is the correct, non-look-ahead cutoff here.
            spot = _notify.extract_spot(bars_by_tf)
            orderflow = _depth.snapshot_for_symbol(symbol)
            ctx = behavior_ai.build_ai_context(symbol, behavior.to_dict(), effective_profile, sig,
                                               spot=spot, orderflow=orderflow)
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

        # Telegram happens AFTER fusion is fully computed, never before --
        # the whole point of shadow mode is that nothing gets announced
        # until the deterministic decision, behavior, AI, and fusion have
        # all already run. Opt-in (telegram_cfg=None / disabled -> no-op,
        # never raises -- see shadow_notify.maybe_notify's own contract).
        # Dedup is checked against the PERSISTED table (db.shadow_signal_
        # already_sent), not just telegram_dispatcher's in-memory registry,
        # so a duplicate is still caught across a process restart.
        tg = None
        fused_dict = fused.to_dict()
        if _notify.should_notify(sig, fused_dict, telegram_cfg):
            sid = _notify.build_signal_id(symbol, sig)
            if not db.shadow_signal_already_sent(sid):
                tg = _notify.maybe_notify(symbol, sig, ai_result, fused_dict,
                                          cfg=telegram_cfg, bars_by_tf=bars_by_tf,
                                          signal_id=sid)

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
            "signal_id": tg.get("signal_id") if tg else None,
            "telegram_status": tg.get("status") if tg else None,
            "telegram_message_id": tg.get("message_id") if tg else None,
            "sent_at": datetime.now(timezone.utc).isoformat() if tg else None,
            # Forward-outcome tracking (app.ai.shadow_outcome) -- captured
            # verbatim from sig, exactly as decide_from_context() planned it
            # at this tick, never recomputed later.
            "entry": sig.get("entry"), "stop_loss": sig.get("stop_loss"),
            "target_1": sig.get("target_1"), "target_2": sig.get("target_2"),
            "option_token": sig.get("token"), "tradingsymbol": sig.get("tradingsymbol"),
            "strike": sig.get("strike"), "expiry": sig.get("expiry"),
            "max_hold_sec": sig.get("max_hold_sec"),
        })
        return fused.to_dict()
    except Exception as e:
        _log.warning("ai shadow decision failed for %s (non-fatal, trading unaffected): %r",
                     symbol, e)
        return None
