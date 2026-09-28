"""app.api.dhan_routes -- the Dhan postback receiver. Dhan's postback has NO
signature/secret of its own (confirmed against their v2 API docs), so this
is a pure logging endpoint gated by a long random path secret, never wired
to any trading decision. Uses TestClient against a tiny throwaway app
wrapping the real route + real _AuthGateMiddleware, matching
test_main_auth.py's pattern -- never boots the full real app."""
import asyncio

import pytest
from starlette.testclient import TestClient

from app import db, main
from app.api import dhan_routes


def _client(monkeypatch, secret="real-secret-value"):
    monkeypatch.setattr(dhan_routes, "POSTBACK_SECRET", secret)
    monkeypatch.setattr(main, "ADMIN_USERNAME", "opuser")
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "strongpw")
    monkeypatch.setattr(main, "API_TOKEN", "")
    from fastapi import FastAPI
    inner = FastAPI()
    inner.include_router(dhan_routes.router)
    wrapped = main._AuthGateMiddleware(inner)
    return TestClient(wrapped)


def test_is_auth_exempt_path_matches_only_the_two_dhan_facing_shapes():
    assert dhan_routes.is_auth_exempt_path("/api/dhan/postback/anything-here") is True
    assert dhan_routes.is_auth_exempt_path("/api/dhan/redirect") is True
    assert dhan_routes.is_auth_exempt_path("/api/dhan/postbacks") is False
    assert dhan_routes.is_auth_exempt_path("/api/dhan/postback") is False  # no trailing segment
    assert dhan_routes.is_auth_exempt_path("/api/dhan/connection-status") is False
    assert dhan_routes.is_auth_exempt_path("/api/hedging/status") is False


def test_correct_secret_is_logged_and_needs_no_basic_auth(fresh_db, monkeypatch):
    client = _client(monkeypatch)
    payload = {"dhanClientId": "1234", "orderId": "abcd", "orderStatus": "TRADED"}
    resp = client.post("/api/dhan/postback/real-secret-value", json=payload)
    assert resp.status_code == 200
    assert resp.json()["received"] is True
    rows = db.list_dhan_postbacks()
    assert len(rows) == 1
    assert rows[0]["order_id"] == "abcd" and rows[0]["order_status"] == "TRADED"


def test_wrong_secret_is_rejected_and_nothing_is_logged(fresh_db, monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/api/dhan/postback/wrong-guess", json={"orderId": "should-not-be-stored"})
    assert resp.status_code == 401
    assert db.list_dhan_postbacks() == []


def test_unconfigured_secret_fails_closed(fresh_db, monkeypatch):
    client = _client(monkeypatch, secret="")
    resp = client.post("/api/dhan/postback/anything", json={"orderId": "x"})
    assert resp.status_code == 401
    assert db.list_dhan_postbacks() == []


def test_malformed_body_does_not_crash_the_endpoint(fresh_db, monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/api/dhan/postback/real-secret-value", content=b"not json at all",
                       headers={"Content-Type": "application/json"})
    assert resp.status_code == 200
    rows = db.list_dhan_postbacks()
    assert len(rows) == 1  # still logged, just marked unparseable, never crashes


def test_non_dict_json_body_is_wrapped_not_rejected(fresh_db, monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/api/dhan/postback/real-secret-value", json=["unexpected", "list", "body"])
    assert resp.status_code == 200
    rows = db.list_dhan_postbacks()
    assert "_raw_body" in rows[0]["raw_json"]


def test_postbacks_listing_route_requires_normal_auth(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/api/dhan/postbacks")
    assert resp.status_code == 401  # no Basic/Bearer given -- normal auth gate applies


def test_postbacks_listing_route_works_with_correct_auth(fresh_db, monkeypatch):
    client = _client(monkeypatch)
    client.post("/api/dhan/postback/real-secret-value", json={"orderId": "x", "orderStatus": "PENDING"})
    resp = client.get("/api/dhan/postbacks", auth=("opuser", "strongpw"))
    assert resp.status_code == 200
    assert resp.json()["rows"][0]["order_status"] == "PENDING"


# ---------------------------------------------------------------------------
# Partner OAuth flow (generate-consent / redirect / connection-status).
# dhan_client's actual Dhan API calls are stubbed -- never hits the real
# auth.dhan.co in tests.
# ---------------------------------------------------------------------------
from app import dhan_client  # noqa: E402


def test_redirect_needs_no_basic_auth_but_requires_a_tokenid(fresh_db, monkeypatch):
    monkeypatch.setattr(dhan_client, "consume_consent",
                        lambda token_id: {"status": "OK", "dhan_client_id": "1000000001",
                                          "dhan_client_name": "JOHN DOE", "expiry_time": "2026-10-01T00:00:00"})
    client = _client(monkeypatch)
    resp = client.get("/api/dhan/redirect", params={"tokenId": "real-token-from-dhan"})
    assert resp.status_code == 200
    assert "JOHN DOE" in resp.text


def test_redirect_without_tokenid_is_a_clean_400_not_a_crash(fresh_db, monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/api/dhan/redirect")
    assert resp.status_code == 400


def test_redirect_surfaces_a_failed_consent_exchange(fresh_db, monkeypatch):
    monkeypatch.setattr(dhan_client, "consume_consent",
                        lambda token_id: {"status": "ERROR", "http_status": 400, "detail": "expired tokenId"})
    client = _client(monkeypatch)
    resp = client.get("/api/dhan/redirect", params={"tokenId": "expired-token"})
    assert resp.status_code == 502


def test_generate_consent_requires_normal_auth(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/api/dhan/generate-consent")
    assert resp.status_code == 401


def test_generate_consent_fails_closed_when_not_configured(monkeypatch):
    monkeypatch.delenv("DHAN_PARTNER_ID", raising=False)
    monkeypatch.delenv("DHAN_PARTNER_SECRET", raising=False)
    client = _client(monkeypatch)
    resp = client.post("/api/dhan/generate-consent", auth=("opuser", "strongpw"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "NOT_CONFIGURED"


def test_connection_status_reports_disconnected_by_default(fresh_db, monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/api/dhan/connection-status", auth=("opuser", "strongpw"))
    assert resp.status_code == 200
    assert resp.json()["connected"] is False


def test_connection_status_never_leaks_the_raw_access_token(fresh_db, monkeypatch):
    import json as _json
    db.set_setting("dhan_access_token", _json.dumps({
        "dhanClientId": "1000000001", "dhanClientName": "JOHN DOE",
        "accessToken": "super-secret-real-token", "expiryTime": "2026-10-01T00:00:00"}))
    client = _client(monkeypatch)
    resp = client.get("/api/dhan/connection-status", auth=("opuser", "strongpw"))
    assert resp.status_code == 200
    assert "super-secret-real-token" not in resp.text
    assert resp.json()["connected"] is True and resp.json()["dhan_client_name"] == "JOHN DOE"
