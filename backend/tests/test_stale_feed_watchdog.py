"""
stale_feed_watchdog.py -- value-staleness detector over quote_snapshots.

Proves: (a) a frozen index LTP (identical across >=3 snaps, >=120s, status OK,
market hours) is detected; (b) the benign daily AngelOne REST pre-close freeze
(~15:15-15:40 IST) is tagged KNOWN_PRECLOSE and does NOT count as anomalous;
(c) a mid-session freeze with options still moving is tagged CONFIRMED + counts
as anomalous; (d) a live/moving feed produces nothing; (e) --self-test passes
against the real DB when present.
"""
import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "stale_feed_watchdog", _ROOT / "scripts" / "stale_feed_watchdog.py")
sfw = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sfw)


# --------------------------------------------------------------------------- #
#  synthetic in-memory quote_snapshots                                        #
# --------------------------------------------------------------------------- #
def _db():
    con = sqlite3.connect(":memory:")
    con.execute("""CREATE TABLE quote_snapshots(
        received_ts TEXT, symbol TEXT, kind TEXT, ltp REAL,
        quote_status TEXT, session_date_ist TEXT, strike REAL, option_type TEXT)""")
    return con


def _add_index(con, symbol, date, start_hhmm, n, step_s, ltp_seq, status="OK"):
    """n snaps starting at start_hhmm IST, step_s apart. ltp_seq is a list or a
    single value (repeated). Times written as UTC (IST-5:30)."""
    h, m = map(int, start_hhmm.split(":"))
    base_utc_min = h * 60 + m - 330
    for i in range(n):
        tot = base_utc_min * 60 + i * step_s
        hh, mm, ss = tot // 3600, (tot % 3600) // 60, tot % 60
        ts = f"{date}T{hh:02d}:{mm:02d}:{ss:02d}Z"
        ltp = ltp_seq[i] if isinstance(ltp_seq, (list, tuple)) else ltp_seq
        con.execute("INSERT INTO quote_snapshots VALUES(?,?,?,?,?,?,NULL,NULL)",
                    (ts, symbol, "INDEX", ltp, status, date))


def _add_options_moving(con, symbol, date, start_hhmm, n, step_s):
    h, m = map(int, start_hhmm.split(":"))
    base_utc_min = h * 60 + m - 330
    for i in range(n):
        tot = base_utc_min * 60 + i * step_s
        hh, mm, ss = tot // 3600, (tot % 3600) // 60, tot % 60
        ts = f"{date}T{hh:02d}:{mm:02d}:{ss:02d}Z"
        con.execute("INSERT INTO quote_snapshots VALUES(?,?,?,?,?,?,?,?)",
                    (ts, symbol, "OPTION", 100.0 + i * 7, "OK", date, 23400.0, "CE"))


DATE = "2026-06-01"


def test_moving_feed_produces_nothing():
    con = _db()
    _add_index(con, "NIFTY", DATE, "12:00", 20, 60,
               [23000 + i * 3 for i in range(20)])
    res = sfw.scan(DATE, con=con)
    assert res["runs"] == []
    assert res["anomalous"] == []


def test_midsession_freeze_with_moving_options_is_confirmed_anomaly():
    con = _db()
    # NIFTY index frozen 12:00-12:12 IST (13 snaps @ 60s), options moving
    _add_index(con, "NIFTY", DATE, "12:00", 13, 60, 23111.5)
    _add_options_moving(con, "NIFTY", DATE, "12:00", 13, 60)
    res = sfw.scan(DATE, con=con)
    assert len(res["runs"]) == 1
    r = res["runs"][0]
    assert r["symbol"] == "NIFTY"
    assert r["verdict"] == "CONFIRMED"
    assert r["anomalous"] is True
    assert r["dur_s"] >= 120
    assert res["anomalous"] == [r]


def test_preclose_freeze_is_known_and_not_anomalous():
    con = _db()
    # frozen 15:16-15:29 IST -> inside the benign pre-close window
    for sym, val in [("NIFTY", 23389.25), ("SENSEX", 74629.5), ("BANKEX", 63641.8)]:
        _add_index(con, sym, DATE, "15:16", 14, 60, val)
        _add_options_moving(con, sym, DATE, "15:16", 14, 60)
    res = sfw.scan(DATE, con=con)
    assert len(res["runs"]) == 3
    assert all(r["verdict"] == "KNOWN_PRECLOSE" for r in res["runs"])
    assert res["anomalous"] == []
    assert res["market_wide"] is False          # only anomalous runs count
    assert "CLEAN" in sfw.summary_line(res)


def test_short_or_brief_freeze_below_thresholds_is_ignored():
    con = _db()
    _add_index(con, "NIFTY", DATE, "12:00", 2, 60, 23100.0)     # only 2 snaps
    _add_index(con, "BANKNIFTY", DATE, "12:00", 4, 20, 51000.0)  # 4 snaps but 60s span
    res = sfw.scan(DATE, con=con)
    assert res["runs"] == []


def test_freeze_outside_market_hours_is_ignored():
    con = _db()
    _add_index(con, "NIFTY", DATE, "16:30", 15, 60, 23100.0)    # post-close
    res = sfw.scan(DATE, con=con)
    assert res["runs"] == []


def test_non_ok_status_breaks_the_run():
    con = _db()
    seq = [23100.0] * 15
    _add_index(con, "NIFTY", DATE, "12:00", 15, 60, seq, status="STALE")
    res = sfw.scan(DATE, con=con)
    assert res["runs"] == []                    # all_ok gate


def test_classifier_pure():
    assert sfw._is_known_preclose("2026-06-01T09:46:00Z", "2026-06-01T09:59:00Z") is True
    assert sfw._is_known_preclose("2026-06-01T06:30:00Z", "2026-06-01T06:45:00Z") is False


def test_selftest_entrypoint_passes():
    """--self-test must exit 0 (it self-skips the DB portion if the file/rows
    are absent, and asserts KNOWN_PRECLOSE tagging when present)."""
    r = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "stale_feed_watchdog.py"), "--self-test"],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SELF-TEST: PASS" in r.stdout
