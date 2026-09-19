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
    monkeypatch os.environ without needing a module reload.

    OPENROUTER_MODEL is the simple, single primary-model override (Phase G).
    OPENROUTER_FAST_MODEL/OPENROUTER_REASONING_MODEL (Phase F) remain
    available for callers doing their own fast/reasoning routing (Phase 13)
    -- OPENROUTER_MODEL just needs to work as *a* way to configure a model
    without requiring that finer-grained split. Neither name is invented as
    a default value: if none of these are set, there is no model, full stop
    -- no hardcoded model-name fallback exists anywhere in this module."""
    return {
        "api_key": (os.environ.get("OPENROUTER_API_KEY") or "").strip(),
        "model": os.environ.get("OPENROUTER_MODEL", "").strip(),
        "fast_model": os.environ.get("OPENROUTER_FAST_MODEL", "").strip(),
        "reasoning_model": os.environ.get("OPENROUTER_REASONING_MODEL", "").strip(),
        "fallback_models": _env_list("OPENROUTER_FALLBACK_MODELS"),
        "timeout_sec": float(os.environ.get("OPENROUTER_TIMEOUT_SEC", "8.0")),
        "max_retries": int(os.environ.get("OPENROUTER_MAX_RETRIES", "1")),
    }


@dataclass
class AIResult:
    status: str                    # OK | CONFIG_REQUIRED | TIMEOUT | HTTP_ERROR | INVALID_JSON | ERROR
    data: dict | None
    model_used: str | None
    latency_ms: float | None
    error: str | None = None
    attempts: list = field(default_factory=list)   # [{"model":..., "status":...}] -- audit trail

    def to_dict(self) -> dict:
        return {"status": self.status, "data": self.data, "model_used": self.model_used,
                "latency_ms": self.latency_ms, "error": self.error, "attempts": self.attempts}


def is_available() -> bool:
    """Cheap, no-network check -- True only if an API key AND at least one
    model are configured. Callers should check this before deciding to
    invoke the AI path at all (Phase 14 performance protection: never even
    attempt a call that's certain to be CONFIG_REQUIRED)."""
    cfg = _config()
    return bool(cfg["api_key"]) and bool(cfg["model"] or cfg["fast_model"])


def config_status() -> str:
    """OK | CONFIG_REQUIRED -- never a network call, purely a config check."""
    return "OK" if is_available() else "CONFIG_REQUIRED"


def selected_model() -> str | None:
    """The model that would actually be used as primary right now, or None
    if none is configured. OPENROUTER_MODEL wins if set (Phase G's simple
    single-model config); otherwise OPENROUTER_FAST_MODEL (Phase F's
    fast/reasoning routing)."""
    cfg = _config()
    return cfg["model"] or cfg["fast_model"] or None


def diagnostics() -> dict:
    """Safe, secret-free config+status snapshot for /api/ai/status."""
    return {
        "openrouter_available": is_available(),
        "openrouter_model": selected_model(),
        "ai_config_status": config_status(),
        "base_url": BASE_URL,
    }


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
        _metrics.record_ai_call("CONFIG_REQUIRED", None, None)
        return AIResult(status="CONFIG_REQUIRED", data=None, model_used=None, latency_ms=None,
                        error="OPENROUTER_API_KEY not configured")

    primary = model or cfg["model"] or cfg["fast_model"]
    candidates = [m for m in [primary] + list(fallback_models or cfg["fallback_models"]) if m]
    if not candidates:
        _metrics.record_ai_call("CONFIG_REQUIRED", None, None)
        return AIResult(status="CONFIG_REQUIRED", data=None, model_used=None, latency_ms=None,
                        error="no model configured (OPENROUTER_MODEL / OPENROUTER_FAST_MODEL / "
                              "_FALLBACK_MODELS unset)")

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


def run_smoke_test() -> dict:
    """ONE minimal, harmless real network call -- no trading/broker content,
    no private data in the prompt. Records model/status/latency only; never
    stores the raw model response. Returns
    {"status": "SKIPPED", "reason": "..."} without ever touching the network
    if no key/model is configured -- callers must never report a smoke test
    as having run when it didn't."""
    if not is_available():
        return {"status": "SKIPPED", "reason": "API_KEY_NOT_CONFIGURED" if not _config()["api_key"]
                else "MODEL_NOT_CONFIGURED", "model": None, "latency_ms": None,
                "retry_count": 0, "http_status": None}
    result = chat_completion_json(
        messages=[{"role": "system", "content": "Respond with only this JSON object, nothing else."},
                 {"role": "user", "content": '{"ping": "pong"}'}],
        max_tokens=20)
    http_status = None
    for a in reversed(result.attempts):
        if a["status"].startswith("HTTP_"):
            http_status = int(a["status"].split("_", 1)[1])
            break
    return {"status": result.status, "reason": result.error, "model": result.model_used,
           "latency_ms": result.latency_ms, "retry_count": max(0, len(result.attempts) - 1),
           "http_status": http_status}
