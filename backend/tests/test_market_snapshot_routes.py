"""app.api.market_snapshot_routes -- calls route handlers directly (same
pattern as test_hedging_routes.py), never a real broker or network fetch."""
from app.api import market_snapshot_routes as R


def test_snapshot_route_defaults_to_offline(monkeypatch):
    seen = {}
    monkeypatch.setattr(R._snap, "build_snapshot",
                        lambda underlying, allow_network=True: seen.setdefault("net", allow_network) or
                        R._snap.AtomicSnapshot(snapshot_id="x", underlying=underlying, taken_at="t",
                                              spot=1.0, spot_source="s", chain_rows=0, chain_expiry=None,
                                              chain_source=None, chain_quality=None, india_vix=None))
    out = R.api_market_snapshot("NIFTY")
    assert seen["net"] is False
    assert out["underlying"] == "NIFTY" and out["snapshot_id"] == "x"


def test_vix_route_unavailable_when_no_quote(monkeypatch):
    monkeypatch.setattr(R._snap, "_latest_index_quote", lambda symbol: None)
    out = R.api_market_vix()
    assert out["status"] == "UNAVAILABLE"


def test_vix_route_ok_when_quote_present(monkeypatch):
    monkeypatch.setattr(R._snap, "_latest_index_quote",
                        lambda symbol: {"ltp": 13.5, "age_sec": 5.0, "source": "ANGELONE_QUOTE_FULL"})
    out = R.api_market_vix()
    assert out["status"] == "OK" and out["ltp"] == 13.5


def test_data_conflict_route_passes_through_tolerance(monkeypatch):
    seen = {}
    monkeypatch.setattr(R, "_check_conflict",
                        lambda underlying, tolerance_pct: seen.update(
                            underlying=underlying, tolerance_pct=tolerance_pct) or {"status": "OK"})
    out = R.api_market_data_conflict("NIFTY", tolerance_pct=0.25)
    assert out == {"status": "OK"}
    assert seen == {"underlying": "NIFTY", "tolerance_pct": 0.25}
