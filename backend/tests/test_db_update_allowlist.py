"""
Regression tests for the column-name allowlist added to db.update_trade /
db.update_smart_scalper_signal (ZEROHERO_FULL_AUDIT_2026-09-19.md LOW
finding). Both functions build their UPDATE ... SET clause's column NAMES
from a dict's keys via an f-string -- values are always parameterized, but
names can't be. Every current caller passes a hardcoded literal dict, so
this was never exploitable, but nothing stopped a future caller from
forwarding request-controlled keys. These tests prove the allowlist
actually rejects an unknown column instead of silently building whatever
SQL a caller's dict keys happen to spell.
"""
import pytest


def test_update_trade_accepts_real_columns(fresh_db):
    db = fresh_db
    db.insert_trade({"trade_id": "T1", "status": "OPEN"})
    db.update_trade("T1", {"stop_loss": 123.4, "pnl": 5.0})
    row = db.get_trade("T1")
    assert row["stop_loss"] == 123.4
    assert row["pnl"] == 5.0


def test_update_trade_rejects_unknown_column(fresh_db):
    db = fresh_db
    db.insert_trade({"trade_id": "T2", "status": "OPEN"})
    with pytest.raises(ValueError, match="unknown column"):
        db.update_trade("T2", {"trade_id=1;--": "x"})
    # nothing was applied -- the row is untouched
    row = db.get_trade("T2")
    assert row["status"] == "OPEN"


def test_update_trade_rejects_mixed_valid_and_invalid_columns(fresh_db):
    """A single bad key in an otherwise-valid dict must reject the whole
    call, not silently apply the valid half."""
    db = fresh_db
    db.insert_trade({"trade_id": "T3", "status": "OPEN", "pnl": 0.0})
    with pytest.raises(ValueError, match="unknown column"):
        db.update_trade("T3", {"pnl": 99.0, "not_a_real_column": "x"})
    row = db.get_trade("T3")
    assert row["pnl"] == 0.0


def test_update_smart_scalper_signal_accepts_real_columns(fresh_db):
    db = fresh_db
    db.insert_smart_scalper_signal({"signal_id": "S1", "created_ts": "2026-09-19T00:00:00Z",
                                    "instrument": "NIFTY", "profile": "BALANCED"})
    db.update_smart_scalper_signal("S1", {"profile": "AGGRESSIVE"})
    rows = db.list_smart_scalper_signals(limit=10)
    assert any(r["signal_id"] == "S1" and r["profile"] == "AGGRESSIVE" for r in rows)


def test_update_smart_scalper_signal_rejects_unknown_column(fresh_db):
    db = fresh_db
    db.insert_smart_scalper_signal({"signal_id": "S2", "created_ts": "2026-09-19T00:00:00Z",
                                    "instrument": "NIFTY", "profile": "BALANCED"})
    with pytest.raises(ValueError, match="unknown column"):
        db.update_smart_scalper_signal("S2", {"signal_id=1;--": "x"})
