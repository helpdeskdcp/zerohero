"""
Option Greeks Engine — derived exposure over captured broker Greeks.

No network, no broker fetch, no trading logic. Seeds the histcap store tables
directly and runs the engine against them.
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.greeks_engine.compute import pair_exposure, build_snapshot   # noqa: E402
from app.greeks_engine.engine import GreeksEngine                     # noqa: E402
from app.greeks_engine.model import Quality                           # noqa: E402
from app.histcap.store import HistStore                               # noqa: E402

_NOW = datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------------ pure math
def test_pair_exposure_is_oi_times_greek_exact():
    pe = pair_exposure(1000, 0.5, 0.012, -2.0, 3.5)
    assert pe["delta_exp"] == 500.0 and pe["gamma_exp"] == 12.0
    assert pe["theta_exp"] == -2000.0 and pe["vega_exp"] == 3500.0
    assert pe["oi"] == 1000.0


def test_pair_exposure_none_when_oi_missing_or_no_greek():
    assert pair_exposure(None, 0.5, 0.01, -2, 3) is None        # no OI -> nothing to derive
    assert pair_exposure(1000, None, None, None, None) is None  # no greek -> nothing to derive
    pe = pair_exposure(1000, 0.5, None, None, None)             # partial greek is ok
    assert pe["delta_exp"] == 500.0 and pe["gamma_exp"] is None  # missing gamma stays None, not 0


def _rows(strikes_ce_pe):
    """[(strike, ce_oi, ce_delta, ce_gamma, ce_iv, pe_oi, pe_delta, pe_gamma, pe_iv)] -> row dicts"""
    out = []
    for k, coi, cd, cg, civ, poi, pd, pg, piv in strikes_ce_pe:
        out.append({"strike": k, "option_type": "CE", "oi": coi, "delta": cd, "gamma": cg,
                    "theta": -1.0, "vega": 2.0, "iv": civ})
        out.append({"strike": k, "option_type": "PE", "oi": poi, "delta": pd, "gamma": pg,
                    "theta": -1.0, "vega": 2.0, "iv": piv})
    return out


def test_build_snapshot_totals_and_weighted_iv():
    rows = _rows([
        (23800, 1000, 0.55, 0.010, 0.14, 1200, -0.45, 0.010, 0.15),
        (23900, 2000, 0.40, 0.020, 0.13, 800, -0.60, 0.020, 0.16),
        (24000, 500, 0.25, 0.008, 0.15, 3000, -0.75, 0.008, 0.18),
    ])
    s = build_snapshot(rows, underlying="NIFTY", expiry="09SEP2026", underlying_price=23850.0,
                       underlying_price_src="ANGELONE_QUOTE:FUTURE", as_of_ts=_iso(_NOW),
                       expected_pairs=6, stale_sec_threshold=90)
    assert s["quality"] == Quality.VALID.value and s["coverage_pct"] == 100.0
    # delta exposure = sum(OI*delta)
    assert s["ce_delta_exp"] == round(1000*0.55 + 2000*0.40 + 500*0.25, 6)
    assert s["pe_delta_exp"] == round(1200*-0.45 + 800*-0.60 + 3000*-0.75, 6)
    assert s["net_delta_exp"] == round(s["ce_delta_exp"] + s["pe_delta_exp"], 6)      # signed
    assert s["diff_delta_exp"] == round(s["ce_delta_exp"] - s["pe_delta_exp"], 6)     # CE - PE
    # PCR(OI)
    assert s["ce_oi_total"] == 3500.0 and s["pe_oi_total"] == 5000.0
    assert s["pcr_oi"] == round(5000/3500, 4)
    # OI-weighted IV = sum(iv*oi)/sum(oi) over all 6 legs
    num = 1000*0.14 + 1200*0.15 + 2000*0.13 + 800*0.16 + 500*0.15 + 3000*0.18
    den = 1000 + 1200 + 2000 + 800 + 500 + 3000
    assert abs(s["oi_weighted_iv"] - num/den) < 1e-6
    assert s["vega_weighted_iv"] is not None


def test_build_snapshot_gamma_concentration():
    # 24000 dominates gamma exposure (huge OI * big gamma)
    rows = _rows([
        (23800, 100, 0.5, 0.001, 0.14, 100, -0.5, 0.001, 0.14),
        (24000, 100000, 0.5, 0.05, 0.14, 100000, -0.5, 0.05, 0.14),
        (24200, 100, 0.5, 0.001, 0.14, 100, -0.5, 0.001, 0.14),
    ])
    s = build_snapshot(rows, underlying="NIFTY", expiry="E", underlying_price=24000.0,
                       underlying_price_src=None, as_of_ts=_iso(_NOW), expected_pairs=6,
                       stale_sec_threshold=90)
    assert s["gamma_conc_strike"] == 24000.0
    assert s["gamma_conc_pct"] > 99.0 and s["gamma_herfindahl"] > 0.98


def test_build_snapshot_missing_oi_is_partial_never_estimated():
    rows = _rows([
        (23800, 1000, 0.5, 0.01, 0.14, 1000, -0.5, 0.01, 0.14),
        (23900, None, 0.4, 0.02, 0.13, None, -0.6, 0.02, 0.15),   # no OI on this strike
        (24000, None, 0.3, 0.01, 0.15, None, -0.7, 0.01, 0.16),   # no OI on this strike
    ])
    s = build_snapshot(rows, underlying="NIFTY", expiry="E", underlying_price=None,
                       underlying_price_src=None, as_of_ts=_iso(_NOW), expected_pairs=6,
                       stale_sec_threshold=90)
    assert s["n_pairs_used"] == 2 and s["n_pairs_missing"] == 4
    assert s["coverage_pct"] == round(100*2/6, 2)
    assert s["quality"] == Quality.PARTIAL.value
    # totals only from the 1 strike that had OI (no fabrication for the other two)
    assert s["ce_delta_exp"] == 500.0 and s["pe_delta_exp"] == -500.0


def test_build_snapshot_no_data():
    rows = [{"strike": 23800, "option_type": "CE", "oi": None, "delta": None,
             "gamma": None, "theta": None, "vega": None, "iv": None}]
    s = build_snapshot(rows, underlying="NIFTY", expiry="E", underlying_price=None,
                       underlying_price_src=None, as_of_ts=_iso(_NOW), expected_pairs=0,
                       stale_sec_threshold=90)
    assert s["quality"] == Quality.NO_DATA.value
    assert s["net_delta_exp"] is None and s["oi_weighted_iv"] is None and s["per_strike"] == []


def test_build_snapshot_stale():
    rows = _rows([(23800, 1000, 0.5, 0.01, 0.14, 1000, -0.5, 0.01, 0.14)])
    old = _iso(_NOW - timedelta(minutes=30))
    s = build_snapshot(rows, underlying="NIFTY", expiry="E", underlying_price=23800.0,
                       underlying_price_src=None, as_of_ts=old, expected_pairs=2,
                       stale_sec_threshold=90)
    assert s["quality"] == Quality.STALE.value and s["stale_sec"] > 1000


# ------------------------------------------------------------------ engine + store
@pytest.fixture
def hist_db(tmp_path):
    p = str(tmp_path / "mh.db")
    HistStore(p)                       # creates histcap schema (option_greeks, quote_snapshots, ...)
    return p


def _seed(db, *, ts, expiry="09SEP2026", underlying="NIFTY"):
    c = sqlite3.connect(db)
    ist = "2026-09-02"
    # broker greeks at 5 strikes CE+PE
    grk = [(23800, "CE", 0.55, 0.010, 0.14), (23800, "PE", -0.45, 0.010, 0.15),
           (23850, "CE", 0.50, 0.020, 0.135), (23850, "PE", -0.50, 0.020, 0.155),
           (23900, "CE", 0.42, 0.015, 0.13), (23900, "PE", -0.58, 0.015, 0.16),
           (23950, "CE", 0.30, 0.008, 0.14), (23950, "PE", -0.70, 0.008, 0.17),
           (24000, "CE", 0.20, 0.006, 0.15), (24000, "PE", -0.80, 0.006, 0.18)]
    for k, typ, d, g, iv in grk:
        c.execute("INSERT OR IGNORE INTO option_greeks(received_ts,server_ts,snap_key,underlying,"
                  "expiry,strike,option_type,session_date_ist,delta,gamma,theta,vega,iv,iv_pct,"
                  "trade_volume,broker_status,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (ts, ts, ts[:19], underlying, expiry, k, typ, ist, d, g, -1.5, 2.5, iv,
                   iv*100, 100, "OK", "ANGELONE_OPTION_GREEK"))
    # OI quotes for 4 of the 5 strikes (24000 deliberately absent -> PARTIAL-ish but still >80% of band? band=those with OI)
    oi = {(23800, "CE"): 30000, (23800, "PE"): 500000, (23850, "CE"): 45000, (23850, "PE"): 460000,
          (23900, "CE"): 220000, (23900, "PE"): 380000, (23950, "CE"): 610000, (23950, "PE"): 90000}
    for (k, typ), v in oi.items():
        c.execute("INSERT OR IGNORE INTO quote_snapshots(received_ts,snap_key,instrument_key,symbol,"
                  "kind,exchange,token,expiry,strike,option_type,session_date_ist,ltp,oi,source) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (ts, ts[:19], f"NFO:T{k}{typ}", underlying, "OPTION", "NFO", f"T{k}{typ}",
                   expiry, k, typ, ist, 100.0, v, "ANGELONE_QUOTE_FULL"))
    # underlying future price
    c.execute("INSERT OR IGNORE INTO quote_snapshots(received_ts,snap_key,instrument_key,symbol,kind,"
              "exchange,token,session_date_ist,ltp,source) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (ts, ts[:19], "NFO:FUT", underlying, "FUTURE", "NFO", "FUT", ist, 23875.5, "ANGELONE_QUOTE_FULL"))
    c.commit(); c.close()


def test_engine_run_once_derives_and_persists(hist_db):
    ts = _iso(_NOW)
    _seed(hist_db, ts=ts)
    eng = GreeksEngine(hist_db)
    r = eng.run_once("NIFTY")
    assert r["snapshots_written"] == 1
    snap = eng.latest("NIFTY")
    assert snap["as_of_ts"] == ts and snap["underlying_price"] == 23875.5
    assert snap["underlying_price_src"] == "ANGELONE_QUOTE:FUTURE"
    # 8 of 10 (strike,side) pairs had OI -> used 8
    assert snap["n_pairs_used"] == 8
    # delta exposure sanity: CE positive, PE negative
    assert snap["ce_delta_exp"] > 0 and snap["pe_delta_exp"] < 0
    assert snap["pcr_oi"] is not None and snap["oi_weighted_iv"] is not None
    assert snap["gamma_conc_strike"] in (23800.0, 23850.0, 23900.0, 23950.0)
    assert snap["source"] == "DERIVED_FROM_ANGELONE_OPTION_GREEK"
    assert isinstance(snap["per_strike"], list) and snap["per_strike"]


def test_engine_is_idempotent_append_only(hist_db):
    ts = _iso(_NOW)
    _seed(hist_db, ts=ts)
    eng = GreeksEngine(hist_db)
    assert eng.run_once("NIFTY")["snapshots_written"] == 1
    assert eng.run_once("NIFTY")["snapshots_written"] == 0        # same as_of_ts -> UNIQUE ignore
    with sqlite3.connect(hist_db) as c:
        assert c.execute("SELECT COUNT(*) FROM greek_exposure").fetchone()[0] == 1


def test_engine_history_is_look_ahead_safe(hist_db):
    t1 = _iso(_NOW - timedelta(minutes=10))
    t2 = _iso(_NOW - timedelta(minutes=5))
    _seed(hist_db, ts=t1)
    _seed(hist_db, ts=t2)
    eng = GreeksEngine(hist_db)
    eng.run_once("NIFTY", as_of=t1)
    eng.run_once("NIFTY", as_of=t2)
    assert len(eng.history("NIFTY")) == 2
    capped = eng.history("NIFTY", as_of=t1)
    assert len(capped) == 1 and capped[0]["as_of_ts"] == t1       # t2 snapshot excluded


def test_engine_no_greeks_is_no_data_run(hist_db):
    eng = GreeksEngine(hist_db)                                   # empty store
    r = eng.run_once("NIFTY")
    assert r["snapshots_written"] == 0 and r["expiries"] == []
    runs = eng.runs(1)
    assert runs and runs[0]["snapshots_written"] == 0


def test_engine_stale_when_greeks_old(hist_db):
    old = _iso(_NOW - timedelta(minutes=45))
    _seed(hist_db, ts=old)
    eng = GreeksEngine(hist_db)
    eng.run_once("NIFTY")
    assert eng.latest("NIFTY")["quality"] == Quality.STALE.value


def test_engine_status_shape(hist_db):
    _seed(hist_db, ts=_iso(_NOW))
    eng = GreeksEngine(hist_db)
    eng.run_once("NIFTY")
    st = eng.status()
    assert st["exposure_snapshots"] == 1 and "by_quality" in st and st["runs"] >= 1
