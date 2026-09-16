"""Live-wiring spec section 18: /api/sr/status observability endpoint --
read-only, never recomputes, never crashes on unknown symbols."""
from app.sr_dynamic import api as sr_api
from app.sr_dynamic import live_state


def test_status_all_returns_empty_map_when_nothing_computed_yet(monkeypatch):
    monkeypatch.setattr(live_state, "_LATEST", {})
    r = sr_api.api_sr_status_all()
    assert r == {"symbols": {}}


def test_status_symbol_reports_no_data_gracefully(monkeypatch):
    monkeypatch.setattr(live_state, "_LATEST", {})
    r = sr_api.api_sr_status_symbol("NATURALGAS")
    assert r["status"] == "no_data"
    assert r["symbol"] == "NATURALGAS"


def test_status_symbol_returns_the_stored_state(monkeypatch):
    monkeypatch.setattr(live_state, "_LATEST", {"NATURALGAS": {"symbol": "NATURALGAS", "spot": 285.0}})
    r = sr_api.api_sr_status_symbol("naturalgas")
    assert r["spot"] == 285.0


def test_status_all_reflects_the_registry(monkeypatch):
    monkeypatch.setattr(live_state, "_LATEST", {"NIFTY": {"symbol": "NIFTY", "spot": 23500.0}})
    r = sr_api.api_sr_status_all()
    assert r["symbols"]["NIFTY"]["spot"] == 23500.0
