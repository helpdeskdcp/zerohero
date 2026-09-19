"""
OpenRouter adapter -- the ONLY place in this codebase that talks to
OpenRouter. Centralized so no second, independent client ever gets built.

SAFETY CONTRACT (non-negotiable, tested in tests/test_openrouter_client.py):
  - No API key hardcoded anywhere. Read only from environment.
  - This module NEVER raises out of chat_completion_json(). Every failure
    mode (no key configured, network error, timeout, non-2xx, malformed
    JSON body, malformed JSON *content*) returns a structured result with
    status != "OK" instead. The deterministic trading engine must be able
    to call this and continue exactly as if AI didn't exist.
  - No secret (the API key, or any Authorization header value) is ever
    written to a log line.
  - Model names come from configuration/environment, never hardcoded
    availability assumptions.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field

import requests

from . import metrics as _metrics

_log = logging.getLogger("chanakya.ai.openrouter")

BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")


def _env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [m.strip() for m in raw.split(",") if m.strip()]


def _config() -> dict:
    """Read fresh every call (not module-level constants) so tests can
    monkeypatch os.environ without needing a module reload."""
    return {
        "api_key": (os.environ.get("OPENROUTER_API_KEY") or "").strip(),
        "fast_model": os.environ.get("OPENROUTER_FAST_MODEL", "").strip(),
        "reasoning_model": os.environ.get("OPENROUTER_REASONING_MODEL", "").strip(),
        "fallback_models": _env_list("OPENROUTER_FALLBACK_MODELS"),
        "timeout_sec": float(os.environ.get("OPENROUTER_TIMEOUT_SEC", "8.0")),
        "max_retries": int(os.environ.get("OPENROUTER_MAX_RETRIES", "1")),
    }


@dataclass
class AIResult:
    status: str                    # OK | UNAVAILABLE | TIMEOUT | HTTP_ERROR | INVALID_JSON | ERROR
    data: dict | None
    model_used: str | None
    latency_ms: float | None
    error: str | None = None
    attempts: list = field(default_factory=list)   # [{"model":..., "status":...}] -- audit trail

    def to_dict(self) -> dict:
        return {"status": self.status, "data": self.data, "model_used": self.model_used,
                "latency_ms": self.latency_ms, "error": self.error, "attempts": self.attempts}


def is_available() -> bool:
    """Cheap, no-network check -- True only if an API key is configured.
    Callers should check this before deciding to invoke the AI path at all
    (Phase 14 performance protection: never even attempt a call that's
    certain to be UNAVAILABLE)."""
    return bool(_config()["api_key"])


def _extract_json(text: str) -> dict | None:
    """Best-effort JSON extraction from a model's text content. Tries a
    direct parse first; if the model wrapped the JSON in prose or code
    fences, falls back to the first-'{'-to-last-'}' substring. Returns
    None (never raises) if nothing parses."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def chat_completion_json(messages: list[dict], *, model: str | None = None,
                         fallback_models: list[str] | None = None,
                         timeout: float | None = None,
                         max_tokens: int = 500) -> AIResult:
    """Structured-JSON chat completion with model fallback, retry, and total
    failure isolation. Tries `model` (or OPENROUTER_FAST_MODEL if None), then
    each of `fallback_models` (or OPENROUTER_FALLBACK_MODELS) in order,
    stopping at the first one that returns valid parseable JSON content."""
    cfg = _config()
    if not cfg["api_key"]:
        return AIResult(status="UNAVAILABLE", data=None, model_used=None, latency_ms=None,
                        error="OPENROUTER_API_KEY not configured")

    primary = model or cfg["fast_model"]
    candidates = [m for m in [primary] + list(fallback_models or cfg["fallback_models"]) if m]
    if not candidates:
        return AIResult(status="UNAVAILABLE", data=None, model_used=None, latency_ms=None,
                        error="no model configured (OPENROUTER_FAST_MODEL / _FALLBACK_MODELS unset)")

    to = timeout if timeout is not None else cfg["timeout_sec"]
    attempts = []
    for mdl in candidates:
        for attempt in range(max(1, cfg["max_retries"])):
            t0 = time.monotonic()
            try:
                resp = requests.post(
                    f"{BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {cfg['api_key']}",
                            "Content-Type": "application/json"},
                    json={"model": mdl, "messages": messages, "max_tokens": max_tokens,
                         "response_format": {"type": "json_object"}},
                    timeout=to)
            except requests.Timeout:
                attempts.append({"model": mdl, "status": "TIMEOUT"})
                _log.warning("openrouter: timeout model=%s attempt=%d", mdl, attempt)
                continue
            except requests.RequestException as e:
                attempts.append({"model": mdl, "status": "NETWORK_ERROR"})
                _log.warning("openrouter: network error model=%s: %s", mdl, type(e).__name__)
                continue
            latency_ms = round((time.monotonic() - t0) * 1000, 1)

            if resp.status_code != 200:
                attempts.append({"model": mdl, "status": f"HTTP_{resp.status_code}"})
                _log.warning("openrouter: HTTP %d model=%s", resp.status_code, mdl)
                continue
            try:
                body = resp.json()
                content = body["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError):
                attempts.append({"model": mdl, "status": "MALFORMED_RESPONSE_BODY"})
                _log.warning("openrouter: malformed response body model=%s", mdl)
                continue
            parsed = _extract_json(content)
            if parsed is None:
                attempts.append({"model": mdl, "status": "INVALID_JSON_CONTENT"})
                _log.warning("openrouter: model returned non-JSON content model=%s", mdl)
                continue
            attempts.append({"model": mdl, "status": "OK"})
            _metrics.record_ai_call("OK", mdl, latency_ms, was_fallback=(mdl != primary))
            return AIResult(status="OK", data=parsed, model_used=mdl,
                           latency_ms=latency_ms, attempts=attempts)

    _metrics.record_ai_call("ERROR", None, None)
    return AIResult(status="ERROR", data=None, model_used=None, latency_ms=None,
                    error="all models/attempts exhausted", attempts=attempts)
