"""
L2 SnapQuote capture -- MCX token resolution, packet validation, health check.
Offline; builds a throwaway l2_capture.db.
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import instruments as INS                              # noqa: E402
from app.l2capture.worker import _validate_packet               # noqa: E402
from app.l2capture import health as H                           # noqa: E402


# ---- MCX front-month resolution ----

def test_resolve_mcx_future_is_chronological_and_futcom_only():
    for sym in ("NATURALGAS", "CRUDEOIL"):
        m = INS.resolve_mcx_future(sym)
        assert m.get("status") == "OK", m
        # must be a FUTCOM future, not an option
        assert str(m.get("instrumenttype") or m.get("symbol") or "").upper().endswith("FUT") \
            or "FUT" in str(m.get("symbol") or "").upper()
        exps = m.get("available_expiries") or []
        assert exps == sorted(exps), "available_expiries must be chronological (ISO dates)"
        nxt = INS.resolve_mcx_future(sym, "NEXT")
        if nxt.get("status") == "OK" and len(exps) > 1:
            assert nxt["symboltoken"] != m["symboltoken"]


def test_resolve_mcx_future_rejects_lexical_trap():
    """'20NOV2026' sorts before '25SEP2026' lexically; the front month is SEP."""
    m = INS.resolve_mcx_future("NATURALGAS")
    exps = m.get("available_expiries") or []
    if exps:
        # first ISO date must be the earliest real calendar date
        from datetime import date
        ds = [date.fromisoformat(x) for x in exps]
        assert ds == sorted(ds) and ds[0] == min(ds)


# ---- packet validation ----

def test_validate_packet_rules():
    now_ms = int(time.time() * 1000)
    good = {"exch_ts_ms": now_ms - 500, "ltp": 23500.0, "bid": 23499.0, "ask": 23501.0}
    assert _validate_packet(good, now_ms=now_ms) == (True, "")
    assert _validate_packet({**good, "exch_ts_ms": None}, now_ms=now_ms)[1] == "no_exch_ts"
    assert _validate_packet({**good, "exch_ts_ms": 123}, now_ms=now_ms)[1] == "no_exch_ts"
    assert _validate_packet({**good, "exch_ts_ms": now_ms - 600_000}, now_ms=now_ms)[1] == "stale"
    assert _validate_packet({**good, "exch_ts_ms": now_ms + 60_000}, now_ms=now_ms)[1] == "future_ts"
    assert _validate_packet({**good, "ltp": 0.0}, now_ms=now_ms)[1] == "bad_ltp"
    assert _validate_packet({**good, "bid": 23502.0}, now_ms=now_ms)[1] == "crossed_book"


# ---- health check ----

def _mk_db(tmp_path, session="2026-09-09", n=600, sym="NIFTY", start_ist_min=9 * 60 + 15):
    p = tmp_path / "l2_capture.db"
    con = sqlite3.connect(p)
    con.executescript("""
      CREATE TABLE capture_runs(id INTEGER PRIMARY KEY, started_ts TEXT, ended_ts TEXT, note TEXT);
      CREATE TABLE snapquote_ticks(
        id INTEGER PRIMARY KEY, received_ts TEXT, exch_ts_ms INTEGER, exch_ts TEXT,
        session_date_ist TEXT, seq INTEGER, token TEXT, exchange_type INTEGER, symbol TEXT, kind TEXT,
        ltp REAL, ltq REAL, atp REAL, volume REAL, tot_buy_qty REAL, tot_sell_qty REAL,
        open REAL, high REAL, low REAL, close REAL, oi REAL, oi_change_pct REAL, ltt_epoch INTEGER,
        bid REAL, ask REAL, bid_qty REAL, ask_qty REAL, depth_json TEXT,
        upper_circuit REAL, lower_circuit REAL, week52_high REAL, week52_low REAL,
        raw_id INTEGER, run_id INTEGER, snap_key TEXT UNIQUE);
    """)
    base_utc = 1757000000  # arbitrary; IST minute derived below
    vol = 1_000_000
    for i in range(n):
        # ~1.4 Hz, walk forward
        e_ms = int((base_utc + i * 0.7) * 1000)
        ist = (start_ist_min + i // 84) % (24 * 60)   # advance ~1 IST-min per 84 packets
        # force exch_ts IST minute-of-day for coverage calc: encode via a UTC epoch
        e_utc = (ist * 60) - int(5.5 * 3600)          # seconds-of-day IST -> UTC epoch mod-day
        e_ms = int((1757_000_000 - (1757_000_000 % 86400) + (e_utc % 86400) + i * 0.7) * 1000)
        vol += 5
        depth = json.dumps({
            "buy": [{"price": 23499 - k, "quantity": 50, "orders": 1} for k in range(5)],
            "sell": [{"price": 23501 + k, "quantity": 50, "orders": 1} for k in range(5)]})
        e_iso = __import__("datetime").datetime.utcfromtimestamp(e_ms / 1000).isoformat() + "+00:00"
        con.execute(
            "INSERT INTO snapquote_ticks(received_ts,exch_ts_ms,exch_ts,session_date_ist,seq,token,"
            "symbol,kind,ltp,ltq,volume,oi,bid,ask,bid_qty,ask_qty,depth_json,snap_key) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e_iso, e_ms, e_iso,   # received ~= exch (no stale-write)
             session, i, "68407", sym, "FUTURE", 23500.0, 25.0, float(vol), 1.8e7,
             23499.0, 23501.0, 50.0, 50.0, depth, f"68407:{i}:{e_ms}"))
    con.execute("INSERT INTO capture_runs(started_ts,ended_ts,note) VALUES (?,?,?)",
                (f"{session}T04:00:00", f"{session}T10:00:00",
                 json.dumps({"state": "running", "rejected": 3, "reject_reasons": {"stale": 3}})))
    con.commit()
    con.close()
    return str(p)


def test_health_analyze_shape_and_dqs(tmp_path):
    db = _mk_db(tmp_path)
    rep = H.analyze(db, "2026-09-09")
    assert rep["status"] == "OK"
    s = rep["by_symbol"]["NIFTY"]
    for k in ("rows", "rate_hz", "session_coverage_pct", "l1_complete_pct", "l2_complete_pct",
              "clean_book_pct", "volume_monotonic_pct", "stale_written", "dup_snap_key",
              "null_pct", "dqs", "verdict", "gap_time_frac", "oi_null_pct"):
        assert k in s, k
    assert 0.0 <= s["dqs"] <= 100.0
    assert s["l1_complete_pct"] == 1.0 and s["l2_complete_pct"] == 1.0
    assert s["clean_book_pct"] == 1.0
    assert s["volume_monotonic_pct"] == 1.0 and s["stale_written"] == 0
    assert s["verdict"] in ("PASS", "WARN", "FAIL")
    assert rep["worker_rejects"]["rejected"] == 3
    assert "stale" in rep["worker_rejects"]["reasons"]
    md = H.render_md(rep)
    assert "OVERALL" in md and "DQS" in md


def test_health_no_data(tmp_path):
    p = tmp_path / "empty.db"
    con = sqlite3.connect(p)
    con.executescript("CREATE TABLE snapquote_ticks(session_date_ist TEXT, symbol TEXT); "
                      "CREATE TABLE capture_runs(started_ts TEXT, ended_ts TEXT, note TEXT);")
    con.commit()
    con.close()
    rep = H.analyze(str(p), None)
    assert rep["status"] == "NO_DATA"
