"""
OpenAI adapter -- the ONLY place in this codebase that talks to OpenAI.
Mirrors app.ai.groq_client's exact safety contract (same reasoning that
applied there applies here: shadow-mode AI must never be able to take down
or destabilize anything it touches).

SAFETY CONTRACT (identical to groq_client.py, non-negotiable):
  - No API key hardcoded anywhere. Read only from environment.
  - This module NEVER raises out of chat_completion_json(). Every failure
    mode (no key configured, network error, timeout, non-2xx, malformed
    JSON body, malformed JSON *content*) returns a structured result with
    status != "OK" instead.
  - No secret (the API key, or any Authorization header value) is ever
    written to a log line.
  - Model name comes from configuration/environment, never a hardcoded
    availability assumption.

Env:
  OPENAI_API_KEY    -- required
  OPENAI_MODEL      -- required, no hardcoded default (matches GROQ_MODEL's
                       own convention -- see groq_client.py's module docstring)
  OPENAI_BASE_URL   -- default https://api.openai.com/v1
  OPENAI_TIMEOUT_SEC -- default 8.0
  OPENAI_MAX_RETRIES -- default 1
"""
from __future__ import annotations

import json
import logging
import time

import os
import requests

from . import metrics as _metrics
from .groq_client import AIResult

_log = logging.getLogger("chanakya.ai.openai")

BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")


def _config() -> dict:
    """Read fresh every call (not module-level constants) so tests can
    monkeypatch os.environ without needing a module reload."""
    return {
        "api_key": (os.environ.get("OPENAI_API_KEY") or "").strip(),
        "model": os.environ.get("OPENAI_MODEL", "").strip(),
        "timeout_sec": float(os.environ.get("OPENAI_TIMEOUT_SEC", "8.0")),
        "max_retries": int(os.environ.get("OPENAI_MAX_RETRIES", "1")),
    }


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
        "openai_available": is_available(),
        "openai_model": selected_model(),
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
                         timeout: float | None = None,
                         max_tokens: int = 500) -> AIResult:
    cfg = _config()
    if not cfg["api_key"]:
        _metrics.record_ai_call("CONFIG_REQUIRED", None, None)
        return AIResult(status="CONFIG_REQUIRED", data=None, model_used=None, latency_ms=None,
                        error="OPENAI_API_KEY not configured")

    mdl = model or cfg["model"]
    if not mdl:
        _metrics.record_ai_call("CONFIG_REQUIRED", None, None)
        return AIResult(status="CONFIG_REQUIRED", data=None, model_used=None, latency_ms=None,
                        error="no model configured (OPENAI_MODEL unset)")

    to = timeout if timeout is not None else cfg["timeout_sec"]
    attempts = []
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
            _log.warning("openai: timeout model=%s attempt=%d", mdl, attempt)
            continue
        except requests.RequestException as e:
            attempts.append({"model": mdl, "status": "NETWORK_ERROR"})
            _log.warning("openai: network error model=%s: %s", mdl, type(e).__name__)
            continue
        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        if resp.status_code != 200:
            attempts.append({"model": mdl, "status": f"HTTP_{resp.status_code}"})
            _log.warning("openai: HTTP %d model=%s", resp.status_code, mdl)
            continue
        try:
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            attempts.append({"model": mdl, "status": "MALFORMED_RESPONSE_BODY"})
            _log.warning("openai: malformed response body model=%s", mdl)
            continue
        parsed = _extract_json(content)
        if parsed is None:
            attempts.append({"model": mdl, "status": "INVALID_JSON_CONTENT"})
            _log.warning("openai: model returned non-JSON content model=%s", mdl)
            continue
        attempts.append({"model": mdl, "status": "OK"})
        _metrics.record_ai_call("OK", mdl, latency_ms, was_fallback=False)
        return AIResult(status="OK", data=parsed, model_used=mdl,
                       latency_ms=latency_ms, attempts=attempts)

    _metrics.record_ai_call("ERROR", None, None)
    return AIResult(status="ERROR", data=None, model_used=None, latency_ms=None,
                    error="all attempts exhausted", attempts=attempts)
