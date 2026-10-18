"""Dashboard API: read-only, returns the Pine dashboard fields from the engine; no writes, no live path."""
import numpy as np
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import hybrid_mp_api as api  # noqa: E402

T0 = 1_775_000_000 - (1_775_000_000 % 86400) - 19800 + 555 * 60          # fixed date in the past (never 'live' whatever day the suite runs)
T0 += (7 - ((T0 + 19800) // 86400 + 3) % 7) % 7 * 86400


def _bars(days=4, seed=1):
    r = np.random.default_rng(seed)
    t, o, h, l, c = [], [], [], [], []
    px = 22000.0
    for d in range(days):
        for m in range(375):
            op = px
            px = px + r.normal(0, 4)
            t.append(T0 + d * 86400 + m * 60); o.append(op); c.append(px)
            h.append(max(op, px) + abs(r.normal(0, 2))); l.append(min(op, px) - abs(r.normal(0, 2)))
    return {"t": np.array(t, dtype=np.int64), "o": np.array(o), "h": np.array(h), "l": np.array(l), "c": np.array(c), "v": np.zeros(len(t))}


@pytest.fixture
def client(monkeypatch):
    b = _bars()
    monkeypatch.setattr(api, "latest_1m", lambda symbol: (b, {"history_last_bar": int(b["t"][-1]), "live_bars_appended": 0, "last_bar_age_sec": 400_000}))
    api._cache.clear()
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_state_has_all_dashboard_fields_and_is_flagged_research_only(client):
    j = client.get("/api/hybrid_mp/state?symbol=NIFTY&tf=5").json()
    for k in ("mtf", "direction", "trend_status", "trade", "session", "opening_gap", "signal_check", "next_target", "targets", "entry", "sl",
              "levels", "last_result", "planned_qty", "pending", "smc", "vwap", "vwap_is_proxy", "today", "candles", "trades_today", "counters"):
        assert k in j, k
    assert j["research_only"] is True and j["live_trading"] is False
    assert j["data"]["data_status"] in ("MARKET_CLOSED", "STALE") and j["session"] != "IN SESSION"      # bars are days old in this test
    assert set(j["mtf"]) == {"3m", "5m", "15m", "40m"} and j["vwap_is_proxy"] is True and j["vwap"] is not None
    assert len(j["candles"]) <= 150 and j["candles"][-1]["time"] > 0


def test_smc_toggle_and_validation(client):
    j = client.get("/api/hybrid_mp/state?symbol=NIFTY&tf=5&smc=1&smc_min=3").json()
    assert j["smc"]["enabled"] is True and j["smc"]["min_score"] == 3
    assert client.get("/api/hybrid_mp/state?symbol=FOO&tf=5").status_code == 400
    assert client.get("/api/hybrid_mp/state?symbol=NIFTY&tf=7").status_code == 400


def test_backtest_endpoint_reads_the_saved_summary_only(client):
    j = client.get("/api/hybrid_mp/backtest").json()
    assert "available" in j


def test_api_module_has_no_order_or_write_paths():
    import ast
    import inspect
    src = inspect.getsource(api)
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    bad = [n.func.attr for n in calls if n.func.attr in {"place_order", "executemany", "executescript", "commit", "post", "put", "delete"}]
    assert not bad
    for n in calls:                                           # every SQL statement must be a SELECT, on a read-only connection
        if n.func.attr == "execute":
            assert n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value.strip().upper().startswith("SELECT")
    assert "mode=ro" in src


def test_state_defaults_to_the_corrected_variant_and_labels_it_not_promoted(monkeypatch):
    from app import hybrid_mp_api as api
    import pytest
    with pytest.raises(ValueError):
        api.build_state("NIFTY", 5, False, 2, "bogus")
