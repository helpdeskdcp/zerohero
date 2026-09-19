"""
Phase 15 -- lightweight in-process observability for the AI layer. No
secrets are ever recorded here (only counts/status/latency). Resets on
process restart -- this is a live-process counter, not a persisted audit
trail (the persisted trail is app.ai.shadow's shadow_decisions table).
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_state = {
    "ai_requests": 0,
    "ai_success": 0,
    "ai_failures": 0,
    "ai_fallback_count": 0,
    "ai_latency_ms_samples": [],   # capped list, most-recent
    "profile_usage": {},           # symbol -> count
    "regime_distribution": {},     # regime -> count
    "ai_rejections": 0,            # signal_validation == FAIL
    "deterministic_vs_ai_disagreement": 0,
    "final_no_trade_count": 0,
}
_MAX_LATENCY_SAMPLES = 500


def record_ai_call(status: str, model_used: str | None, latency_ms: float | None,
                   was_fallback: bool = False) -> None:
    with _lock:
        _state["ai_requests"] += 1
        if status == "OK":
            _state["ai_success"] += 1
        else:
            _state["ai_failures"] += 1
        if was_fallback:
            _state["ai_fallback_count"] += 1
        if latency_ms is not None:
            _state["ai_latency_ms_samples"].append(latency_ms)
            if len(_state["ai_latency_ms_samples"]) > _MAX_LATENCY_SAMPLES:
                _state["ai_latency_ms_samples"].pop(0)


def record_profile_usage(symbol: str) -> None:
    with _lock:
        _state["profile_usage"][symbol] = _state["profile_usage"].get(symbol, 0) + 1


def record_regime(regime: str) -> None:
    with _lock:
        _state["regime_distribution"][regime] = _state["regime_distribution"].get(regime, 0) + 1


def record_decision(*, ai_disagreed: bool, is_no_trade: bool, ai_rejected: bool) -> None:
    with _lock:
        if ai_disagreed:
            _state["deterministic_vs_ai_disagreement"] += 1
        if is_no_trade:
            _state["final_no_trade_count"] += 1
        if ai_rejected:
            _state["ai_rejections"] += 1


def snapshot() -> dict:
    with _lock:
        samples = list(_state["ai_latency_ms_samples"])
        avg_latency = round(sum(samples) / len(samples), 1) if samples else None
        return {
            "ai_requests": _state["ai_requests"], "ai_success": _state["ai_success"],
            "ai_failures": _state["ai_failures"], "ai_fallback_count": _state["ai_fallback_count"],
            "ai_avg_latency_ms": avg_latency, "ai_latency_samples": len(samples),
            "profile_usage": dict(_state["profile_usage"]),
            "regime_distribution": dict(_state["regime_distribution"]),
            "ai_rejections": _state["ai_rejections"],
            "deterministic_vs_ai_disagreement": _state["deterministic_vs_ai_disagreement"],
            "final_no_trade_count": _state["final_no_trade_count"],
        }


def _reset_for_tests() -> None:
    with _lock:
        for k in ("ai_requests", "ai_success", "ai_failures", "ai_fallback_count",
                  "ai_rejections", "deterministic_vs_ai_disagreement", "final_no_trade_count"):
            _state[k] = 0
        _state["ai_latency_ms_samples"] = []
        _state["profile_usage"] = {}
        _state["regime_distribution"] = {}
