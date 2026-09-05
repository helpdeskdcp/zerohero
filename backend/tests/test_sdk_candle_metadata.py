"""Regression coverage for the AngelOne SDK candle fast-path.

Covers the audit fixes:
  * the SDK candle path must return the SAME staleness / market-hours metadata
    schema as the legacy path (so the orchestrator's DATA_VALID / DATA_FRESH
    gate stops firing "data timestamp missing" on every SDK read);
  * the SDK path must stay fail-closed on stale candles (never upgrade a stale
    read to data_status == "OK");
  * TP calibration must not resolve predictions against a stale connector read;
  * /api/run must run the blocking pipeline off the event loop;
  * an unexpected /api/run exception is logged with a traceback but the client
    only ever sees a coarse, non-sensitive error class;
  * "market instruments unavailable" is an explicit state, never fabricated;
  * concurrent _market_sdk() callers cannot corrupt the shared session.
"""
import asyncio
import json
import logging
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))  # repo root — for the `broker` package

from app.connectors import angelone


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


class _FakeSDK:
    """Minimal stand-in for broker.angelone.AngelOneClient.get_candles()."""

    def __init__(self, last_dt, n=60):
        step = timedelta(minutes=5)
        self._candles = []
        for i in range(n):
            t = last_dt - step * (n - 1 - i)
            px = 100 + i * 0.05
            self._candles.append({
                "timestamp": _iso(t), "open": px, "high": px + 0.2,
                "low": px - 0.2, "close": px + 0.1, "volume": 1000 + i,
            })

    def get_candles(self, exchange, token, interval, from_date, to_date):
        return {"status": "OK", "data_status": "OK", "candles": self._candles}


@pytest.fixture(autouse=True)
def _reset_sdk_singleton():
    saved = angelone._sdk_client
    angelone._sdk_client = None
    yield
    angelone._sdk_client = saved


# ---------------------------------------------------------------- Test 1
def test_sdk_candle_path_returns_full_metadata_contract(monkeypatch):
    fresh = datetime.now(timezone.utc) - timedelta(minutes=2)
    monkeypatch.setattr(angelone, "_market_sdk", lambda *a, **k: _FakeSDK(fresh))
    out = angelone.fetch_candles(market="NSE", symbol="NIFTY", exchange="NSE",
                                 symboltoken="99926000", interval=None, fromdate=None,
                                 todate=None, timeframe="5m", instrument="INDEX")
    assert out["source"] == "ANGELONE_SDK"
    for key in ("stale_seconds", "data_age_seconds", "market_open",
                "market_status", "fetched_at", "data_timestamp", "snapshot_id"):
        assert key in out, f"SDK candle response missing {key}"
    assert out["candles"] and out["candle_count"] == len(out["candles"])
    assert out["stale_seconds"] == out["data_age_seconds"]


# ---------------------------------------------------------------- Test 2 + Test 4
def test_sdk_fresh_candles_are_ok_and_do_not_trip_freshness_gate(monkeypatch, fresh_db):
    from app import orchestrator
    fresh = datetime.now(timezone.utc) - timedelta(minutes=1)
    monkeypatch.setattr(angelone, "_market_sdk", lambda *a, **k: _FakeSDK(fresh))
    res = orchestrator.run_pipeline({
        "market": "NSE", "symbol": "NIFTY", "instrument": "INDEX", "timeframe": "5m",
        "exchange": "NSE", "symboltoken": "99926000",
        "account": {"capital": 200000, "risk_pct": 1}})
    c = res["contract"]
    assert res["connector"]["data_status"] == "OK"
    assert c["data_status"] == "OK"
    # the original bug: this gate fired on every SDK read
    assert "GATE: data timestamp missing" not in c["reason"]
    assert "data stale" not in c["reason"]
    assert c["data_age_seconds"] is not None and c["data_age_seconds"] >= 0


# ---------------------------------------------------------------- Test 3
def test_sdk_stale_candles_are_marked_stale_at_connector(monkeypatch):
    stale = datetime.now(timezone.utc) - timedelta(hours=3)
    monkeypatch.setattr(angelone, "_market_sdk", lambda *a, **k: _FakeSDK(stale))
    out = angelone.fetch_candles(market="NSE", symbol="NIFTY", exchange="NSE",
                                 symboltoken="99926000", interval=None, fromdate=None,
                                 todate=None, timeframe="5m")
    assert out["source"] == "ANGELONE_SDK"
    assert out["data_status"] == "STALE"
    assert out["stale_seconds"] > 900


def test_sdk_stale_candles_fail_closed_in_pipeline(monkeypatch, fresh_db):
    from app import orchestrator
    stale = datetime.now(timezone.utc) - timedelta(hours=3)
    monkeypatch.setattr(angelone, "_market_sdk", lambda *a, **k: _FakeSDK(stale))
    res = orchestrator.run_pipeline({
        "market": "NSE", "symbol": "NIFTY", "instrument": "INDEX", "timeframe": "5m",
        "exchange": "NSE", "symboltoken": "99926000",
        "account": {"capital": 200000, "risk_pct": 1}})
    c = res["contract"]
    assert c["data_status"] == "STALE"
    assert c["final_decision"] == "NO_TRADE"
    assert "data stale" in c["reason"]


# ---------------------------------------------------------------- Test 5
def test_tp_calibration_does_not_resolve_against_stale_forward_candles(fresh_db):
    from app import tp_calibration
    db = fresh_db
    old_ts = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with db.db() as conn:
        conn.execute(
            "INSERT INTO tp_predictions (ts,symbol,timeframe,direction,turn,raw,p_up,confidence,"
            "close_at_pred,atr_at_pred,horizon_bars,next_hi_lo,next_hi_hi,next_lo_lo,next_lo_hi,"
            "expected_move_pts,feature_scores) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (old_ts, "NIFTY", "5m", "UP_TURN", 0.6, 0.6, 0.7, 70,
             100.0, 1.0, 6, 101.0, 102.0, 98.0, 99.0, 1.5, "{}"))

    calls = {"stale": 0}

    def stale_fetch(**kw):
        calls["stale"] += 1
        base = datetime.now(timezone.utc)
        return {"data_status": "STALE", "candles": [
            {"t": _iso(base - timedelta(minutes=5 * (8 - i))), "o": 105, "h": 106,
             "l": 104, "c": 105.5, "v": 1} for i in range(9)]}

    resolved = tp_calibration.resolve_pending(stale_fetch)
    assert calls["stale"] >= 1                       # it did fetch
    assert resolved == 0                             # but scored nothing
    with db.db() as conn:
        row = conn.execute("SELECT resolved, outcome FROM tp_predictions").fetchone()
    assert row["resolved"] == 0 and row["outcome"] is None

    # positive control: a fresh OK read does resolve the same row
    def ok_fetch(**kw):
        base = datetime.now(timezone.utc)
        return {"data_status": "OK", "candles": [
            {"t": _iso(base - timedelta(minutes=5 * (8 - i))), "o": 100 + i, "h": 101 + i,
             "l": 99 + i, "c": 100.5 + i, "v": 10} for i in range(9)]}

    resolved2 = tp_calibration.resolve_pending(ok_fetch)
    assert resolved2 == 1
    with db.db() as conn:
        row = conn.execute("SELECT resolved FROM tp_predictions").fetchone()
    assert row["resolved"] == 1


# ---------------------------------------------------------------- Test 6
def test_api_run_offloads_pipeline_off_the_event_loop(monkeypatch):
    from app import main
    from app.api import engines_routes
    main_thread = threading.get_ident()
    seen = {}

    def fake_pipeline(payload):
        seen["thread"] = threading.get_ident()
        return {"contract": {"decision": "NO_TRADE"}, "trade": None}

    # api_run_pipeline lives in app.api.engines_routes and calls its own
    # module-local `run_pipeline` name -- patch it there, not on app.main.
    monkeypatch.setattr(engines_routes, "run_pipeline", fake_pipeline)
    resp = asyncio.run(main.api_run_pipeline(main.SignalRequest(market="NSE", symbol="NIFTY")))
    assert "thread" in seen and seen["thread"] != main_thread
    assert resp["contract"]["decision"] == "NO_TRADE"


# ---------------------------------------------------------------- Test 7
def test_api_run_unexpected_exception_is_logged_and_sanitised(monkeypatch, caplog):
    from app import main
    from app.api import engines_routes
    secret = "BrokerJWT-eyJhbGciOi-SUPERSECRET"

    def boom(payload):
        raise RuntimeError(f"connection to broker failed token={secret}")

    monkeypatch.setattr(engines_routes, "run_pipeline", boom)
    with caplog.at_level(logging.ERROR, logger="chanakya.api"):
        resp = asyncio.run(main.api_run_pipeline(main.SignalRequest(market="NSE", symbol="NIFTY")))

    # client response: fail-closed + coarse class, no secret / traceback leak
    assert resp["error"] == "DATA_UNAVAILABLE"
    assert resp["error_class"] == "INTERNAL_ERROR"
    assert resp["contract"]["final_decision"] == "NO_TRADE"
    assert resp["contract"]["data_status"] == "DATA_UNAVAILABLE"
    blob = json.dumps(resp)
    assert secret not in blob and "Traceback" not in blob

    # server log: the full traceback IS captured
    recs = [r for r in caplog.records if r.name == "chanakya.api" and r.levelno >= logging.ERROR]
    assert recs and recs[0].exc_info is not None
    assert secret in caplog.text


# ---------------------------------------------------------------- Test 8
def test_market_instruments_unavailable_is_explicit_not_fabricated(monkeypatch):
    from app import main
    monkeypatch.setattr(angelone, "_market_sdk", lambda *a, **k: None)
    out = main.api_market_instruments(market="NSE")
    assert out["data_status"] == "DATA_UNAVAILABLE"
    assert out["instruments"] == []


def test_run_form_surfaces_unavailable_state_and_readonly_market_fields():
    root = Path(__file__).parents[2]
    js = (root / "frontend" / "static" / "js" / "app.js").read_text()
    html = (root / "frontend" / "index.html").read_text()
    assert "market instruments unavailable" in js.lower()
    for name in ('name="spot"', 'name="atm"', 'name="underlying"'):
        seg = html[html.index(name):html.index(name) + 140]
        assert "readonly" in seg, f"{name} must stay read-only"


# ---------------------------------------------------------------- Test 9
def test_market_sdk_concurrent_access_is_consistent(monkeypatch):
    import broker.angelone as bpkg

    ctor_calls = {"n": 0}
    RealClient = bpkg.AngelOneClient

    class CountingClient(RealClient):
        def __init__(self, *a, **k):
            ctor_calls["n"] += 1
            super().__init__(*a, **k)

    monkeypatch.setattr(bpkg, "AngelOneClient", CountingClient)

    angelone._sdk_client = None
    now = time.time()
    monkeypatch.setattr(angelone, "_session_cache",
                        {"jwt": "JWT-1", "feed_token": "FT-1", "ts": now})
    monkeypatch.setattr(angelone, "_get_jwt", lambda: ("OK", "JWT-1", ""))

    errors = []

    def worker():
        try:
            for _ in range(25):
                assert angelone._market_sdk() is not None
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=worker) for _ in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert ctor_calls["n"] == 1                       # the singleton is built exactly once
    c = angelone._sdk_client
    assert c.jwt == "JWT-1" and c.feed_token == "FT-1" and c.login_ts == now
