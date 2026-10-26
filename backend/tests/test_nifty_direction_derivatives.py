"""Captured futures / option-chain provider: point-in-time causality, identity validation, no fabrication, cadence-aware freshness."""
import json, sqlite3, time
from datetime import datetime, timezone, timedelta
import pytest
from app.nifty_direction import captured_derivatives as CDV, derivatives as D, adapters as A
from app.nifty_direction.historical import HistoricalAdapter

IST = timezone(timedelta(hours=5, minutes=30))
DAY = "2026-10-23"
AS_OF = datetime.fromisoformat("2026-10-23T15:30:00+05:30").timestamp()             # = 10:00:00Z


def z(h, m, s=0): return f"2026-10-23T{h:02d}:{m:02d}:{s:02d}Z"


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app import instruments as I
    mp = tmp_path / "master.json"
    mp.write_text(json.dumps([{"token": "48704", "name": "NIFTY", "expiry": "27OCT2026", "instrumenttype": "FUTIDX", "exch_seg": "NFO"},
                              {"token": "1", "name": "NIFTY", "expiry": "27OCT2026", "instrumenttype": "OPTIDX", "exch_seg": "NFO"},
                              {"token": "2", "name": "NIFTY", "expiry": "03NOV2026", "instrumenttype": "OPTIDX", "exch_seg": "NFO"}]))
    monkeypatch.setattr(I, "MASTER_CACHE", str(mp)); CDV._MASTER.update(mtime=None, u=None)
    db = str(tmp_path / "mh.db"); c = sqlite3.connect(db)
    c.executescript("""CREATE TABLE quote_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, received_ts TEXT NOT NULL, exch_ts TEXT, symbol TEXT NOT NULL, kind TEXT NOT NULL, token TEXT, expiry TEXT, strike REAL,
        option_type TEXT, session_date_ist TEXT NOT NULL, ltp REAL, close REAL, volume REAL, oi REAL, oi_change REAL, basis REAL, quote_status TEXT, source TEXT);
        CREATE INDEX ix_quotes_lookup ON quote_snapshots(symbol, kind, exch_ts); CREATE INDEX ix_quotes_opt ON quote_snapshots(symbol, expiry, strike, option_type, exch_ts);
        CREATE INDEX ix_quotes_day ON quote_snapshots(session_date_ist, symbol);
        CREATE TABLE option_greeks (id INTEGER PRIMARY KEY AUTOINCREMENT, received_ts TEXT NOT NULL, snap_key TEXT NOT NULL, underlying TEXT NOT NULL, expiry TEXT NOT NULL, strike REAL NOT NULL, option_type TEXT NOT NULL,
        session_date_ist TEXT NOT NULL, delta REAL, iv REAL, broker_status TEXT NOT NULL);
        CREATE INDEX ix_greeks_day ON option_greeks(session_date_ist, underlying);""")
    c.commit(); return c, db


def fut(c, exch, recv=None, oi=1000.0, chg=-100.0, ltp=22600.0, close=22700.0, expiry="27OCT2026", token="48704", status="OK"):
    c.execute("INSERT INTO quote_snapshots (received_ts, exch_ts, symbol, kind, token, expiry, session_date_ist, ltp, close, volume, oi, oi_change, basis, quote_status, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (recv or exch, exch, "NIFTY", "FUTURE", token, expiry, DAY, ltp, close, 5.0, oi, chg, -10.0, status, "ANGELONE_QUOTE_FULL")); c.commit()


def test_futures_point_in_time_and_prev_oi(env):
    c, db = env; fut(c, z(9, 30), oi=1000, chg=-100); fut(c, z(9, 50), oi=1200, chg=-50); fut(c, z(10, 5), oi=9999, chg=0)         # last row is AFTER as_of
    d = CDV.CapturedDerivatives(AS_OF, db); f = d.futures(); d.close()
    assert f["oi"] == 1200 and f["prev_oi"] == 1250 and f["expiry"] == "27OCT2026" and f["identity"] == "LISTED_IN_MASTER" and f["symbol"] == "NIFTY27OCT2026FUT" and f["ts"] == datetime(2026, 10, 23, 9, 50, tzinfo=timezone.utc).timestamp()


def test_futures_ignores_rows_received_after_as_of_and_bad_status(env):
    c, db = env; fut(c, z(9, 40), oi=1111); fut(c, z(9, 55), recv=z(10, 20), oi=2222); fut(c, z(9, 58), oi=3333, status="DATA_UNAVAILABLE")
    d = CDV.CapturedDerivatives(AS_OF, db); f = d.futures(); d.close(); assert f["oi"] == 1111


def test_futures_later_data_does_not_change_earlier_answer(env):
    c, db = env; fut(c, z(9, 40), oi=1111)
    d = CDV.CapturedDerivatives(AS_OF, db); a = d.futures(); d.close()
    fut(c, z(11, 0), oi=5)
    d = CDV.CapturedDerivatives(AS_OF, db); b = d.futures(); d.close(); assert a == b


def test_futures_missing_fields_or_token_mismatch_are_unavailable_not_guessed(env):
    c, db = env; fut(c, z(9, 40)); c.execute("UPDATE quote_snapshots SET oi_change=NULL"); c.commit()
    d = CDV.CapturedDerivatives(AS_OF, db); assert d.futures() is None; d.close()
    c.execute("DELETE FROM quote_snapshots"); c.commit(); fut(c, z(9, 40), token="99999")
    d = CDV.CapturedDerivatives(AS_OF, db); assert d.futures() is None; d.close()                       # captured token != listed contract


def test_futures_expired_contract_skipped_and_unlisted_flagged(env):
    c, db = env; fut(c, z(9, 40), expiry="20OCT2026", token="7"); d = CDV.CapturedDerivatives(AS_OF, db); assert d.futures() is None; d.close()
    fut(c, z(9, 41), expiry="24NOV2026", token="55"); d = CDV.CapturedDerivatives(AS_OF, db); f = d.futures(); d.close()
    assert f["expiry"] == "24NOV2026" and f["identity"] == "CAPTURED_NOT_IN_CURRENT_MASTER"


def _chain_rows(c, snap=z(9, 55), n=30, base=22600.0, oi_recv=z(9, 56), status="OK"):
    c.execute("INSERT INTO quote_snapshots (received_ts, exch_ts, symbol, kind, session_date_ist, ltp, quote_status) VALUES (?,NULL,?,?,?,?,?)", (z(9, 57), "NIFTY", "INDEX", DAY, base, "OK"))
    for i in range(n):
        k = base - 750 + 50 * i
        for t in ("CE", "PE"):
            c.execute("INSERT INTO option_greeks (received_ts, snap_key, underlying, expiry, strike, option_type, session_date_ist, delta, iv, broker_status) VALUES (?,?,?,?,?,?,?,?,?,?)", (snap, snap[:19], "NIFTY", "27OCT2026", k, t, DAY, .5, .12, status))
            c.execute("INSERT INTO quote_snapshots (received_ts, exch_ts, symbol, kind, expiry, strike, option_type, session_date_ist, ltp, volume, oi, quote_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (oi_recv, oi_recv, "NIFTY", "OPTION", "27OCT2026", k, t, DAY, 100.0, 7.0, 1000.0 + k % 97, "OK"))
    c.commit()


def test_chain_band_oi_and_oldest_timestamp(env):
    c, db = env; _chain_rows(c)
    d = CDV.CapturedDerivatives(AS_OF, db); ch = d.option_chain(); d.close()
    assert ch["expiry"] == "27OCT2026" and ch["atm"] == 22600.0 and ch["identity"] == "LISTED_IN_MASTER" and ch["oi_scope"] == "NEAR_MONEY"
    assert len(ch["rows"]) == 21 and ch["rows"][0]["strike"] == 22100.0 and ch["rows"][-1]["strike"] == 23100.0 and all(r["ce_oi"] and r["pe_oi"] and r["ce_iv"] == .12 for r in ch["rows"])
    assert ch["ts"] == datetime(2026, 10, 23, 9, 55, tzinfo=timezone.utc).timestamp()                          # oldest of greek (09:55) and OI (09:56) times
    v = D.chain_view(ch, AS_OF, spot=22600.0, max_age=1800); assert v["available"] and v["strikes"] == 21 and v["oi_scope"] == "NEAR_MONEY" and v["identity"] == "LISTED_IN_MASTER"


def test_chain_not_built_without_greeks_spot_or_with_failed_status(env):
    c, db = env; d = CDV.CapturedDerivatives(AS_OF, db); assert d.option_chain() is None; d.close()
    _chain_rows(c, status="NO_DATA"); d = CDV.CapturedDerivatives(AS_OF, db); assert d.option_chain() is None; d.close()
    c.executescript("DELETE FROM option_greeks; DELETE FROM quote_snapshots;"); _chain_rows(c); c.execute("DELETE FROM quote_snapshots WHERE kind='INDEX'"); c.commit()
    d = CDV.CapturedDerivatives(AS_OF, db); ch = d.option_chain(); d.close(); assert ch["rows"] == [] and ch["reason"] == "NO_SPOT" and D.chain_view(ch, AS_OF)["available"] is False


def test_chain_ignores_snapshots_after_as_of(env):
    c, db = env; _chain_rows(c, snap=z(9, 55)); _chain_rows(c, snap=z(10, 20), oi_recv=z(10, 20))
    d = CDV.CapturedDerivatives(AS_OF, db); ch = d.option_chain(); d.close()
    assert ch["snap_key"] == "2026-10-23T09:55:00" and ch["greek_ts"] == datetime(2026, 10, 23, 9, 55, tzinfo=timezone.utc).timestamp()


def test_freshness_limits_are_cadence_aware_and_report_age():
    now = 10_000.0; f = {"symbol": "NIFTY27OCT2026FUT", "expiry": "27OCT2026", "ltp": 101, "prev_close": 100, "oi": 90, "prev_oi": 100, "ts": now - 200}
    assert D.futures_view(f, now)["reason"] == "STALE"                                             # live limit 60 s
    v = D.futures_view(f, now, max_age=420); assert v["available"] and v["age_sec"] == 200 and v["activity"] == "SHORT_COVERING"
    s = D.futures_view({**f, "ts": now - 500}, now, max_age=420); assert s["reason"] == "STALE" and s["age_sec"] == 500 and s["limit_sec"] == 420
    assert D.confirm(f, None, now, max_age={"futures": 420, "chain": 1800})["futures"]["available"] is True


def test_historical_adapter_exposes_capabilities_and_deriv_limits(env, monkeypatch, tmp_path):
    c, db = env; fut(c, z(9, 40)); monkeypatch.setattr(CDV, "DB", db)
    h = sqlite3.connect(str(tmp_path / "h.db")); h.execute("CREATE TABLE bars (sym TEXT, ts INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, PRIMARY KEY (sym, ts)) WITHOUT ROWID"); h.commit(); h.close()
    ad = HistoricalAdapter(AS_OF, str(tmp_path / "h.db"))
    try:
        assert {A.CAP_FUTURES, A.CAP_CHAIN} <= A.capabilities(ad) and ad.deriv_max_age == {"futures": 420.0, "chain": 1800.0}
        monkeypatch.setattr(CDV, "DB", db); monkeypatch.setattr(CDV.CapturedDerivatives.__init__, "__defaults__", (db, "NIFTY"))
        assert ad.futures()["oi"] == 1000.0 and ad.option_chain() is None
    finally: ad.close()
