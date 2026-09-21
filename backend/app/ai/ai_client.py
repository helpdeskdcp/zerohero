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

from .groq_client import AIResult
from . import groq_client as _groq
from . import openai_client as _openai

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
           "ai_config_status": config_status()}


def chat_completion_json(messages: list[dict], *, model: str | None = None,
                         fallback_models: list[str] | None = None,
                         timeout: float | None = None,
                         max_tokens: int = 500) -> AIResult:
    # messages passed by keyword throughout (not positionally) -- matches
    # app.ai.behavior_ai's own calling convention, which existing test
    # mocks (lambda **k: ...) depend on.
    groq_result = _groq.chat_completion_json(
        messages=messages, model=model, fallback_models=fallback_models,
        timeout=timeout, max_tokens=max_tokens)
    if groq_result.status == "OK":
        return groq_result

    if not _openai.is_available():
        # No second provider to try -- Groq's own result (with its own
        # attempts list) is the whole story.
        return groq_result

    openai_result = _openai.chat_completion_json(messages=messages, timeout=timeout, max_tokens=max_tokens)
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
