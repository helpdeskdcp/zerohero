"""app.ai.shadow_outcome -- forward-outcome tracking for shadow decisions.
Real option-premium snapshots only, via an isolated HistStore (tmp_path,
never the live market_history.db). No fabricated data, no look-ahead
(only rows with ts strictly after the signal are ever considered)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.ai import shadow_outcome as so
from app.histcap.store import HistStore

_UTC = timezone.utc


@pytest.fixture
def store(tmp_path):
    return HistStore(str(tmp_path / "mh.db"))


def _seed_quote(store, *, symbol, strike, option_type, expiry, ts, ltp):
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO quote_snapshots(received_ts,exch_ts,snap_key,instrument_key,symbol,kind,"
            "exchange,token,expiry,strike,option_type,session_date_ist,ltp,source) "
            "VALUES(?,?,?,?,?, 'OPTION','NSE','T1',?,?,?,?,?,'seed')",
            (ts, ts, ts[:19], "NSE:T1", symbol, expiry, strike, option_type, ts[:10], ltp))


def _row(**over):
    base = {"ts": "2026-09-21T06:00:00+00:00", "symbol": "NATURALGAS",
           "deterministic_decision": "BUY_PE", "strike": 276.0, "expiry": "25SEP2026",
           "entry": 10.0, "stop_loss": 7.0, "target_1": 15.0, "max_hold_sec": 1800}
    base.update(over)
    return base


def test_no_levels_recorded_when_entry_missing():
    result = so.check_outcome(_row(entry=None))
    assert result["status"] == "NO_LEVELS_RECORDED"


def test_no_leg_recorded_when_decision_not_ce_or_pe():
    result = so.check_outcome(_row(deterministic_decision="NO_TRADE"))
    assert result["status"] == "NO_LEG_RECORDED"


def test_insufficient_data_when_no_forward_snapshots(store):
    result = so.check_outcome(_row(), store=store)
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["snapshot_count"] == 0


def test_target_hit_detected_from_real_snapshots(store):
    row = _row()
    _seed_quote(store, symbol="NATURALGAS", strike=276.0, option_type="PE", expiry="25SEP2026",
               ts="2026-09-21T06:05:00+00:00", ltp=12.0)
    _seed_quote(store, symbol="NATURALGAS", strike=276.0, option_type="PE", expiry="25SEP2026",
               ts="2026-09-21T06:10:00+00:00", ltp=16.0)   # >= target_1=15
    result = so.check_outcome(row, store=store)
    assert result["status"] == "TARGET_HIT"
    assert result["mfe"] == pytest.approx(6.0)   # 16 - 10
    assert result["snapshot_count"] == 2


def test_sl_hit_detected_from_real_snapshots(store):
    row = _row()
    _seed_quote(store, symbol="NATURALGAS", strike=276.0, option_type="PE", expiry="25SEP2026",
               ts="2026-09-21T06:05:00+00:00", ltp=8.0)
    _seed_quote(store, symbol="NATURALGAS", strike=276.0, option_type="PE", expiry="25SEP2026",
               ts="2026-09-21T06:10:00+00:00", ltp=6.0)    # <= stop_loss=7
    result = so.check_outcome(row, store=store)
    assert result["status"] == "SL_HIT"
    assert result["mae"] == pytest.approx(4.0)   # 10 - 6


def test_first_touch_wins_not_last(store):
    """Target hit first, then price falls back below stop_loss later -- the
    outcome must stay TARGET_HIT (a later snapshot can't un-hit an earlier,
    real touch)."""
    row = _row()
    _seed_quote(store, symbol="NATURALGAS", strike=276.0, option_type="PE", expiry="25SEP2026",
               ts="2026-09-21T06:05:00+00:00", ltp=16.0)   # target hit first
    _seed_quote(store, symbol="NATURALGAS", strike=276.0, option_type="PE", expiry="25SEP2026",
               ts="2026-09-21T06:10:00+00:00", ltp=5.0)    # falls back after
    result = so.check_outcome(row, store=store)
    assert result["status"] == "TARGET_HIT"


def test_open_when_neither_hit_and_within_hold_window(store):
    row = _row(max_hold_sec=3600)
    _seed_quote(store, symbol="NATURALGAS", strike=276.0, option_type="PE", expiry="25SEP2026",
               ts="2026-09-21T06:05:00+00:00", ltp=11.0)   # between SL and target
    now = datetime(2026, 9, 21, 6, 20, 0, tzinfo=_UTC)   # 20 min after signal, hold=60min
    result = so.check_outcome(row, store=store, now=now)
    assert result["status"] == "OPEN"


def test_timed_out_when_neither_hit_and_hold_window_elapsed(store):
    row = _row(max_hold_sec=600)   # 10-min hold
    _seed_quote(store, symbol="NATURALGAS", strike=276.0, option_type="PE", expiry="25SEP2026",
               ts="2026-09-21T06:05:00+00:00", ltp=11.0)
    now = datetime(2026, 9, 21, 6, 30, 0, tzinfo=_UTC)   # 30 min after signal, hold=10min
    result = so.check_outcome(row, store=store, now=now)
    assert result["status"] == "TIMED_OUT_NO_HIT"


def test_never_looks_at_data_before_the_signal(store):
    """A snapshot BEFORE the signal timestamp that would satisfy TARGET_HIT
    must never be counted -- this is a review function, but it must still
    never attribute a pre-signal price move to a signal that hadn't fired
    yet (no retroactive outcomes)."""
    row = _row()
    _seed_quote(store, symbol="NATURALGAS", strike=276.0, option_type="PE", expiry="25SEP2026",
               ts="2026-09-21T05:00:00+00:00", ltp=20.0)   # BEFORE signal ts (06:00)
    result = so.check_outcome(row, store=store)
    assert result["status"] == "INSUFFICIENT_DATA"   # the only real row is pre-signal -> ignored


def test_outcomes_report_skips_rows_without_recorded_levels(monkeypatch, store):
    from app import db as _db
    rows = [
        {"symbol": "NATURALGAS", "ts": "2026-09-21T06:00:00+00:00", "signal_id": "a",
         "deterministic_decision": "BUY_PE", "fused_final_state": "SELL", "telegram_status": "SENT",
         "strike": 276.0, "expiry": "25SEP2026", "entry": 10.0, "stop_loss": 7.0, "target_1": 15.0,
         "max_hold_sec": 1800},
        {"symbol": "NIFTY", "ts": "2026-09-21T06:00:00+00:00", "signal_id": "b",
         "deterministic_decision": "NO_TRADE", "fused_final_state": "NO_TRADE",
         "telegram_status": None, "entry": None},
    ]
    monkeypatch.setattr(_db, "list_shadow_decisions", lambda symbol=None, limit=200: rows)
    out = so.outcomes_report(store=store)
    assert len(out) == 1
    assert out[0]["symbol"] == "NATURALGAS"
    assert out[0]["status"] == "INSUFFICIENT_DATA"
