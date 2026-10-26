"""Arena Terminal tab backend: read-only chart endpoints behind the arena token."""
import hashlib, json, os
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.arena import api as A, terminal as TM


@pytest.fixture()
def client(tmp_path, monkeypatch):
    pf = tmp_path / "participants.json"; pf.write_text(json.dumps({"TERM_TEST": {"display": "t", "token_sha256": hashlib.sha256(b"tok-term").hexdigest(), "hidden": True}}))
    monkeypatch.setattr(A, "PARTICIPANTS_FILE", str(pf)); app = FastAPI(); app.include_router(A.router); return TestClient(app)


H = {"X-Arena-Token": "tok-term"}


def test_chart_endpoints_need_the_token(client):
    assert client.get("/api/arena/public/chart?underlying=NATURALGAS&expiry=2026-10-23&strike=250").status_code == 401
    assert client.get("/api/arena/public/chart/underlying?underlying=NIFTY", headers={"X-Arena-Token": "wrong"}).status_code == 401


def test_chart_rejects_bad_parameters(client):
    for q in ("underlying=FOO&expiry=2026-10-23&strike=250", "underlying=NATURALGAS&expiry=not-a-date&strike=250", "underlying=NATURALGAS&expiry=2026-10-23&strike=250&option_type=XX", "underlying=NATURALGAS&expiry=2026-10-23&strike=250&tf=7"):
        assert client.get("/api/arena/public/chart?" + q, headers=H).status_code == 422
    assert client.get("/api/arena/public/chart/underlying?underlying=NIFTY&tf=3", headers=H).status_code == 422


def test_premium_candles_are_ohlc_consistent_and_ist_shifted(client):
    r = client.get("/api/arena/public/chart?underlying=NATURALGAS&expiry=2026-10-23&strike=250&option_type=CE&tf=5", headers=H)
    assert r.status_code == 200; j = r.json()
    for c in j["candles"]: assert c["low"] <= min(c["open"], c["close"]) and c["high"] >= max(c["open"], c["close"]) and c["time"] % 300 == TM.IST_S % 300


def test_underlying_view_has_indicator_series_and_consistent_markers(client):
    r = client.get("/api/arena/public/chart/underlying?underlying=NIFTY&tf=15", headers=H); assert r.status_code == 200; j = r.json()
    if not j["candles"]: pytest.skip("no NIFTY candles captured")
    times = {c["time"] for c in j["candles"]}
    assert all(m["time"] in times and m["side"] in ("BUY", "SELL") for m in j["markers"]) and all(s["time"] in times for s in j["stop"])
    assert j["stop"] and set(j.keys()) >= {"candles", "stop", "ma", "markers", "spot", "source"}


def test_terminal_module_is_read_only():
    src = open(TM.__file__, encoding="utf-8").read()
    assert "INSERT" not in src.upper() and "UPDATE " not in src.upper() and "execution" not in src and "angelone_orders" not in src
