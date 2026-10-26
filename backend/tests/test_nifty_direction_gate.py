"""The real app + the real auth gate (no bypass): nifty-direction routes are mounted on app.main and sit behind the same 401 gate as everything else.
A throwaway token is set on the module global for the test only; production credentials are never read."""
import pytest
from fastapi.testclient import TestClient

from app import main as M
from app.nifty_direction import constituents as C, store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "API_TOKEN", "test-token-only"); monkeypatch.setattr(C, "ROOT", str(tmp_path)); monkeypatch.setattr(store, "DB", str(tmp_path / "n.db"))
    monkeypatch.delenv("ENABLE_LIVE_ORDERS", raising=False); monkeypatch.delenv("NIFTY_DIR_ADAPTER", raising=False)
    return TestClient(M.app)


def test_routes_are_mounted_on_the_main_app():
    paths = set(M.app.openapi()["paths"])
    assert {"/api/nifty-direction/status", "/api/nifty-direction/overview", "/api/nifty-direction/refresh-master"} <= paths


@pytest.mark.parametrize("method,path", [("get", "/api/nifty-direction/status"), ("get", "/api/nifty-direction/overview"), ("get", "/api/nifty-direction/signals"), ("post", "/api/nifty-direction/refresh-master")])
def test_gate_blocks_without_credentials(client, method, path):
    r = getattr(client, method)(path); assert r.status_code == 401
    r = getattr(client, method)(path, headers={"Authorization": "Bearer wrong"}); assert r.status_code == 401


def test_gate_allows_valid_token_and_routes_work(client):
    h = {"Authorization": "Bearer test-token-only"}
    s = client.get("/api/nifty-direction/status", headers=h); assert s.status_code == 200 and s.json()["live_orders_enabled"] is False and s.json()["paper_only"] is True
    assert client.get("/api/nifty-direction/overview", headers=h).status_code == 503          # no master built in the temp dir -> explicit, not a fake result
    assert client.get("/api/nifty-direction/signals", headers=h).json() == {"rows": []}
    assert client.get("/api/nifty-direction/paper", headers=h).json()["paper_only"] is True
