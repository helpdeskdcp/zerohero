"""app.ai.ai_client -- the Groq-primary/OpenAI-fallback orchestrator that
app.ai.behavior_ai and app.ai.shadow import as `_client`. No real network
call is ever made.

NOTE: app.ai.groq_client and app.ai.openai_client both `import requests` --
that's the SAME module object in sys.modules, so monkeypatching
`gc.requests.post` and `oc.requests.post` separately just overwrites the
same attribute twice (whichever patch runs last wins for BOTH modules).
Every test here instead patches `requests.post` exactly once with a single
router keyed on the request URL (gc.BASE_URL vs oc.BASE_URL)."""
import json

import requests

from app.ai import ai_client as ac
from app.ai import groq_client as gc
from app.ai import openai_client as oc


class _FakeResp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


def _ok_body(**over):
    d = {"regime": "TREND", "confidence": 70}
    d.update(over)
    return {"choices": [{"message": {"content": json.dumps(d)}}]}


def _router(monkeypatch, *, groq_resp=None, openai_resp=None, openai_calls=None):
    def _post(url, headers=None, json=None, timeout=None):
        if url.startswith(gc.BASE_URL):
            return groq_resp
        if url.startswith(oc.BASE_URL):
            if openai_calls is not None:
                openai_calls.append(1)
            return openai_resp
        raise AssertionError(f"unexpected URL in test: {url}")
    monkeypatch.setattr(requests, "post", _post)


def test_unavailable_when_neither_provider_configured(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert ac.is_available() is False
    assert ac.config_status() == "CONFIG_REQUIRED"
    result = ac.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "CONFIG_REQUIRED"


def test_groq_success_never_calls_openai(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "groq/model")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-fake")
    openai_calls = []
    _router(monkeypatch, groq_resp=_FakeResp(200, _ok_body()),
           openai_resp=_FakeResp(200, _ok_body()), openai_calls=openai_calls)

    result = ac.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "OK"
    assert result.model_used == "groq/model"
    assert openai_calls == []   # OpenAI was never touched


def test_groq_exhausted_falls_back_to_openai(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "groq/model")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-fake")
    # Simulates the real production symptom: account-level 429 on every model.
    _router(monkeypatch, groq_resp=_FakeResp(429, {}),
           openai_resp=_FakeResp(200, _ok_body(confidence=55)))

    result = ac.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "OK"
    assert result.model_used == "gpt-fake"
    assert result.data["confidence"] == 55
    # Both providers' attempts are visible for diagnostics.
    assert any(a["status"] == "HTTP_429" for a in result.attempts)
    assert any(a["model"] == "gpt-fake" and a["status"] == "OK" for a in result.attempts)


def test_both_providers_fail_returns_error_with_both_reasons(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "groq/model")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-fake")
    _router(monkeypatch, groq_resp=_FakeResp(429, {}), openai_resp=_FakeResp(500, {}))

    result = ac.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "ERROR"
    assert "groq:" in result.error and "openai:" in result.error


def test_groq_unconfigured_openai_configured_uses_openai_directly(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-fake")
    _router(monkeypatch, groq_resp=_FakeResp(200, _ok_body()), openai_resp=_FakeResp(200, _ok_body()))

    assert ac.is_available() is True
    result = ac.chat_completion_json([{"role": "user", "content": "x"}])
    assert result.status == "OK"
    assert result.model_used == "gpt-fake"


def test_diagnostics_reports_active_provider_and_never_exposes_keys(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-super-secret")
    monkeypatch.setenv("GROQ_MODEL", "groq/model")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-fake")
    d = ac.diagnostics()
    assert d["active_provider"] == "groq"
    assert "gsk-super-secret" not in str(d) and "sk-super-secret" not in str(d)


def test_diagnostics_active_provider_is_openai_when_groq_unconfigured(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-fake")
    d = ac.diagnostics()
    assert d["active_provider"] == "openai"
