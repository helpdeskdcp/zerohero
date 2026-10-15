"""
Provider orchestrator -- tries Groq first (with its own internal model
fallback list, see groq_client.py), and only when Groq's entire attempt
sequence fails does it fall back to OpenAI. This is the module
app.ai.behavior_ai and app.ai.shadow import as `_client` -- neither of
them needs to know two providers exist.

Why Groq stays primary: it's the already-paid-for/free-tier default this
session standardized on (see app.ai.groq_client's own docstring). OpenAI is
a paid fallback for when Groq's account-level rate limit is exhausted
(observed in production: "all models/attempts exhausted" across every
Groq fallback model simultaneously, since the limit is per-account, not
per-model) -- never the other way around, so normal operation never
spends OpenAI credits.

SAFETY CONTRACT: identical to groq_client.py / openai_client.py --
never raises, never logs a secret, status != "OK" on any failure.
"""
from __future__ import annotations

import threading
import time as _time

from .groq_client import AIResult
from . import groq_client as _groq
from . import openai_client as _openai

# ---- per-provider circuit breaker -------------------------------------------------------------------------------------------
# Seen in production (2026-10-15): Groq answers HTTP 429 (account rate limit) and OpenAI HTTP 401 (key rejected) on EVERY call, so each AI
# decision spent several doomed HTTP requests and logged two warnings, around the clock. A provider that failed in a way retrying cannot
# fix is now skipped for a cooldown:
#   * every attempt HTTP 401/403 (credentials) -> 1 hour (needs a human; the cooldown only stops the hammering)
#   * every attempt HTTP 429 (rate limit)      -> 60 s, doubling per consecutive failure up to 15 min
#   * anything else (timeouts, 5xx, bad JSON)  -> no cooldown (transient)
# A success resets the provider. Behaviour when a provider works is unchanged. No secret is read or logged here.
_BREAKER_LOCK = threading.Lock()
_BREAKER = {"groq": {"until": 0.0, "fails": 0, "reason": None}, "openai": {"until": 0.0, "fails": 0, "reason": None}}
AUTH_COOLDOWN_SEC = 3600.0
RATE_BASE_COOLDOWN_SEC, RATE_MAX_COOLDOWN_SEC = 60.0, 900.0


def _in_cooldown(name: str) -> bool:
    with _BREAKER_LOCK:
        return _time.time() < _BREAKER[name]["until"]


def _record(name: str, result: AIResult) -> None:
    with _BREAKER_LOCK:
        b = _BREAKER[name]
        if result.status == "OK":
            b.update(until=0.0, fails=0, reason=None)
            return
        codes = [str(a.get("status", "")) for a in (result.attempts or [])]
        if codes and all(c in ("HTTP_401", "HTTP_403") for c in codes):
            b.update(until=_time.time() + AUTH_COOLDOWN_SEC, fails=b["fails"] + 1, reason="AUTH_REJECTED")
        elif codes and all(c == "HTTP_429" for c in codes):
            n = b["fails"] + 1
            b.update(until=_time.time() + min(RATE_BASE_COOLDOWN_SEC * 2 ** (n - 1), RATE_MAX_COOLDOWN_SEC), fails=n, reason="RATE_LIMITED")


def _skipped(name: str) -> AIResult:
    with _BREAKER_LOCK:
        b = _BREAKER[name]
        left = max(b["until"] - _time.time(), 0.0)
        reason = b["reason"]
    return AIResult(status="ERROR", data=None, model_used=None, latency_ms=None, error=f"{name}: skipped, {reason} ({left:.0f}s cooldown left)",
                    attempts=[{"model": None, "status": "COOLDOWN"}])


def breaker_status() -> dict:
    now = _time.time()
    with _BREAKER_LOCK:
        return {k: {"cooldown_left_sec": round(max(v["until"] - now, 0.0), 1), "consecutive_failures": v["fails"], "reason": v["reason"]}
                for k, v in _BREAKER.items()}


def _reset_breaker_for_tests() -> None:
    with _BREAKER_LOCK:
        for v in _BREAKER.values():
            v.update(until=0.0, fails=0, reason=None)

# Every non-OK Groq status is worth a fallback try (CONFIG_REQUIRED means
# Groq simply isn't set up -- OpenAI is still worth trying; TIMEOUT/ERROR
# mean Groq's own per-model retry/fallback already ran and still failed,
# so a second attempt against the SAME account would just repeat the same
# failure -- only a different provider can help).


def is_available() -> bool:
    """True if EITHER provider is usable -- callers (behavior.ai_required
    gating in shadow.py) only need to know "is it worth building a
    context", not which provider will actually answer."""
    return _groq.is_available() or _openai.is_available()


def config_status() -> str:
    return "OK" if is_available() else "CONFIG_REQUIRED"


def diagnostics() -> dict:
    """Safe, secret-free config+status snapshot for both providers, plus
    which one would actually be tried first."""
    g, o = _groq.diagnostics(), _openai.diagnostics()
    active = "groq" if _groq.is_available() else ("openai" if _openai.is_available() else None)
    return {"active_provider": active, "groq": g, "openai": o,
           "ai_config_status": config_status(), "breaker": breaker_status()}


def chat_completion_json(messages: list[dict], *, model: str | None = None,
                         fallback_models: list[str] | None = None,
                         timeout: float | None = None,
                         max_tokens: int = 500) -> AIResult:
    # messages passed by keyword throughout (not positionally) -- matches
    # app.ai.behavior_ai's own calling convention, which existing test
    # mocks (lambda **k: ...) depend on.
    if _in_cooldown("groq"):
        groq_result = _skipped("groq")
    else:
        groq_result = _groq.chat_completion_json(
            messages=messages, model=model, fallback_models=fallback_models,
            timeout=timeout, max_tokens=max_tokens)
        _record("groq", groq_result)
    if groq_result.status == "OK":
        return groq_result

    if not _openai.is_available():
        # No second provider to try -- Groq's own result (with its own
        # attempts list) is the whole story.
        return groq_result

    if _in_cooldown("openai"):
        openai_result = _skipped("openai")
    else:
        openai_result = _openai.chat_completion_json(messages=messages, timeout=timeout, max_tokens=max_tokens)
        _record("openai", openai_result)
    # Merge attempt history either way so a caller inspecting `.attempts`
    # can see the full cross-provider story, not just whichever provider
    # happened to answer last.
    combined_attempts = list(groq_result.attempts) + list(openai_result.attempts)
    if openai_result.status == "OK":
        return AIResult(status="OK", data=openai_result.data, model_used=openai_result.model_used,
                        latency_ms=openai_result.latency_ms, attempts=combined_attempts)
    return AIResult(status="ERROR", data=None, model_used=None, latency_ms=None,
                    error=f"groq: {groq_result.error} | openai: {openai_result.error}",
                    attempts=combined_attempts)
