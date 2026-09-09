"""
app/optionchain/api.py -- Layer 6 dashboard route. Offline: direct handler
calls (the repo's convention -- no httpx/TestClient), a synthetic
market_history.db, `live=0` so no network.
"""
import sqlite3

import pytest

import app.optionchain.api as OCAPI
from app.optionchain.api import api_optionchain, api_optionchain_underlyings


@pytest.fixture
def hist_db(tmp_path, monkeypatch):
    p = tmp_path / "market_history.db"
    con = sqlite3.connect(p)
    con.executescript("""
        CREATE TABLE quote_snapshots(
          id INTEGER PRIMARY KEY, symbol TEXT, kind TEXT, exchange TEXT, expiry TEXT,
          strike REAL, option_type TEXT, ltp REAL, oi REAL, oi_change REAL, volume REAL,
          bid REAL, ask REAL, bid_qty REAL, ask_qty REAL, session_date_ist TEXT, received_ts TEXT);
        CREATE TABLE option_greeks(
          id INTEGER PRIMARY KEY, received_ts TEXT, snap_key TEXT, underlying TEXT, expiry TEXT,
          strike REAL, option_type TEXT, session_date_ist TEXT, delta REAL, gamma REAL,
          theta REAL, vega REAL, iv REAL, iv_pct REAL, trade_volume REAL);
        CREATE TABLE greek_exposure(
          id INTEGER PRIMARY KEY, as_of_ts TEXT, computed_ts TEXT, underlying TEXT, expiry TEXT,
          underlying_price REAL, pcr_oi REAL, ce_oi_total REAL, pe_oi_total REAL, per_strike_json TEXT);
    """)
    exp = "15SEP2026"
    for snap, rts in (("2026-09-09T09:00:00", "2026-09-09T09:00:01Z"),
                      ("2026-09-09T09:00:40", "2026-09-09T09:00:41Z")):
        for k in range(23000, 24001, 50):
            for ot, dl in (("CE", 0.5), ("PE", -0.5)):
                con.execute("INSERT INTO option_greeks(received_ts,snap_key,underlying,expiry,strike,"
                            "option_type,session_date_ist,delta,gamma,theta,vega,iv,iv_pct,trade_volume) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (rts, snap, "NIFTY", exp, float(k), ot, "2026-09-09",
                             dl, 0.001, -3.1, 4.2, 0.12, 12.0, 4000.0))
    for i, rts in enumerate(("2026-09-09T08:59:00Z", "2026-09-09T09:00:39Z")):
        for k in range(23400, 23701, 50):
            for ot in ("CE", "PE"):
                con.execute("INSERT INTO quote_snapshots(symbol,kind,expiry,strike,option_type,ltp,oi,"
                            "oi_change,volume,bid,ask,bid_qty,ask_qty,session_date_ist,received_ts) "
                            "VALUES('NIFTY','OPTION',?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (exp, float(k), ot, 120.0 + i, 90000.0, 400.0, 7000.0,
                             119.0 + i, 121.0 + i, 5.0, 5.0, "2026-09-09", rts))
    for rts, ltp in (("2026-09-09T08:59:30Z", 23470.0), ("2026-09-09T09:00:38Z", 23485.0)):
        con.execute("INSERT INTO quote_snapshots(symbol,kind,ltp,session_date_ist,received_ts) "
                    "VALUES('NIFTY','INDEX',?,?,?)", (ltp, "2026-09-09", rts))
    con.execute("INSERT INTO greek_exposure(as_of_ts,computed_ts,underlying,expiry,underlying_price,"
                "pcr_oi,ce_oi_total,pe_oi_total,per_strike_json) VALUES("
                "'2026-09-09T08:30:00+00:00','2026-09-09T08:30:05Z','NIFTY',?,23450.0,0.8,9e7,7e7,?)",
                (exp, '[{"strike":23000.0,"ce":{"oi":700000.0},"pe":{"oi":800000.0}},'
                      ' {"strike":24000.0,"ce":{"oi":640000.0},"pe":{"oi":120000.0}}]'))
    con.commit()
    con.close()
    monkeypatch.setenv("CHANAKYA_HIST_DB_PATH", str(p))
    OCAPI._cache.clear()
    yield str(p)
    OCAPI._cache.clear()


def test_underlyings_list():
    r = api_optionchain_underlyings()
    assert r["default"] == "NIFTY" and "NIFTY" in r["underlyings"]
    assert "AUTO" in r["expiries"]


def test_route_returns_the_five_sections(hist_db):
    j = api_optionchain("NIFTY", live=0, atm_window=8, realized_vol=0.11)
    assert j["status"] == "OK" and j["source"] == "angelone_captured"
    assert j["expiry"] == "15SEP2026" and j["spot"] == 23485.0
    for key in ("chain", "analytics", "structure", "quality", "qualification", "capability"):
        assert key in j, f"missing section {key}"
    assert len(j["chain"]["rows"]) == 21
    assert set(j["analytics"]) >= {"pcr", "max_pain", "iv_skew", "oi_walls", "gex"}
    assert j["structure"]["capability"]["structure_blocks_ok"] >= 4
    assert j["qualification"]["verdict"] in ("QUALIFIED", "WATCH", "NO_TRADE")
    assert "not wired to any order path" in " ".join(j["qualification"]["notes"])
    import json
    json.dumps(j)                                   # must serialise for the HTTP layer


def test_route_is_cached_then_bypassed_for_baseline(hist_db):
    a = api_optionchain("NIFTY", live=0)
    b = api_optionchain("NIFTY", live=0)
    assert a is b                                    # same cached object
    c = api_optionchain("NIFTY", live=0, baseline="2026-09-09T09:00:20Z")
    assert c is not a and "oi_baseline" in c
    assert c["oi_baseline"]["status"] in ("ok", "no_baseline", "no_overlap")


def test_unknown_underlying_is_no_data_not_500(hist_db):
    j = api_optionchain("NOTANINDEX", live=0)
    assert j["status"] == "NO_DATA" and "note" in j


def test_bad_baseline_ts_does_not_raise(hist_db):
    j = api_optionchain("NIFTY", live=0, baseline="not-a-timestamp")
    assert j["status"] == "OK"
    assert j["oi_baseline"]["status"] in ("error", "no_baseline", "no_overlap", "ok")


def test_atm_window_is_clamped(hist_db):
    j = api_optionchain("NIFTY", live=0, atm_window=9999)
    assert j["status"] == "OK"                       # clamped, no slice/þrow


def test_router_is_mounted_on_the_app():
    from app.main import app
    paths = {r.path for r in app.routes}
    assert "/api/optionchain/{underlying}" in paths
    assert "/api/optionchain/underlyings" in paths
