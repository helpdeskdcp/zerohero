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


def test_is_postback_path_matches_only_the_webhook_shape():
    assert dhan_routes.is_postback_path("/api/dhan/postback/anything-here") is True
    assert dhan_routes.is_postback_path("/api/dhan/postbacks") is False
    assert dhan_routes.is_postback_path("/api/dhan/postback") is False  # no trailing segment
    assert dhan_routes.is_postback_path("/api/hedging/status") is False


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
