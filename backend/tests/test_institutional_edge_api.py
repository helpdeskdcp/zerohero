"""
app/institutional_edge/api.py -- read-only routes. Direct handler calls
(this repo's convention for FastAPI route testing).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.institutional_edge import api, evaluator  # noqa: E402
from app.institutional_edge.store import EdgeStore  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    s = EdgeStore(db_path=str(tmp_path / "ie_api_test.db"))
    monkeypatch.setattr(evaluator, "store", lambda: s)
    monkeypatch.setattr(api, "store", lambda: s)
    evaluator._machines.clear()
    yield s
    evaluator._machines.clear()


def _seed(db, n, *, win_rate=0.6, symbol="NATURALGAS", regime="TRENDING_UP"):
    for i in range(n):
        is_win = (i % 10) < round(win_rate * 10)
        db.insert_scalp_signal({
            "signal_id": f"sig-{symbol}-{i}", "source": "LIVE", "status": "CLOSED",
            "symbol": symbol, "regime": regime, "probability": 0.6,
            "outcome": "WIN" if is_win else "LOSS", "points": 10.0 if is_win else -8.0,
            "created_ts": f"2026-09-01T00:{i % 60:02d}:00Z", "resolved": 1,
        })


def test_status_starts_empty_before_any_evaluation():
    assert api.status() == {"tracked": []}


def test_status_instrument_evaluates_and_registers_the_scope(fresh_db):
    _seed(fresh_db, 60)
    out = api.status_instrument("NATURALGAS", condition="regime==TRENDING_UP")
    assert out["instrument"] == "NATURALGAS"
    tracked = api.status()["tracked"]
    assert len(tracked) == 1 and tracked[0]["condition_label"] == "regime==TRENDING_UP"


def test_conditions_endpoint_lists_the_registry():
    out = api.conditions_list()
    assert "regime==TRENDING_UP" in out["conditions"]
    assert "pcr>1.2" in out["conditions"]


def test_costs_endpoint_lists_only_validated_profiles():
    out = api.costs_known()
    keys = {(p["exchange"], p["segment"]) for p in out["known_profiles"]}
    assert keys == {("MCX", "NATURALGAS_OPTION"), ("MCX", "CRUDEOIL_OPTION")}


def test_history_reads_from_the_store(fresh_db):
    _seed(fresh_db, 60)
    api.status_instrument("NATURALGAS", condition="regime==TRENDING_UP")
    out = api.history(instrument="NATURALGAS", condition="regime==TRENDING_UP", limit=200)
    assert len(out["evaluations"]) == 1
    assert out["evaluations"][0]["instrument"] == "NATURALGAS"


def test_status_instrument_never_touches_execution_or_broker_state(fresh_db):
    _seed(fresh_db, 60)
    out = api.status_instrument("NATURALGAS", condition="regime==TRENDING_UP")
    forbidden_keys = {"order_id", "broker", "execution", "credentials"}
    assert forbidden_keys.isdisjoint(out.keys())
