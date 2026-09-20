"""
Groq adapter -- the ONLY place in this codebase that talks to Groq.
Centralized so no second, independent client ever gets built. Groq's API
is OpenAI-compatible (same /chat/completions shape).

SAFETY CONTRACT (non-negotiable, tested in tests/test_groq_client.py):
  - No API key hardcoded anywhere. Read only from environment.
  - This module NEVER raises out of chat_completion_json(). Every failure
    mode (no key configured, network error, timeout, non-2xx, malformed
    JSON body, malformed JSON *content*) returns a structured result with
    status != "OK" instead.
  - No secret (the API key, or any Authorization header value) is ever
    written to a log line.
  - Model names come from configuration/environment, never hardcoded
    availability assumptions.

Env:
  GROQ_API_KEY         -- required
  GROQ_MODEL            -- primary model (e.g. "openai/gpt-oss-120b" or
                           "llama-3.3-70b-versatile"); no hardcoded default
  GROQ_FALLBACK_MODELS  -- comma-separated, optional
  GROQ_BASE_URL         -- default https://api.groq.com/openai/v1
  GROQ_TIMEOUT_SEC      -- default 8.0
  GROQ_MAX_RETRIES      -- default 1
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

import os
import requests

from . import metrics as _metrics

_log = logging.getLogger("chanakya.ai.groq")

BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")


def _env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [m.strip() for m in raw.split(",") if m.strip()]


def _config() -> dict:
    """Read fresh every call (not module-level constants) so tests can
    monkeypatch os.environ without needing a module reload."""
    return {
        "api_key": (os.environ.get("GROQ_API_KEY") or "").strip(),
        "model": os.environ.get("GROQ_MODEL", "").strip(),
        "fallback_models": _env_list("GROQ_FALLBACK_MODELS"),
        "timeout_sec": float(os.environ.get("GROQ_TIMEOUT_SEC", "8.0")),
        "max_retries": int(os.environ.get("GROQ_MAX_RETRIES", "1")),
    }


@dataclass
class AIResult:
    status: str                    # OK | CONFIG_REQUIRED | TIMEOUT | HTTP_ERROR | INVALID_JSON | ERROR
    data: dict | None
    model_used: str | None
    latency_ms: float | None
    error: str | None = None
    attempts: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"status": self.status, "data": self.data, "model_used": self.model_used,
                "latency_ms": self.latency_ms, "error": self.error, "attempts": self.attempts}


def is_available() -> bool:
    cfg = _config()
    return bool(cfg["api_key"]) and bool(cfg["model"])


def config_status() -> str:
    return "OK" if is_available() else "CONFIG_REQUIRED"


def selected_model() -> str | None:
    cfg = _config()
    return cfg["model"] or None


def diagnostics() -> dict:
    """Safe, secret-free config+status snapshot."""
    return {
        "groq_available": is_available(),
        "groq_model": selected_model(),
        "ai_config_status": config_status(),
        "base_url": BASE_URL,
    }


def _extract_json(text: str) -> dict | None:
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
    cfg = _config()
    if not cfg["api_key"]:
        _metrics.record_ai_call("CONFIG_REQUIRED", None, None)
        return AIResult(status="CONFIG_REQUIRED", data=None, model_used=None, latency_ms=None,
                        error="GROQ_API_KEY not configured")

    primary = model or cfg["model"]
    candidates = [m for m in [primary] + list(fallback_models or cfg["fallback_models"]) if m]
    if not candidates:
        _metrics.record_ai_call("CONFIG_REQUIRED", None, None)
        return AIResult(status="CONFIG_REQUIRED", data=None, model_used=None, latency_ms=None,
                        error="no model configured (GROQ_MODEL / GROQ_FALLBACK_MODELS unset)")

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
                _log.warning("groq: timeout model=%s attempt=%d", mdl, attempt)
                continue
            except requests.RequestException as e:
                attempts.append({"model": mdl, "status": "NETWORK_ERROR"})
                _log.warning("groq: network error model=%s: %s", mdl, type(e).__name__)
                continue
            latency_ms = round((time.monotonic() - t0) * 1000, 1)

            if resp.status_code != 200:
                attempts.append({"model": mdl, "status": f"HTTP_{resp.status_code}"})
                _log.warning("groq: HTTP %d model=%s", resp.status_code, mdl)
                continue
            try:
                body = resp.json()
                content = body["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError):
                attempts.append({"model": mdl, "status": "MALFORMED_RESPONSE_BODY"})
                _log.warning("groq: malformed response body model=%s", mdl)
                continue
            parsed = _extract_json(content)
            if parsed is None:
                attempts.append({"model": mdl, "status": "INVALID_JSON_CONTENT"})
                _log.warning("groq: model returned non-JSON content model=%s", mdl)
                continue
            attempts.append({"model": mdl, "status": "OK"})
            _metrics.record_ai_call("OK", mdl, latency_ms, was_fallback=(mdl != primary))
            return AIResult(status="OK", data=parsed, model_used=mdl,
                           latency_ms=latency_ms, attempts=attempts)

    _metrics.record_ai_call("ERROR", None, None)
    return AIResult(status="ERROR", data=None, model_used=None, latency_ms=None,
                    error="all models/attempts exhausted", attempts=attempts)


def run_smoke_test() -> dict:
    """ONE minimal, harmless real network call. Never touches the network
    if unconfigured; never stores the raw model response.

    max_tokens=500 (not a small value like 20): Groq's available models
    (the openai/gpt-oss-* family) are reasoning models that spend a
    *variable*, non-deterministic amount of the completion budget on hidden
    reasoning_tokens before the visible JSON content -- confirmed via real
    calls ranging from ~80 to 150+ reasoning tokens for the identical
    prompt. A small fixed budget (20, or even 150) intermittently produces
    an empty/truncated response that fails response_format's JSON
    validation. 500 matches chat_completion_json's own default and is
    reliable in practice; the smoke call is still trivially cheap either
    way."""
    if not is_available():
        return {"status": "SKIPPED", "reason": "API_KEY_NOT_CONFIGURED" if not _config()["api_key"]
                else "MODEL_NOT_CONFIGURED", "model": None, "latency_ms": None,
                "retry_count": 0, "http_status": None}
    result = chat_completion_json(
        messages=[{"role": "system", "content": "Respond with only this JSON object, nothing else."},
                 {"role": "user", "content": '{"ping": "pong"}'}],
        max_tokens=500)
    http_status = None
    for a in reversed(result.attempts):
        if a["status"].startswith("HTTP_"):
            http_status = int(a["status"].split("_", 1)[1])
            break
    return {"status": result.status, "reason": result.error, "model": result.model_used,
           "latency_ms": result.latency_ms, "retry_count": max(0, len(result.attempts) - 1),
           "http_status": http_status}
