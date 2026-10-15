import pytest

from app.ai import ai_client as C
from app.ai.groq_client import AIResult


def res(status, codes=(), err=None):
    return AIResult(status=status, data={"x": 1} if status == "OK" else None, model_used="m" if status == "OK" else None,
                    latency_ms=1 if status == "OK" else None, error=err, attempts=[{"model": "m", "status": c} for c in codes])


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    C._reset_breaker_for_tests()
    monkeypatch.setattr(C._openai, "is_available", lambda: True)
    yield
    C._reset_breaker_for_tests()


def test_auth_failure_stops_the_hammering_for_an_hour_and_rate_limit_backs_off(monkeypatch):
    calls = {"g": 0, "o": 0}

    def groq(**k):
        calls["g"] += 1
        return res("ERROR", ["HTTP_429", "HTTP_429"], "all attempts failed")

    def openai(**k):
        calls["o"] += 1
        return res("ERROR", ["HTTP_401"], "rejected")

    monkeypatch.setattr(C._groq, "chat_completion_json", groq)
    monkeypatch.setattr(C._openai, "chat_completion_json", openai)
    r1 = C.chat_completion_json(messages=[])
    assert r1.status == "ERROR" and calls == {"g": 1, "o": 1}
    for _ in range(5):                                                  # inside the cooldown: no HTTP at all, same ERROR contract
        r = C.chat_completion_json(messages=[])
        assert r.status == "ERROR" and r.attempts[0]["status"] == "COOLDOWN"
    assert calls == {"g": 1, "o": 1}
    st = C.breaker_status()
    assert st["openai"]["reason"] == "AUTH_REJECTED" and st["openai"]["cooldown_left_sec"] > 3000
    assert st["groq"]["reason"] == "RATE_LIMITED" and 0 < st["groq"]["cooldown_left_sec"] <= 60


def test_rate_limit_cooldown_doubles_and_caps(monkeypatch):
    monkeypatch.setattr(C._openai, "is_available", lambda: False)
    monkeypatch.setattr(C._groq, "chat_completion_json", lambda **k: res("ERROR", ["HTTP_429"]))
    seen = []
    for _ in range(7):
        C._BREAKER["groq"]["until"] = 0.0                                # pretend the previous cooldown elapsed
        C.chat_completion_json(messages=[])
        seen.append(round(C.breaker_status()["groq"]["cooldown_left_sec"]))
    assert seen[:4] == [60, 120, 240, 480] and max(seen) <= 900


def test_transient_errors_do_not_trip_and_success_resets(monkeypatch):
    monkeypatch.setattr(C._openai, "is_available", lambda: False)
    monkeypatch.setattr(C._groq, "chat_completion_json", lambda **k: res("ERROR", ["TIMEOUT"]))
    C.chat_completion_json(messages=[])
    assert C.breaker_status()["groq"]["cooldown_left_sec"] == 0
    monkeypatch.setattr(C._groq, "chat_completion_json", lambda **k: res("ERROR", ["HTTP_429"]))
    C.chat_completion_json(messages=[])
    assert C.breaker_status()["groq"]["consecutive_failures"] == 1
    C._BREAKER["groq"]["until"] = 0.0
    monkeypatch.setattr(C._groq, "chat_completion_json", lambda **k: res("OK", ["HTTP_200"]))
    assert C.chat_completion_json(messages=[]).status == "OK"
    assert C.breaker_status()["groq"] == {"cooldown_left_sec": 0.0, "consecutive_failures": 0, "reason": None}
