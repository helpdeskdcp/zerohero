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
    monkeypatch.delenv("UPSTOX_ANALYTICS_TOKEN", raising=False)


def _router(rules):
    """rules: list of (substr_in_url, _Resp). Returns a fake _http."""
    def fake(method, url, **k):
        for sub, resp in rules:
            if sub in url:
                return resp
        return _Resp(200, {"status": "success", "data": []})
    return fake


# ---------------------------------------------------------------- auth_health
def test_health_no_token(monkeypatch):
    monkeypatch.setattr(U, "_http", lambda *a, **k: _Resp(200))
    h = U.auth_health()
    assert h["credentials_configured"] is True
    assert h["token_kind"] == "none"
    assert h["access_token_present"] is False
    assert h["access_token_valid"] is None
    assert "test-secret-value" not in str(h) and "test-key-uuid" not in str(h)


def test_health_analytics_token_all_read_apis_ok_expired_plus_gated(monkeypatch):
    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "analytics-token-xyz")
    monkeypatch.setattr(U, "_http", _router([
        ("/v2/expired-instruments/expiries", _Resp(401, {"errors": [{"errorCode": "UDAPI1149"}]})),
        ("/v2/historical-candle/", _Resp(200, {"status": "success", "data": {"candles": []}})),
        ("/v2/option/chain", _Resp(200, {"status": "success", "data": []})),
        ("/v2/market/holidays", _Resp(200, {"status": "success", "data": []})),
    ]))
    h = U.auth_health()
    assert h["token_kind"] == "analytics"
    assert h["access_token_valid"] is True
    assert h["historical_data_api"] == "OK"
    assert h["option_chain_api"] == "OK"
    assert h["market_information_api"] == "OK"
    assert "Upstox Plus subscription required" in h["expired_instruments_api"]
    assert h["note"] == "Upstox Plus subscription required"
    assert "analytics-token-xyz" not in str(h)


def test_health_expired_token_invalid(monkeypatch):
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "stale")
    monkeypatch.setattr(U, "_http", _router([
        ("/v2/historical-candle/", _Resp(401, {"errors": [{"errorCode": "UDAPI100050"}]})),
    ]))
    h = U.auth_health()
    assert h["token_kind"] == "oauth"
    assert h["access_token_valid"] is False
    assert "regenerate" in h["note"].lower() or "invalid" in h["note"].lower()


def test_health_static_ip_gate_on_historical(monkeypatch):
    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "tok")
    monkeypatch.setattr(U, "_http", _router([
        ("/v2/historical-candle/", _Resp(401, {"errors": [{"errorCode": "UDAPI1221"}]})),
    ]))
    h = U.auth_health()
    assert "static-IP" in h["historical_data_api"]


def test_analytics_token_is_preferred_over_oauth(monkeypatch):
    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "ANALYTICS")
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "OAUTH")
    c = U._creds()
    assert c["access_token"] == "ANALYTICS" and c["token_kind"] == "analytics"
    monkeypatch.delenv("UPSTOX_ANALYTICS_TOKEN")
    assert U._creds()["access_token"] == "OAUTH" and U._creds()["token_kind"] == "oauth"


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
    with pytest.raises(ValueError):
        U.get_historical_candles("k", "7minute", "2024-01-01", "2024-01-01")
    assert set(U.INTERVALS) == {"1minute", "3minute", "5minute", "15minute", "30minute", "day"}


def test_readonly_market_wrappers_build_paths(monkeypatch):
    calls = {}

    def fake(method, url, **k):
        calls.setdefault("urls", []).append(url)
        calls["params"] = k.get("params", {})
        return _Resp(200, {"status": "success", "data": []})

    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "tok")
    monkeypatch.setattr(U, "_http", fake)

    U.get_historical_candles("NSE_INDEX|Nifty 50", "1minute", "2024-08-30", "2024-08-29")
    assert "/v2/historical-candle/" in calls["urls"][-1] and "/1minute/2024-08-30/2024-08-29" in calls["urls"][-1]
    U.get_option_chain("NSE_INDEX|Nifty 50", "2026-09-30")
    assert calls["urls"][-1].endswith("/v2/option/chain") and calls["params"]["expiry_date"] == "2026-09-30"
    U.get_market_holidays()
    assert calls["urls"][-1].endswith("/v2/market/holidays")
    U.get_ltp("NSE_INDEX|Nifty 50")
    assert calls["urls"][-1].endswith("/v2/market-quote/ltp")


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
