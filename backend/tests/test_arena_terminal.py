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


def _mini_db(path, rows):
    import sqlite3
    c = sqlite3.connect(path); c.execute("CREATE TABLE quote_snapshots (id INTEGER PRIMARY KEY, received_ts TEXT, exch_ts TEXT, symbol TEXT, expiry TEXT, strike REAL, option_type TEXT, kind TEXT, source TEXT, ltp REAL, bid REAL, ask REAL)")
    c.executemany("INSERT INTO quote_snapshots (received_ts, exch_ts, symbol, expiry, strike, option_type, kind, source, ltp, bid, ask) VALUES (?,?,?,?,?,?,?,?,?,?,?)", [r + (r[-1] - 0.1, r[-1] + 0.1) for r in rows]); c.commit(); c.close()


def test_illiquid_contract_chart_is_continuous_heartbeat_rows_included_and_long_silence_stays_a_gap(tmp_path):
    import datetime as dt
    day = dt.datetime(2026, 10, 26, 3, 30, tzinfo=dt.timezone.utc)                                          # 09:00 IST
    iso = lambda m: (day + dt.timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ"); R = lambda m, px, ex=0: (iso(m), iso(ex), "NATURALGAS", "20NOV2026", 240.0, "CE", "OPTION", "ANGELONE_QUOTE_FULL", px)
    rows = [R(0, 50.0), R(1, 50.0), R(2, 50.0, 0), R(3, 50.0, 0), R(4, 51.0, 4), R(5, 51.0, 4), R(6, 51.0, 4),                  # unchanged quotes keep an OLD exchange timestamp (heartbeat rows)
            R(40, 52.0, 40)]                                                                                                    # then 34 minutes of silence
    p = str(tmp_path / "mh.db"); _mini_db(p, rows); now = (day + dt.timedelta(minutes=41)).timestamp()
    out = TM.premium("NATURALGAS", "2026-11-20", 240.0, "CE", tf=1, now=now, path=p)
    times = [c["time"] - TM.IST_S for c in out["candles"]]; mins = [round((t - day.timestamp()) / 60) for t in times]
    assert mins[:7] == [0, 1, 2, 3, 4, 5, 6] and mins[-1] == 40 and not any(7 <= m < 40 for m in mins)                        # continuous while quotes stand (even with old exchange stamps); a 34 min silence is NOT filled
    assert out["candles"][4]["close"] == 51.0 and out["candles"][0]["open"] == 50.0
    inc = TM.premium("NATURALGAS", "2026-11-20", 240.0, "CE", tf=1, since=day.timestamp() + 5 * 60, now=now, path=p)          # incremental refresh also sees heartbeat rows
    assert [round((c["time"] - TM.IST_S - day.timestamp()) / 60) for c in inc["candles"]][:2] == [4, 5]
