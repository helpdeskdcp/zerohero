"""
app/optionchain/ DATA LAYER -- offline, no network, no live app import.

Covers:
  * the canonical dataclasses (roundtrip / window / ATM / leg lookup)
  * angelone_chain.fetch against a synthetic market_history.db
  * upstox_open.parse / nse_v3.parse on canned JSON (pure, no HTTP)
  * quality.score bounds + verdict
  * resolve.get_chain(allow_network=False) -> primary-only, deterministic
  * static: the module imports nothing from app.main / autoscalp / execution
"""
import ast
import os
import pathlib
import sqlite3

import pytest

from app.optionchain import get_chain
from app.optionchain.chain import OptionChain, StrikeRow, OptionLeg
from app.optionchain.quality import score as dq_score
from app.optionchain.sources import angelone_chain, upstox_open, nse_v3


# --------------------------------------------------------------------------- #
#  canonical shape                                                             #
# --------------------------------------------------------------------------- #
def _demo_chain(spot=None):
    rows = []
    for k in range(23000, 24001, 50):
        d = (k - 23500) / 5000.0
        rows.append(StrikeRow(
            strike=float(k),
            ce=OptionLeg(ltp=200.0 - (k - 23500) * 0.04, oi=1000.0 + k, iv=0.12, delta=0.5 - d),
            pe=OptionLeg(ltp=200.0 + (k - 23500) * 0.04, oi=2000.0 + k, iv=0.13, delta=-0.5 - d)))
    return OptionChain(underlying="NIFTY", expiry="15SEP2026", ts="2026-09-09T00:00:00Z",
                       source="unit", spot=spot, rows=rows,
                       capability={"has_greeks": True, "greek_coverage": 1.0,
                                   "has_oi": True}).sort().compute_atm()


def test_dataclass_roundtrip_and_to_dict_drops_none():
    leg = OptionLeg(ltp=12.5, oi=None)
    assert leg.to_dict() == {"ltp": 12.5}
    d = _demo_chain(spot=23480).to_dict()
    assert d["underlying"] == "NIFTY" and d["n_strikes"] == len(d["rows"]) == 21
    assert d["rows"][0]["ce"] is not None and "delta" in d["rows"][0]["ce"]


def test_atm_from_spot_and_from_parity_proxy():
    assert _demo_chain(spot=23480).atm_strike == 23500.0        # nearest strike to spot
    proxy = _demo_chain(spot=None)                              # |ce_ltp - pe_ltp| min
    assert proxy.atm_strike == 23500.0


def test_window_and_leg_lookup():
    c = _demo_chain(spot=23500)
    w = c.window(3)
    assert [r.strike for r in w] == [23350, 23400, 23450, 23500, 23550, 23600, 23650]
    assert c.leg(23500, "CE").delta == pytest.approx(0.5)
    assert c.leg(99999, "PE") is None


# --------------------------------------------------------------------------- #
#  synthetic capture DB for the PRIMARY source                                 #
# --------------------------------------------------------------------------- #
@pytest.fixture
def hist_db(tmp_path):
    p = tmp_path / "market_history.db"
    con = sqlite3.connect(p)
    con.executescript("""
        CREATE TABLE quote_snapshots(
          id INTEGER PRIMARY KEY, symbol TEXT, kind TEXT, exchange TEXT, expiry TEXT,
          strike REAL, option_type TEXT, ltp REAL, oi REAL, oi_change REAL, volume REAL,
          bid REAL, ask REAL, bid_qty REAL, ask_qty REAL, session_date_ist TEXT,
          received_ts TEXT);
        CREATE TABLE option_greeks(
          id INTEGER PRIMARY KEY, received_ts TEXT, snap_key TEXT, underlying TEXT,
          expiry TEXT, strike REAL, option_type TEXT, session_date_ist TEXT,
          delta REAL, gamma REAL, theta REAL, vega REAL, iv REAL, iv_pct REAL,
          trade_volume REAL);
        CREATE TABLE greek_exposure(
          id INTEGER PRIMARY KEY, as_of_ts TEXT, computed_ts TEXT, underlying TEXT,
          expiry TEXT, underlying_price REAL, pcr_oi REAL, ce_oi_total REAL,
          pe_oi_total REAL, per_strike_json TEXT);
    """)
    exp = "15SEP2026"
    # ---- full-chain greeks spine: two snap_keys, 23000..24000 both sides ----
    for snap, rts in (("2026-09-09T10:00:00", "2026-09-09T10:00:01Z"),
                      ("2026-09-09T10:00:40", "2026-09-09T10:00:41Z")):
        for k in range(23000, 24001, 50):
            for ot, dl in (("CE", 0.5), ("PE", -0.5)):
                con.execute(
                    "INSERT INTO option_greeks(received_ts,snap_key,underlying,expiry,"
                    "strike,option_type,session_date_ist,delta,gamma,theta,vega,iv,iv_pct,"
                    "trade_volume) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rts, snap, "NIFTY", exp, float(k), ot, "2026-09-09",
                     dl, 0.001, -3.1, 4.2, 0.12, 12.0, 5000.0))
    # ---- near-money live quotes: only 23400..23600, latest at 10:00:39 ----
    for i, rts in enumerate(("2026-09-09T09:59:00Z", "2026-09-09T10:00:39Z")):
        for k in range(23400, 23601, 50):
            for ot in ("CE", "PE"):
                con.execute(
                    "INSERT INTO quote_snapshots(symbol,kind,expiry,strike,option_type,"
                    "ltp,oi,oi_change,volume,bid,ask,bid_qty,ask_qty,session_date_ist,"
                    "received_ts) VALUES('NIFTY','OPTION',?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (exp, float(k), ot, 100.0 + i, 111000.0, 500.0, 9000.0,
                     99.0 + i, 101.0 + i, 10.0, 12.0, "2026-09-09", rts))
    # ---- index spot ----
    for rts, ltp in (("2026-09-09T09:59:30Z", 23470.0), ("2026-09-09T10:00:38Z", 23485.0)):
        con.execute("INSERT INTO quote_snapshots(symbol,kind,ltp,session_date_ist,"
                    "received_ts) VALUES('NIFTY','INDEX',?,?,?)",
                    (ltp, "2026-09-09", rts))
    # ---- wing OI from the greek engine (older -> stale) ----
    psj = '[{"strike": 23000.0, "ce": {"oi": 700000.0}, "pe": {"oi": 800000.0}},' \
          ' {"strike": 24000.0, "ce": {"oi": 640000.0}, "pe": {"oi": 120000.0}}]'
    con.execute("INSERT INTO greek_exposure(as_of_ts,computed_ts,underlying,expiry,"
                "underlying_price,pcr_oi,ce_oi_total,pe_oi_total,per_strike_json) "
                "VALUES('2026-09-09T09:30:00+00:00','2026-09-09T09:30:05Z','NIFTY',?,"
                "23450.0,0.78,9.3e7,7.2e7,?)", (exp, psj))
    con.commit()
    con.close()
    return str(p)


def test_angelone_chain_assembles_spine_plus_overlays(hist_db):
    c = angelone_chain.fetch("NIFTY", "AUTO", db_path=hist_db)
    assert c is not None
    assert c.expiry == "15SEP2026"
    assert len(c.rows) == 21                                   # full greek spine
    assert c.capability["has_greeks"] and c.capability["greek_coverage"] == 1.0
    assert c.spot == 23485.0 and c.atm_strike == 23500.0       # freshest index print

    # near-money strike: priced from the live quote overlay
    atm_ce = c.leg(23500, "CE")
    assert atm_ce.ltp == pytest.approx(101.0) and atm_ce.delta == pytest.approx(0.5)
    assert atm_ce.oi == pytest.approx(111000.0)

    # a wing strike: greeks yes, live price no, OI backfilled from greek_exposure
    wing = c.leg(23000, "CE")
    assert wing.delta == pytest.approx(0.5) and wing.ltp is None
    assert wing.oi == pytest.approx(700000.0)
    assert c.capability["oi_stale"] is True
    assert 0.0 < c.capability["ltp_coverage"] < 1.0


def test_angelone_chain_none_when_db_missing(tmp_path):
    assert angelone_chain.fetch("NIFTY", db_path=str(tmp_path / "nope.db")) is None


def test_resolve_offline_uses_primary_only(hist_db, monkeypatch):
    called = {"net": 0}
    monkeypatch.setattr(upstox_open, "fetch", lambda *a, **k: called.__setitem__("net", called["net"] + 1))
    monkeypatch.setattr(nse_v3, "fetch", lambda *a, **k: called.__setitem__("net", called["net"] + 1))
    c = get_chain("NIFTY", "AUTO", allow_network=False, db_path=hist_db)
    assert c is not None and c.source == "angelone_captured"
    assert called["net"] == 0
    assert c.quality["verdict"] in ("PASS", "WARN", "FAIL")


# --------------------------------------------------------------------------- #
#  secondary / tertiary parsers -- pure, canned JSON, no HTTP                   #
# --------------------------------------------------------------------------- #
def test_upstox_open_parse():
    obj = {"data": {"strategyChainData": {"strikeMap": {
        "23400": {"callOptionData": {"marketData": {"ltp": 120.0, "oi": 5000.0,
                                                    "prevOi": 4000.0, "volume": 9.0,
                                                    "bidPrice": 119.0, "askPrice": 121.0},
                                     "analytics": {"iv": 12.5, "delta": 0.55, "gamma": 0.0009,
                                                   "theta": -4.1, "vega": 3.0}},
                  "putOptionData": {"marketData": {"ltp": 80.0, "oi": 6000.0, "prevOi": 6500.0},
                                    "analytics": {"iv": 13.0, "delta": -0.45}}},
        "23450": {"callOptionData": {"marketData": {"ltp": 95.0, "oi": 7000.0, "prevOi": 7000.0},
                                     "analytics": {"iv": 12.0, "delta": 0.5}},
                  "putOptionData": {"marketData": {"ltp": 100.0, "oi": 8000.0, "prevOi": 7500.0},
                                    "analytics": {"iv": 12.2, "delta": -0.5}}}}}}}
    c = upstox_open.parse(obj, "NIFTY", "15SEP2026")
    assert c is not None and len(c.rows) == 2 and c.source == "upstox_open"
    ce = c.leg(23400, "CE")
    assert ce.iv == pytest.approx(0.125) and ce.delta == pytest.approx(0.55)
    assert ce.oi_change == pytest.approx(1000.0)
    assert c.capability["has_greeks"] is True


def test_upstox_open_parse_empty_returns_none():
    assert upstox_open.parse({"data": {"strategyChainData": {"strikeMap": {}}}}, "NIFTY", "X") is None


def test_nse_v3_parse():
    obj = {"records": {"underlyingValue": 23480.0, "data": [
        {"strikePrice": 23400, "expiryDate": "15-Sep-2026",
         "CE": {"lastPrice": 120.0, "openInterest": 5000, "changeinOpenInterest": 400,
                "totalTradedVolume": 12, "impliedVolatility": 12.5, "bidPrice": 119, "askPrice": 121},
         "PE": {"lastPrice": 80.0, "openInterest": 6000, "changeinOpenInterest": -200,
                "totalTradedVolume": 9, "impliedVolatility": 13.0}},
        {"strikePrice": 23450, "expiryDate": "15-Sep-2026",
         "CE": {"lastPrice": 95.0, "openInterest": 7000, "changeinOpenInterest": 0,
                "totalTradedVolume": 4, "impliedVolatility": 12.0},
         "PE": {"lastPrice": 100.0, "openInterest": 8000, "changeinOpenInterest": 100,
                "totalTradedVolume": 7, "impliedVolatility": 12.2}}]}}
    c = nse_v3.parse(obj, "NIFTY", "15SEP2026")
    assert c is not None and len(c.rows) == 2 and c.spot == 23480.0
    assert c.capability["has_greeks"] is False and c.capability["has_oi"] is True
    ce = c.leg(23400, "CE")
    assert ce.oi == pytest.approx(5000.0) and ce.oi_change == pytest.approx(400.0)
    assert ce.iv == pytest.approx(0.125)


def test_nse_v3_parse_filters_other_expiries():
    obj = {"records": {"underlyingValue": 100.0, "data": [
        {"strikePrice": 100, "expiryDate": "15-Sep-2026", "CE": {"lastPrice": 1.0, "openInterest": 1}},
        {"strikePrice": 100, "expiryDate": "22-Sep-2026", "CE": {"lastPrice": 2.0, "openInterest": 2}}]}}
    c = nse_v3.parse(obj, "NIFTY", "15SEP2026")
    assert c is not None and len(c.rows) == 1 and c.leg(100, "CE").ltp == 1.0


# --------------------------------------------------------------------------- #
#  quality scorer                                                              #
# --------------------------------------------------------------------------- #
def test_quality_score_full_chain_is_high():
    q = dq_score(_demo_chain(spot=23500), atm_window=5)
    assert 0.0 <= q["dqs"] <= 100.0
    assert q["verdict"] == "PASS" and q["atm_window_coverage"] == 1.0


def test_quality_score_empty_and_thin():
    empty = OptionChain(underlying="NIFTY", expiry="15SEP2026", ts="t", source="x", rows=[])
    assert dq_score(empty)["verdict"] == "FAIL"
    thin = OptionChain(underlying="NIFTY", expiry="15SEP2026", ts="t", source="x", spot=None,
                       rows=[StrikeRow(strike=23500.0, ce=OptionLeg(ltp=10.0))]).compute_atm()
    q = dq_score(thin)
    assert q["dqs"] < 80.0 and q["verdict"] in ("WARN", "FAIL")


def test_quality_score_flags_expired_chain():
    c = _demo_chain(spot=23500)
    c.expiry = "01JAN2020"
    assert dq_score(c)["expiry_valid"] is False
    assert dq_score(c)["verdict"] == "FAIL"


# --------------------------------------------------------------------------- #
#  static safety: no live-app imports                                          #
# --------------------------------------------------------------------------- #
def test_optionchain_does_not_import_live_app():
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "optionchain"
    forbidden = ("app.main", "app.autoscalp", "app.execution", "app.scalp",
                 "app.brokers", "app.order")
    hits = []
    for f in root.rglob("*.py"):
        tree = ast.parse(f.read_text(), str(f))
        for node in ast.walk(tree):
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module] if isinstance(node, ast.ImportFrom) and node.module
                    else [])
            for m in mods:
                if any(m == b or m.startswith(b + ".") for b in forbidden):
                    hits.append(f"{f.name}: {m}")
    assert not hits, f"live-app imports in the data layer: {hits}"
