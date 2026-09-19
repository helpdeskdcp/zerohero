"""app.ai.openrouter_client -- every test here either has no API key
configured (real UNAVAILABLE path, no network) or mocks requests.post
directly. No real network call is ever made."""
import json

import pytest
import requests

from app.ai import openrouter_client as oc


def test_unavailable_when_no_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert oc.is_available() is False
    result = oc.chat_completion_json([{"role": "user", "content": "hi"}])
    assert result.status == "UNAVAILABLE"
    assert result.data is None


def test_available_when_key_set(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake-not-real")
    assert oc.is_available() is True


def test_unavailable_when_key_set_but_no_model_configured(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")
    monkeypatch.delenv("OPENROUTER_FAST_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_FALLBACK_MODELS", raising=False)
    result = oc.chat_completion_json([{"role": "user", "content": "hi"}])
    assert result.status == "UNAVAILABLE"
    assert "model" in result.error


class _FakeResp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


def test_successful_call_parses_json_content(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENROUTER_FAST_MODEL", "test/model-a")
    body = {"choices": [{"message": {"content": json.dumps({"regime": "TREND", "confidence": 70})}}]}
    monkeypatch.setattr(oc.requests, "post", lambda *a, **k: _FakeResp(200, body))
    result = oc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "OK"
    assert result.data == {"regime": "TREND", "confidence": 70}
    assert result.model_used == "test/model-a"
    assert result.latency_ms is not None


def test_never_raises_on_network_exception(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENROUTER_FAST_MODEL", "test/model-a")
    monkeypatch.setenv("OPENROUTER_MAX_RETRIES", "1")

    def _boom(*a, **k):
        raise requests.ConnectionError("simulated network failure")
    monkeypatch.setattr(oc.requests, "post", _boom)
    result = oc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "ERROR"      # exhausted, never raised


def test_timeout_is_isolated(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENROUTER_FAST_MODEL", "test/model-a")

    def _timeout(*a, **k):
        raise requests.Timeout("simulated timeout")
    monkeypatch.setattr(oc.requests, "post", _timeout)
    result = oc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "ERROR"
    assert result.attempts[0]["status"] == "TIMEOUT"


def test_http_error_status_isolated(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENROUTER_FAST_MODEL", "test/model-a")
    monkeypatch.setattr(oc.requests, "post", lambda *a, **k: _FakeResp(500, {}))
    result = oc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "ERROR"
    assert result.attempts[0]["status"] == "HTTP_500"


def test_malformed_response_body_isolated(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENROUTER_FAST_MODEL", "test/model-a")
    monkeypatch.setattr(oc.requests, "post", lambda *a, **k: _FakeResp(200, {"no_choices_key": True}))
    result = oc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "ERROR"
    assert result.attempts[0]["status"] == "MALFORMED_RESPONSE_BODY"


def test_invalid_json_content_isolated(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENROUTER_FAST_MODEL", "test/model-a")
    body = {"choices": [{"message": {"content": "not json at all, sorry"}}]}
    monkeypatch.setattr(oc.requests, "post", lambda *a, **k: _FakeResp(200, body))
    result = oc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "ERROR"
    assert result.attempts[0]["status"] == "INVALID_JSON_CONTENT"


def test_json_wrapped_in_prose_is_extracted(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENROUTER_FAST_MODEL", "test/model-a")
    content = 'Sure, here you go: {"regime": "RANGE", "confidence": 55} -- hope that helps!'
    body = {"choices": [{"message": {"content": content}}]}
    monkeypatch.setattr(oc.requests, "post", lambda *a, **k: _FakeResp(200, body))
    result = oc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "OK"
    assert result.data["regime"] == "RANGE"


def test_falls_back_to_second_model_after_primary_fails(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENROUTER_FAST_MODEL", "test/primary")
    monkeypatch.setenv("OPENROUTER_FALLBACK_MODELS", "test/fallback")
    calls = []

    def _post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])
        if json["model"] == "test/primary":
            return _FakeResp(500, {})
        body = {"choices": [{"message": {"content": '{"regime": "TREND", "confidence": 80}'}}]}
        return _FakeResp(200, body)
    monkeypatch.setattr(oc.requests, "post", _post)
    result = oc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "OK"
    assert result.model_used == "test/fallback"
    assert calls == ["test/primary", "test/fallback"]


def test_api_key_never_appears_in_result_or_attempts(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-super-secret-value-xyz")
    monkeypatch.setenv("OPENROUTER_FAST_MODEL", "test/model-a")
    monkeypatch.setattr(oc.requests, "post", lambda *a, **k: _FakeResp(500, {}))
    result = oc.chat_completion_json([{"role": "user", "content": "x"}])
    dumped = str(result.to_dict())
    assert "sk-super-secret-value-xyz" not in dumped
