import os, time
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.nifty_direction import api as NA, constituents as C, store, adapters as A

CSV = ("Company Name,Industry,Symbol,Series,ISIN Code\n" + "".join(f"Co{i},{'Fin' if i%2 else 'IT'},S{i},EQ,INE{i:03d}\n" for i in range(50))).encode()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ROOT", str(tmp_path)); monkeypatch.setattr(store, "DB", str(tmp_path / "n.db")); monkeypatch.delenv("ENABLE_LIVE_ORDERS", raising=False); monkeypatch.delenv("NIFTY_DIR_ADAPTER", raising=False)
    app = FastAPI(); app.include_router(NA.router); return TestClient(app)


def test_status_without_master_and_blockers(client, tmp_path):
    r = client.get("/api/nifty-direction/status").json(); assert r["paper_only"] is True and r["live_orders_enabled"] is False and r["master"] is None
    C.save(C.build_master(CSV, None), str(tmp_path / "master.json"))
    r = client.get("/api/nifty-direction/status").json(); assert r["master"]["weights_status"] == "MISSING" and "weights" in r["blockers"][0]


def test_overview_needs_master_then_reports_no_adapter(client, tmp_path):
    assert client.get("/api/nifty-direction/overview").status_code == 503
    C.save(C.build_master(CSV, None), str(tmp_path / "master.json"))
    o = client.get("/api/nifty-direction/overview").json(); assert o["status"] == "NO_ADAPTER_CONFIGURED" and o["data_kind"] == "NONE"
    assert client.get("/api/nifty-direction/overview?mode=replay").status_code == 400


def test_overview_with_mock_is_labelled_mock(client, tmp_path, monkeypatch):
    C.save(C.build_master(CSV, None), str(tmp_path / "master.json")); monkeypatch.setenv("NIFTY_DIR_ADAPTER", "mock")
    o = client.get("/api/nifty-direction/overview").json(); assert o["data_kind"] == "MOCK" and o["score"]["actionable"] is False and len(o["heatmap"]) == 50


def test_read_routes_and_config(client):
    for p in ("signals", "health-events", "paper"): assert client.get(f"/api/nifty-direction/{p}").json()["rows"] == []
    cfg = client.get("/api/nifty-direction/config").json(); assert cfg["score"]["w_contribution"] == 30 and cfg["paper"]["qty"] == 1 and cfg["strategy"]["ema_fast"] == 9


def test_no_write_routes_except_refresh_and_no_order_routes():
    routes = {(m, r.path) for r in NA.router.routes for m in r.methods}
    assert {x for x in routes if x[0] != "GET"} == {("POST", "/api/nifty-direction/refresh-master")}
    assert not [r for _, r in routes if any(k in r for k in ("order", "place", "buy", "sell"))]


def test_refresh_master_uses_cached_csv_and_checks_schema(client, tmp_path, monkeypatch):
    m = C.refresh(fetch=lambda u: CSV, root=str(tmp_path)); assert m["count"] == 50 and m["weights_status"] == "MISSING" and (tmp_path / "constituents.csv").exists()
    (tmp_path / "weights.csv").write_text("symbol,weight_pct\n" + "".join(f"S{i},2.0\n" for i in range(50)))
    assert C.refresh(fetch=lambda u: CSV, root=str(tmp_path))["weights_status"] == "COMPLETE"
    with pytest.raises(ValueError): C.refresh(fetch=lambda u: b"x,y\n1,2\n", root=str(tmp_path))
    assert (tmp_path / "constituents.csv").read_bytes() == CSV                          # bad download did not overwrite the cached copy
