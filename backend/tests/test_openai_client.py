"""app.ai.openai_client -- mirrors tests/test_groq_client.py's exact pattern.
Every test here either has no API key configured (real UNAVAILABLE path, no
network) or mocks requests.post directly. No real network call is ever made."""
import json

import pytest
import requests

from app.ai import openai_client as oc


def test_config_required_when_no_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert oc.is_available() is False
    assert oc.config_status() == "CONFIG_REQUIRED"
    result = oc.chat_completion_json([{"role": "user", "content": "hi"}])
    assert result.status == "CONFIG_REQUIRED"
    assert result.data is None


def test_available_when_key_and_model_set(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-not-real")
    monkeypatch.setenv("OPENAI_MODEL", "test-model-a")
    assert oc.is_available() is True
    assert oc.config_status() == "OK"
    assert oc.selected_model() == "test-model-a"


def test_config_required_when_key_set_but_no_model_configured(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert oc.is_available() is False
    result = oc.chat_completion_json([{"role": "user", "content": "hi"}])
    assert result.status == "CONFIG_REQUIRED"
    assert "model" in result.error


def test_diagnostics_never_exposes_the_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret-diag-test")
    monkeypatch.setenv("OPENAI_MODEL", "test-model-a")
    d = oc.diagnostics()
    assert d == {"openai_available": True, "openai_model": "test-model-a",
                "ai_config_status": "OK", "base_url": oc.BASE_URL}
    assert "sk-super-secret-diag-test" not in str(d)


class _FakeResp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


def test_successful_call_parses_json_content(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENAI_MODEL", "test-model-a")
    body = {"choices": [{"message": {"content": json.dumps({"regime": "TREND", "confidence": 70})}}]}
    monkeypatch.setattr(oc.requests, "post", lambda *a, **k: _FakeResp(200, body))
    result = oc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "OK"
    assert result.data == {"regime": "TREND", "confidence": 70}
    assert result.model_used == "test-model-a"
    assert result.latency_ms is not None


def test_never_raises_on_network_exception(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENAI_MODEL", "test-model-a")

    def _boom(*a, **k):
        raise requests.ConnectionError("simulated network failure")
    monkeypatch.setattr(oc.requests, "post", _boom)
    result = oc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "ERROR"


def test_timeout_is_isolated(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENAI_MODEL", "test-model-a")

    def _timeout(*a, **k):
        raise requests.Timeout("simulated timeout")
    monkeypatch.setattr(oc.requests, "post", _timeout)
    result = oc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "ERROR"
    assert result.attempts[0]["status"] == "TIMEOUT"


def test_http_error_status_isolated(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENAI_MODEL", "test-model-a")
    monkeypatch.setattr(oc.requests, "post", lambda *a, **k: _FakeResp(429, {}))
    result = oc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "ERROR"
    assert result.attempts[0]["status"] == "HTTP_429"


def test_invalid_json_content_isolated(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENAI_MODEL", "test-model-a")
    body = {"choices": [{"message": {"content": "not json at all, sorry"}}]}
    monkeypatch.setattr(oc.requests, "post", lambda *a, **k: _FakeResp(200, body))
    result = oc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "ERROR"
    assert result.attempts[0]["status"] == "INVALID_JSON_CONTENT"


def test_api_key_never_appears_in_result_or_attempts(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret-value-xyz")
    monkeypatch.setenv("OPENAI_MODEL", "test-model-a")
    monkeypatch.setattr(oc.requests, "post", lambda *a, **k: _FakeResp(500, {}))
    result = oc.chat_completion_json([{"role": "user", "content": "x"}])
    dumped = str(result.to_dict())
    assert "sk-super-secret-value-xyz" not in dumped
