"""app.dhan_client -- Dhan App-Based OAuth consent flow. Every call to Dhan's
real auth.dhan.co is stubbed (requests.post monkeypatched) -- never hits the
real network in tests."""
import json

from app import dhan_client, db


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


def test_generate_consent_not_configured_without_app_credentials(monkeypatch):
    monkeypatch.delenv("DHAN_APP_ID", raising=False)
    monkeypatch.delenv("DHAN_APP_SECRET", raising=False)
    monkeypatch.delenv("DHAN_CLIENT_ID", raising=False)
    out = dhan_client.generate_consent()
    assert out["status"] == "NOT_CONFIGURED"


def test_generate_consent_not_configured_without_client_id(monkeypatch):
    monkeypatch.setenv("DHAN_APP_ID", "aid-123")
    monkeypatch.setenv("DHAN_APP_SECRET", "asecret-456")
    monkeypatch.delenv("DHAN_CLIENT_ID", raising=False)
    out = dhan_client.generate_consent()
    assert out["status"] == "NOT_CONFIGURED"


def test_generate_consent_sends_real_app_headers_and_client_id_and_returns_login_url(monkeypatch):
    monkeypatch.setenv("DHAN_APP_ID", "aid-123")
    monkeypatch.setenv("DHAN_APP_SECRET", "asecret-456")
    monkeypatch.setenv("DHAN_CLIENT_ID", "1000000001")
    captured = {}

    def fake_post(url, headers=None, timeout=None):
        captured["url"], captured["headers"] = url, headers
        return _FakeResponse(200, {"consentAppId": "capp-123", "consentAppStatus": "GENERATED", "status": "success"})

    monkeypatch.setattr(dhan_client.requests, "post", fake_post)
    out = dhan_client.generate_consent()
    assert out["status"] == "OK"
    assert out["consent_app_id"] == "capp-123"
    assert out["login_url"] == "https://auth.dhan.co/login/consentApp-login?consentAppId=capp-123"
    assert captured["url"] == "https://auth.dhan.co/app/generate-consent?client_id=1000000001"
    assert captured["headers"] == {"app_id": "aid-123", "app_secret": "asecret-456"}


def test_generate_consent_surfaces_a_non_200_as_error(monkeypatch):
    monkeypatch.setenv("DHAN_APP_ID", "aid")
    monkeypatch.setenv("DHAN_APP_SECRET", "sec")
    monkeypatch.setenv("DHAN_CLIENT_ID", "1000000001")
    monkeypatch.setattr(dhan_client.requests, "post",
                        lambda url, headers=None, timeout=None: _FakeResponse(401, {"error": "bad app_secret"}))
    out = dhan_client.generate_consent()
    assert out["status"] == "ERROR" and out["http_status"] == 401


def test_consume_consent_stores_the_access_token_and_returns_a_redacted_summary(fresh_db, monkeypatch):
    monkeypatch.setenv("DHAN_APP_ID", "aid")
    monkeypatch.setenv("DHAN_APP_SECRET", "sec")
    real_body = {"dhanClientId": "1000000001", "dhanClientName": "JOHN DOE",
                "dhanClientUcc": "CEFE4265", "givenPowerOfAttorney": True,
                "accessToken": "the-real-secret-token", "expiryTime": "2026-10-01T00:00:00"}
    monkeypatch.setattr(dhan_client.requests, "post",
                        lambda url, headers=None, timeout=None: _FakeResponse(200, real_body))

    out = dhan_client.consume_consent("real-token-id")
    assert out["status"] == "OK"
    assert out["dhan_client_id"] == "1000000001"
    assert "accessToken" not in out and "the-real-secret-token" not in json.dumps(out)

    # but it WAS stored, for internal use only
    assert dhan_client.get_access_token() == "the-real-secret-token"
    status = dhan_client.connection_status()
    assert status["connected"] is True
    assert "the-real-secret-token" not in json.dumps(status)


def test_consume_consent_not_configured_without_app_credentials(monkeypatch):
    monkeypatch.delenv("DHAN_APP_ID", raising=False)
    monkeypatch.delenv("DHAN_APP_SECRET", raising=False)
    out = dhan_client.consume_consent("some-token")
    assert out["status"] == "NOT_CONFIGURED"


def test_get_access_token_is_none_when_never_connected(fresh_db):
    assert dhan_client.get_access_token() is None


def test_connection_status_disconnected_when_never_connected(fresh_db):
    assert dhan_client.connection_status() == {"connected": False}


def test_connection_status_handles_a_corrupt_stored_record(fresh_db):
    db.set_setting("dhan_access_token", "{not valid json")
    status = dhan_client.connection_status()
    assert status["connected"] is False
