"""
Global kill switch (app/execution/killswitch.py) — the emergency stop that
OrderManager.prearm/submit must honour. Offline: fresh temp DB per test.
"""
import os
import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))


def _fresh(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("CHANAKYA_DB_PATH", os.path.join(d, "t.db"))
    import importlib
    import app.db as db
    importlib.reload(db)
    db.init_db()
    return db


def test_defaults_inactive_monitor_policy(monkeypatch):
    _fresh(monkeypatch)
    from app.execution import killswitch
    st = killswitch.state()
    assert st["active"] is False
    assert st["policy"] == "MONITOR"
    assert killswitch.is_active() is False
    assert killswitch.policy() == "MONITOR"


def test_activate_persists_and_is_active_true(monkeypatch):
    _fresh(monkeypatch)
    from app.execution import killswitch
    killswitch.activate(reason="test halt")
    assert killswitch.is_active() is True
    st = killswitch.state()
    assert st["reason"] == "test halt"
    assert st["ts"] is not None


def test_deactivate_round_trip(monkeypatch):
    _fresh(monkeypatch)
    from app.execution import killswitch
    killswitch.activate(reason="halt")
    killswitch.deactivate(reason="resume")
    assert killswitch.is_active() is False
    assert killswitch.state()["reason"] == "resume"


def test_activate_flatten_policy(monkeypatch):
    _fresh(monkeypatch)
    from app.execution import killswitch
    killswitch.activate(reason="flatten now", pol="FLATTEN")
    assert killswitch.policy() == "FLATTEN"
    assert killswitch.is_active() is True


def test_invalid_policy_falls_back_to_monitor(monkeypatch):
    _fresh(monkeypatch)
    from app.execution import killswitch
    killswitch.activate(reason="x", pol="NOT_A_REAL_POLICY")
    assert killswitch.policy() == "MONITOR"


def test_set_policy_preserves_active_state(monkeypatch):
    _fresh(monkeypatch)
    from app.execution import killswitch
    killswitch.activate(reason="halt")
    killswitch.set_policy("FLATTEN")
    st = killswitch.state()
    assert st["active"] is True
    assert st["policy"] == "FLATTEN"


def test_activate_without_policy_keeps_previous_policy(monkeypatch):
    _fresh(monkeypatch)
    from app.execution import killswitch
    killswitch.activate(reason="a", pol="FLATTEN")
    killswitch.deactivate()
    killswitch.activate(reason="b")   # no pol passed -- must keep FLATTEN, not reset to MONITOR
    assert killswitch.policy() == "FLATTEN"


def test_state_changes_write_order_events(monkeypatch):
    db = _fresh(monkeypatch)
    from app.execution import killswitch
    killswitch.activate(reason="audit-check")
    events = db.list_order_events(limit=10)
    kinds = [e["kind"] for e in events]
    assert "KILL_SWITCH_ON" in kinds
    killswitch.deactivate(reason="resume-check")
    kinds2 = [e["kind"] for e in db.list_order_events(limit=10)]
    assert "KILL_SWITCH_OFF" in kinds2
