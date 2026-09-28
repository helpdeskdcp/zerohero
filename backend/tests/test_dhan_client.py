"""app.dhan_client -- Dhan Partner OAuth consent flow. Every call to Dhan's
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


def test_generate_consent_not_configured_without_partner_credentials(monkeypatch):
    monkeypatch.delenv("DHAN_PARTNER_ID", raising=False)
    monkeypatch.delenv("DHAN_PARTNER_SECRET", raising=False)
    out = dhan_client.generate_consent()
    assert out["status"] == "NOT_CONFIGURED"


def test_generate_consent_sends_real_partner_headers_and_returns_login_url(monkeypatch):
    monkeypatch.setenv("DHAN_PARTNER_ID", "pid-123")
    monkeypatch.setenv("DHAN_PARTNER_SECRET", "psecret-456")
    captured = {}

    def fake_post(url, headers=None, timeout=None):
        captured["url"], captured["headers"] = url, headers
        return _FakeResponse(200, {"consentId": "abc-123", "consentStatus": "GENERATED"})

    monkeypatch.setattr(dhan_client.requests, "post", fake_post)
    out = dhan_client.generate_consent()
    assert out["status"] == "OK"
    assert out["consent_id"] == "abc-123"
    assert out["login_url"] == "https://auth.dhan.co/consent-login?consentId=abc-123"
    assert captured["url"] == "https://auth.dhan.co/partner/generate-consent"
    assert captured["headers"] == {"partner_id": "pid-123", "partner_secret": "psecret-456"}


def test_generate_consent_surfaces_a_non_200_as_error(monkeypatch):
    monkeypatch.setenv("DHAN_PARTNER_ID", "pid")
    monkeypatch.setenv("DHAN_PARTNER_SECRET", "sec")
    monkeypatch.setattr(dhan_client.requests, "post",
                        lambda url, headers=None, timeout=None: _FakeResponse(401, {"error": "bad partner_secret"}))
    out = dhan_client.generate_consent()
    assert out["status"] == "ERROR" and out["http_status"] == 401


def test_consume_consent_stores_the_access_token_and_returns_a_redacted_summary(fresh_db, monkeypatch):
    monkeypatch.setenv("DHAN_PARTNER_ID", "pid")
    monkeypatch.setenv("DHAN_PARTNER_SECRET", "sec")
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


def test_consume_consent_not_configured_without_partner_credentials(monkeypatch):
    monkeypatch.delenv("DHAN_PARTNER_ID", raising=False)
    monkeypatch.delenv("DHAN_PARTNER_SECRET", raising=False)
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
