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
          bid REAL, ask REAL, bid_qty REAL, ask_qty REAL, session_date_ist TEXT, received_ts TEXT, source TEXT DEFAULT 'ANGELONE_QUOTE_FULL');
        CREATE TABLE option_greeks(
          id INTEGER PRIMARY KEY, received_ts TEXT, snap_key TEXT, underlying TEXT, expiry TEXT,
          strike REAL, option_type TEXT, session_date_ist TEXT, delta REAL, gamma REAL,
          theta REAL, vega REAL, iv REAL, iv_pct REAL, trade_volume REAL, source TEXT DEFAULT 'ANGELONE_OPTION_GREEK');
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
    # MCX + BSE are exposed alongside the NSE indices, grouped by exchange
    assert {"NSE", "BSE", "MCX"} <= set(r["groups"])
    assert "CRUDEOIL" in r["groups"]["MCX"] and "NATURALGAS" in r["groups"]["MCX"]
    assert "SENSEX" in r["groups"]["BSE"]
    assert set(r["underlyings"]) == {s for g in r["groups"].values() for s in g}


def test_api_historical_provider_is_strict_and_disables_live_resolution(monkeypatch):
    from app.optionchain.sources import dhan_chain
    def forbidden(*a, **kw):
        raise AssertionError("an as-of request cannot resolve a live expiry list")
    monkeypatch.setattr(dhan_chain, "resolve_expiry", forbidden)
    seen = []
    monkeypatch.setattr(OCAPI, "get_chain", lambda u, e, **kw: seen.append((e, kw)))
    OCAPI._cache.clear()
    api_optionchain("NIFTY", expiry="08SEP2026", provider="ANGELONE",
                    as_of="2026-09-02T04:30:00Z", live=1)
    assert seen[0][0] == "08SEP2026"
    assert seen[0][1]["provider"] == "ANGELONE" and not seen[0][1]["allow_network"]
    OCAPI._cache.clear()


@pytest.mark.parametrize("expiry", ["AUTO", "NEXT"])
def test_historical_api_reports_calendar_unavailable_without_live_lookup(hist_db, monkeypatch, expiry):
    """Captured contracts and today's broker cannot certify a historical alias."""
    from app.optionchain.sources import dhan_chain
    calls = []
    def forbidden(*a, **kw):
        calls.append(True)
        raise AssertionError("no live calendar lookup")
    monkeypatch.setattr(dhan_chain, "fetch", forbidden)
    monkeypatch.setattr(dhan_chain, "resolve_expiry", forbidden)
    result = api_optionchain("NIFTY", expiry=expiry, live=1, as_of="2026-09-09T09:00:41Z")
    assert result["status"] == "NO_DATA"
    assert result["reason"] == "CALENDAR_EVIDENCE_UNAVAILABLE"
    assert calls == []


@pytest.mark.parametrize("symbol,dates", [("NIFTY", ["2031-01-07", "2031-01-14"]),
                                        ("SENSEX", ["2031-01-02", "2031-01-09"]),
                                        ("CRUDEOIL", ["2031-01-15", "2031-02-17"])])
@pytest.mark.parametrize("expiry,index", [("AUTO", 0), ("NEXT", 1), ("2031-01-23", None)])
def test_route_resolves_dhan_expiry_without_angel_master(monkeypatch, symbol, dates, expiry, index):
    from app.optionchain.sources import dhan_chain
    from app import instruments
    monkeypatch.setattr(dhan_chain, "expiry_list", lambda *a, **kw: dates)
    def forbidden(*a, **kw):
        pytest.fail("Dhan expiry must not depend on AngelOne instrument master")
    monkeypatch.setattr(instruments, "list_nse_option_expiries", forbidden)
    seen = []
    monkeypatch.setattr(OCAPI, "get_chain", lambda u, e, **kw: seen.append(e))
    OCAPI._cache.clear()
    api_optionchain(symbol, expiry=expiry, live=1)
    assert seen == [dhan_chain._to_canonical_expiry(dates[index]) if index is not None else expiry]
    OCAPI._cache.clear()


def test_route_returns_the_five_sections(hist_db):
    j = api_optionchain("NIFTY", expiry="15SEP2026", live=0, atm_window=8, realized_vol=0.11)
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
    a = api_optionchain("NIFTY", expiry="15SEP2026", live=0)
    b = api_optionchain("NIFTY", expiry="15SEP2026", live=0)
    assert a is b                                    # same cached object
    c = api_optionchain("NIFTY", expiry="15SEP2026", live=0, baseline="2026-09-09T09:00:20Z")
    assert c is not a and "oi_baseline" in c and "oi_buildup" in c
    assert c["oi_baseline"]["status"] in ("ok", "no_baseline", "no_overlap")
    assert c["oi_buildup"]["status"] in ("ok", "no_baseline", "no_spot")


def test_route_computes_oi_buildup_regime_from_two_snapshots(hist_db):
    # OI Build-Up regime existed in analytics.py (oi_buildup_regime) but was
    # never called from this route -- found during a frontend/backend data
    # audit. Wired alongside the existing oi_baseline block, reusing the
    # same fetched baseline chain (no extra DB read).
    j = api_optionchain("NIFTY", expiry="15SEP2026", live=0, baseline="2026-09-09T08:59:31Z")
    assert j["status"] == "OK"
    bu = j["oi_buildup"]
    assert bu["status"] == "ok"
    assert bu["price_direction"] == "UP"                      # spot 23470.0 -> 23485.0
    # the fixture's OI is identical at both captured timestamps (90000.0 for
    # every strike/side) -- a real net-zero OI change must classify as FLAT,
    # never a fabricated buildup/unwind label.
    assert bu["ce_regime"] == "FLAT" and bu["pe_regime"] == "FLAT"


def test_unknown_underlying_is_no_data_not_500(hist_db):
    j = api_optionchain("NOTANINDEX", live=0)
    assert j["status"] == "NO_DATA" and "note" in j


def test_bad_baseline_ts_does_not_raise(hist_db):
    j = api_optionchain("NIFTY", expiry="15SEP2026", live=0, baseline="not-a-timestamp")
    assert j["status"] == "OK"
    assert j["oi_baseline"]["status"] in ("error", "no_baseline", "no_overlap", "ok")
    assert j["oi_buildup"]["status"] in ("error", "no_baseline", "no_spot", "ok")


def test_atm_window_is_clamped(hist_db):
    j = api_optionchain("NIFTY", expiry="15SEP2026", live=0, atm_window=9999)
    assert j["status"] == "OK"                       # clamped, no slice/þrow


def _all_route_paths(routes):
    """Recursively collect .path from every route, descending into
    include_router() wrappers (Starlette >=1.0 wraps an included APIRouter
    in an _IncludedRouter with .original_router.routes instead of exposing
    .path directly on app.routes)."""
    paths = set()
    for r in routes:
        p = getattr(r, "path", None)
        if p is not None:
            paths.add(p)
        sub = getattr(getattr(r, "original_router", None), "routes", None) or getattr(r, "routes", None)
        if sub:
            paths |= _all_route_paths(sub)
    return paths


def test_router_is_mounted_on_the_app():
    from app.main import app
    paths = _all_route_paths(app.routes)
    assert "/api/optionchain/{underlying}" in paths
    assert "/api/optionchain/underlyings" in paths
