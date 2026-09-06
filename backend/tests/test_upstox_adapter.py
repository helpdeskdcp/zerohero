"""
Upstox connector -- offline tests. No network, no secrets in output.

Verifies: auth_health() reports booleans only for every state; login_url()
carries client_id + redirect but never the secret; the Authorization header is
redacted in logs; the module hardcodes no credentials; the Expired Instruments
wrappers build the documented paths.
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.connectors import upstox as U   # noqa: E402


class _Resp:
    def __init__(self, status=200, js=None, text=""):
        self.status_code = status
        self._js = js if js is not None else {}
        self.text = text
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._js


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("UPSTOX_API_KEY", "test-key-uuid")
    monkeypatch.setenv("UPSTOX_API_SECRET", "test-secret-value")
    monkeypatch.setenv("UPSTOX_REDIRECT_URI", "http://127.0.0.1:7060/api/upstox/callback")
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)


# ---------------------------------------------------------------- auth_health
def test_health_no_token(monkeypatch):
    monkeypatch.setattr(U, "_http", lambda *a, **k: _Resp(200))
    h = U.auth_health()
    assert h["credentials_configured"] is True
    assert h["access_token_present"] is False
    assert h["access_token_valid"] is None            # can't check without a token
    assert "test-secret-value" not in str(h) and "test-key-uuid" not in str(h)


def test_health_valid_token(monkeypatch):
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "daily-token-xyz")
    monkeypatch.setattr(U, "_http", lambda method, url, **k: _Resp(200, {"data": ["2024-08-29"]}))
    h = U.auth_health()
    assert h["access_token_present"] is True
    assert h["access_token_valid"] is True
    assert h["api_reachable"] is True
    assert h.get("expired_instruments_api") == "OK"
    assert "daily-token-xyz" not in str(h)


def test_health_expired_token(monkeypatch):
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "stale")
    monkeypatch.setattr(U, "_http", lambda method, url, **k:
                        _Resp(401, {"status": "error", "errors": [{"errorCode": "UDAPI100050",
                                                                   "message": "Invalid token"}]}))
    h = U.auth_health()
    assert h["access_token_present"] is True
    assert h["access_token_valid"] is False
    assert "UDAPI100050" in h["note"] and "fresh token" in h["note"]


def test_health_plus_plan_gate(monkeypatch):
    """Valid token, but the Expired Instruments API needs an Upstox Plus plan."""
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "analytics-token")
    monkeypatch.setattr(U, "_http", lambda method, url, **k:
                        _Resp(401, {"status": "error", "errors": [{"errorCode": "UDAPI1149",
                                                                   "message": "Plus plan required"}]}))
    h = U.auth_health()
    assert h["access_token_valid"] is True                 # the token is fine
    assert "Plus" in h["expired_instruments_api"] and "UDAPI1149" in h["expired_instruments_api"]


def test_health_static_ip_gate(monkeypatch):
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(U, "_http", lambda method, url, **k:
                        _Resp(401, {"errors": [{"errorCode": "UDAPI1221", "message": "static IP"}]}))
    h = U.auth_health()
    assert h["access_token_valid"] is True
    assert "static-IP" in h["expired_instruments_api"]


def test_health_not_configured(monkeypatch):
    monkeypatch.delenv("UPSTOX_API_KEY", raising=False)
    monkeypatch.delenv("UPSTOX_API_SECRET", raising=False)
    h = U.auth_health()
    assert h["credentials_configured"] is False
    assert h["access_token_valid"] is None


# ---------------------------------------------------------------- OAuth url / redaction
def test_login_url_has_client_id_not_secret():
    url = U.login_url()
    assert "client_id=test-key-uuid" in url
    assert "redirect_uri=" in url
    assert "response_type=code" in url
    assert "test-secret-value" not in url            # secret must never appear in the URL


def test_redact_masks_authorization():
    r = U._redact({"Authorization": "Bearer supersecrettoken", "Accept": "application/json"})
    assert r["Authorization"] == "Bearer ***redacted***"
    assert "supersecrettoken" not in str(r)
    assert r["Accept"] == "application/json"


def test_authed_get_requires_token(monkeypatch):
    monkeypatch.setattr(U, "_http", lambda *a, **k: _Resp(200))
    with pytest.raises(RuntimeError) as e:
        U._authed_get("/v2/user/profile")
    assert "OAuth" in str(e.value)
    assert "test-secret" not in str(e.value)


# ---------------------------------------------------------------- expired-instruments wrappers
def test_expired_wrappers_build_documented_paths(monkeypatch):
    calls = {}

    def fake_http(method, url, **k):
        calls["method"] = method
        calls["url"] = url
        calls["headers"] = k.get("headers", {})
        calls["params"] = k.get("params", {})
        return _Resp(200, {"data": []})

    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(U, "_http", fake_http)

    U.get_expiries("NSE_INDEX|Nifty 50")
    assert calls["url"].endswith("/v2/expired-instruments/expiries")
    assert calls["params"]["instrument_key"] == "NSE_INDEX|Nifty 50"

    U.get_expired_option_contracts("NSE_INDEX|Nifty 50", "2024-08-29")
    assert calls["url"].endswith("/v2/expired-instruments/option/contract")
    assert calls["params"]["expiry_date"] == "2024-08-29"

    U.get_expired_historical_candles("NSE_FO|12345|29-08-2024", "1minute", "2024-08-29", "2024-08-01")
    assert "/v2/expired-instruments/historical-candle/" in calls["url"]
    assert "/1minute/2024-08-29/2024-08-01" in calls["url"]
    assert calls["headers"]["Authorization"].startswith("Bearer ")


def test_interval_guard():
    with pytest.raises(ValueError):
        U.get_expired_historical_candles("k", "7minute", "2024-01-01", "2024-01-01")
    assert set(U.INTERVALS) == {"1minute", "3minute", "5minute", "15minute", "30minute", "day"}


# ---------------------------------------------------------------- no hardcoded secrets
def test_module_source_has_no_hardcoded_credentials():
    src = Path(U.__file__).read_text()
    assert "6d9950a0" not in src            # the real API key must not be in source
    assert "2esybva20i" not in src          # the real secret must not be in source
    for tok in ("api_secret =", "access_token =", "client_secret="):
        # only allowed as dict keys / body fields, never as an assigned literal
        assert f'{tok} "' not in src and f"{tok} '" not in src


def test_underlying_keys_pilot_set():
    assert U.UNDERLYING_KEYS["NIFTY"] == "NSE_INDEX|Nifty 50"
    assert U.UNDERLYING_KEYS["BANKNIFTY"] == "NSE_INDEX|Nifty Bank"
