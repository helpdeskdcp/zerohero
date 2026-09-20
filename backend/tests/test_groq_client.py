"""app.ai.groq_client -- every test here either has no API key configured
(real UNAVAILABLE path, no network) or mocks requests.post directly. No real
network call is ever made."""
import json

import pytest
import requests

from app.ai import groq_client as gc


def test_config_required_when_no_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert gc.is_available() is False
    assert gc.config_status() == "CONFIG_REQUIRED"
    result = gc.chat_completion_json([{"role": "user", "content": "hi"}])
    assert result.status == "CONFIG_REQUIRED"
    assert result.data is None


def test_available_when_key_and_model_set(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake-not-real")
    monkeypatch.setenv("GROQ_MODEL", "test/model-a")
    assert gc.is_available() is True
    assert gc.config_status() == "OK"
    assert gc.selected_model() == "test/model-a"


def test_config_required_when_key_set_but_no_model_configured(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    assert gc.is_available() is False
    result = gc.chat_completion_json([{"role": "user", "content": "hi"}])
    assert result.status == "CONFIG_REQUIRED"
    assert "model" in result.error


def test_selected_model_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    assert gc.selected_model() is None


def test_diagnostics_never_exposes_the_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-super-secret-diag-test")
    monkeypatch.setenv("GROQ_MODEL", "test/model-a")
    d = gc.diagnostics()
    assert d == {"groq_available": True, "groq_model": "test/model-a",
                "ai_config_status": "OK", "base_url": gc.BASE_URL}
    assert "gsk-super-secret-diag-test" not in str(d)


class _FakeResp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


def test_successful_call_parses_json_content(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model-a")
    body = {"choices": [{"message": {"content": json.dumps({"regime": "TREND", "confidence": 70})}}]}
    monkeypatch.setattr(gc.requests, "post", lambda *a, **k: _FakeResp(200, body))
    result = gc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "OK"
    assert result.data == {"regime": "TREND", "confidence": 70}
    assert result.model_used == "test/model-a"
    assert result.latency_ms is not None


def test_never_raises_on_network_exception(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model-a")
    monkeypatch.setenv("GROQ_MAX_RETRIES", "1")

    def _boom(*a, **k):
        raise requests.ConnectionError("simulated network failure")
    monkeypatch.setattr(gc.requests, "post", _boom)
    result = gc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "ERROR"


def test_timeout_is_isolated(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model-a")

    def _timeout(*a, **k):
        raise requests.Timeout("simulated timeout")
    monkeypatch.setattr(gc.requests, "post", _timeout)
    result = gc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "ERROR"
    assert result.attempts[0]["status"] == "TIMEOUT"


def test_http_error_status_isolated(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model-a")
    monkeypatch.setattr(gc.requests, "post", lambda *a, **k: _FakeResp(500, {}))
    result = gc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "ERROR"
    assert result.attempts[0]["status"] == "HTTP_500"


def test_malformed_response_body_isolated(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model-a")
    monkeypatch.setattr(gc.requests, "post", lambda *a, **k: _FakeResp(200, {"no_choices_key": True}))
    result = gc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "ERROR"
    assert result.attempts[0]["status"] == "MALFORMED_RESPONSE_BODY"


def test_invalid_json_content_isolated(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model-a")
    body = {"choices": [{"message": {"content": "not json at all, sorry"}}]}
    monkeypatch.setattr(gc.requests, "post", lambda *a, **k: _FakeResp(200, body))
    result = gc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "ERROR"
    assert result.attempts[0]["status"] == "INVALID_JSON_CONTENT"


def test_falls_back_to_second_model_after_primary_fails(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/primary")
    monkeypatch.setenv("GROQ_FALLBACK_MODELS", "test/fallback")
    calls = []

    def _post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])
        if json["model"] == "test/primary":
            return _FakeResp(500, {})
        body = {"choices": [{"message": {"content": '{"regime": "TREND", "confidence": 80}'}}]}
        return _FakeResp(200, body)
    monkeypatch.setattr(gc.requests, "post", _post)
    result = gc.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "OK"
    assert result.model_used == "test/fallback"
    assert calls == ["test/primary", "test/fallback"]


def test_api_key_never_appears_in_result_or_attempts(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-super-secret-value-xyz")
    monkeypatch.setenv("GROQ_MODEL", "test/model-a")
    monkeypatch.setattr(gc.requests, "post", lambda *a, **k: _FakeResp(500, {}))
    result = gc.chat_completion_json([{"role": "user", "content": "x"}])
    dumped = str(result.to_dict())
    assert "gsk-super-secret-value-xyz" not in dumped


# ---------------------------------------------------------------- smoke test

def test_smoke_test_skipped_when_no_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    out = gc.run_smoke_test()
    assert out["status"] == "SKIPPED"
    assert out["reason"] == "API_KEY_NOT_CONFIGURED"
    assert out["retry_count"] == 0 and out["http_status"] is None


def test_smoke_test_skipped_when_key_but_no_model(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    out = gc.run_smoke_test()
    assert out["status"] == "SKIPPED"
    assert out["reason"] == "MODEL_NOT_CONFIGURED"


def test_smoke_test_runs_real_call_path_when_configured(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model-a")
    body = {"choices": [{"message": {"content": '{"ping": "pong"}'}}]}
    monkeypatch.setattr(gc.requests, "post", lambda *a, **k: _FakeResp(200, body))
    out = gc.run_smoke_test()
    assert out["status"] == "OK"
    assert out["model"] == "test/model-a"
    assert out["latency_ms"] is not None
    assert out["retry_count"] == 0
    assert out["http_status"] is None


def test_smoke_test_reports_retry_count_and_http_status_on_failure(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model-a")
    monkeypatch.setattr(gc.requests, "post", lambda *a, **k: _FakeResp(503, {}))
    out = gc.run_smoke_test()
    assert out["status"] == "ERROR"
    assert out["http_status"] == 503
    assert out["retry_count"] >= 0
