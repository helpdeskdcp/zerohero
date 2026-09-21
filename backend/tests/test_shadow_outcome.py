"""app.ai.shadow_outcome -- forward-outcome tracking for shadow decisions.
Real option-premium snapshots only, via an isolated HistStore (tmp_path,
never the live market_history.db). No fabricated data, no look-ahead
(only rows with ts strictly after the signal are ever considered)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

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


# ---- group_into_setups / setups_report: real incident (2026-09-21) -- one
# continuing NATURALGAS move was re-evaluated every ~30s and a naive per-tick
# or per-(entry,stop_loss,target_1)-tuple count reported 7-16 "distinct
# setups" for what was really ONE opportunity.

def _tick(ts, **over):
    row = {"ts": ts, "symbol": "NATURALGAS", "deterministic_decision": "BUY_PE",
          "strike": 275.0, "expiry": "23SEP2026", "option_token": "578349",
          "entry": 4.1, "stop_loss": 3.7, "target_1": 4.7, "max_hold_sec": 1800}
    row.update(over)
    return row


def test_group_into_setups_collapses_a_continuing_move_into_one_setup():
    rows = [_tick(f"2026-09-21T09:5{i}:00+00:00", entry=4.0 + i * 0.02) for i in range(9)]
    groups = so.group_into_setups(rows)
    assert len(groups) == 1
    assert groups[0]["evaluation_count"] == 9
    assert groups[0]["first_seen"] == rows[0]["ts"]
    assert groups[0]["last_seen"] == rows[-1]["ts"]


def test_group_into_setups_fused_state_oscillation_does_not_break_the_chain():
    """AI confidence wavering between NO_TRADE/WEAK_SELL on the SAME leg
    (fused_final_state) must not split the run -- only the deterministic
    engine's own decision/leg identity does."""
    rows = [_tick("2026-09-21T09:50:00+00:00", fused_final_state="WEAK_SELL"),
           _tick("2026-09-21T09:50:30+00:00", fused_final_state="NO_TRADE"),
           _tick("2026-09-21T09:51:00+00:00", fused_final_state="WEAK_SELL")]
    groups = so.group_into_setups(rows)
    assert len(groups) == 1 and groups[0]["evaluation_count"] == 3


def test_group_into_setups_real_no_trade_tick_breaks_the_chain():
    rows = [_tick("2026-09-21T09:50:00+00:00"),
           {"ts": "2026-09-21T09:50:30+00:00", "symbol": "NATURALGAS",
            "deterministic_decision": "NO_TRADE", "entry": None},
           _tick("2026-09-21T09:51:00+00:00")]
    groups = so.group_into_setups(rows)
    assert len(groups) == 2
    assert all(g["evaluation_count"] == 1 for g in groups)


def test_group_into_setups_opposite_direction_breaks_the_chain():
    rows = [_tick("2026-09-21T09:50:00+00:00", deterministic_decision="BUY_PE"),
           _tick("2026-09-21T09:50:30+00:00", deterministic_decision="BUY_CE",
                strike=280.0, option_token="999999")]
    groups = so.group_into_setups(rows)
    assert len(groups) == 2


def test_group_into_setups_different_leg_breaks_the_chain():
    """Same direction, but the engine rolled to a different strike -- a
    genuinely new opportunity, not a continuation."""
    rows = [_tick("2026-09-21T09:50:00+00:00", strike=275.0, option_token="578349"),
           _tick("2026-09-21T09:50:30+00:00", strike=280.0, option_token="999999")]
    groups = so.group_into_setups(rows)
    assert len(groups) == 2


def test_group_into_setups_large_gap_breaks_the_chain():
    rows = [_tick("2026-09-21T09:50:00+00:00"),
           _tick("2026-09-21T09:55:00+00:00")]   # 5 min gap > _MAX_SETUP_GAP_SEC
    groups = so.group_into_setups(rows)
    assert len(groups) == 2


def test_setups_report_uses_first_tick_for_outcome_not_a_later_drifted_one(store, fresh_db):
    """The setup's outcome must be evaluated from the FIRST tick's entry/SL/
    target (the moment the opportunity was actually detected), not a later
    tick's drifted levels."""
    rows = [_tick("2026-09-21T09:50:00+00:00", entry=4.0, stop_loss=3.6, target_1=4.6),
           _tick("2026-09-21T09:50:30+00:00", entry=4.2, stop_loss=3.8, target_1=4.9)]
    _seed_quote(store, symbol="NATURALGAS", strike=275.0, option_type="PE", expiry="23SEP2026",
               ts="2026-09-21T09:51:00+00:00", ltp=4.6)   # hits the FIRST tick's target (4.6), not the second's (4.9)
    import app.db as _db_mod
    with patch.object(_db_mod, "list_shadow_decisions", lambda symbol=None, limit=200: rows):
        out = so.setups_report(symbol="NATURALGAS", store=store)
    assert len(out) == 1
    assert out[0]["entry"] == 4.0 and out[0]["target_1"] == 4.6
    assert out[0]["status"] == "TARGET_HIT"
    assert out[0]["evaluation_count"] == 2


def test_real_trade_match_finds_a_real_trade_in_window():
    real_rows = [{"decision": "BUY_PE", "created_ts": "2026-09-21T09:48:30+00:00",
                 "status": "CLOSED", "outcome": "WIN", "entry": 719.4,
                 "exit_reason": "TARGET", "points": 7.4}]
    match = so._real_trade_match("CRUDEOIL", "BUY_PE", "2026-09-21T09:48:08+00:00",
                                 "2026-09-21T09:49:12+00:00",
                                 list_scalp_signals=lambda symbol=None, limit=200: real_rows)
    assert match is not None and match["outcome"] == "WIN"


def test_real_trade_match_none_when_no_real_trade_exists():
    match = so._real_trade_match("NATURALGAS", "BUY_PE", "2026-09-21T09:50:00+00:00",
                                 "2026-09-21T09:59:58+00:00",
                                 list_scalp_signals=lambda symbol=None, limit=200: [])
    assert match is None


def test_setups_report_flags_real_trade_when_one_matches(store):
    rows = [_tick("2026-09-21T09:48:00+00:00", symbol="CRUDEOIL", strike=720.0,
                 option_token="T720", entry=719.4, stop_loss=715.8, target_1=726.43)]
    real_rows = [{"decision": "BUY_PE", "created_ts": "2026-09-21T09:48:08+00:00",
                 "status": "CLOSED", "outcome": "WIN", "entry": 719.4,
                 "exit_reason": "TARGET", "points": 7.4}]
    import app.db as _db_mod
    with patch.object(_db_mod, "list_shadow_decisions", lambda symbol=None, limit=200: rows), \
        patch.object(_db_mod, "list_scalp_signals", lambda **k: real_rows):
        out = so.setups_report(symbol="CRUDEOIL", store=store)
    assert len(out) == 1
    assert out[0]["real_trade"] is not None and out[0]["real_trade"]["outcome"] == "WIN"
