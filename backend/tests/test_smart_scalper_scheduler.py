"""
SLICE — live scheduler wiring (spec section 48).

SmartScalperScheduler is the previously-deferred piece: without it,
evaluate()/manage() only ran when an operator hit the API directly, so the
paper engine could go a whole session without a single trade even when the
engine itself was working. Offline: fresh temp DB, engine.manage/evaluate are
monkeypatched so no live_trading / no order path is exercised regardless.
"""
import asyncio
import os
import sys
import tempfile
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


def _run(coro):
    return asyncio.run(coro)


def test_defaults_to_disarmed_and_arm_disarm_persist(monkeypatch):
    db = _fresh(monkeypatch)
    from app.smart_index_scalper.scheduler import SmartScalperScheduler
    s = SmartScalperScheduler(profile="BALANCED", poll_sec=15)
    assert s.armed is False
    s.arm()
    assert s.armed is True and db.get_setting("smart_scalper_armed") == "1"
    s.disarm()
    assert s.armed is False and db.get_setting("smart_scalper_armed") == "0"


def test_tick_skips_when_market_closed_and_calls_manage_always(monkeypatch):
    db = _fresh(monkeypatch)
    from app.smart_index_scalper.scheduler import SmartScalperScheduler
    s = SmartScalperScheduler(profile="BALANCED", poll_sec=15)
    s._market_open = lambda: False
    calls = {"manage": 0, "evaluate": 0}
    s.engine.manage = lambda **kw: calls.__setitem__("manage", calls["manage"] + 1) or {"managed": []}
    s.engine.evaluate = lambda *a, **kw: calls.__setitem__("evaluate", calls["evaluate"] + 1) or {}
    _run(s.tick_once())
    assert s.last_result == {"skipped": "market_closed"}
    assert calls == {"manage": 0, "evaluate": 0}, "closed market must not call the engine at all"


def test_tick_manages_always_but_only_evaluates_when_armed(monkeypatch):
    db = _fresh(monkeypatch)
    from app.smart_index_scalper.scheduler import SmartScalperScheduler
    s = SmartScalperScheduler(profile="BALANCED", poll_sec=15)
    s._market_open = lambda: True
    calls = {"manage": 0, "evaluate": 0}
    s.engine.manage = lambda **kw: calls.__setitem__("manage", calls["manage"] + 1) or {"managed": []}
    s.engine.evaluate = lambda *a, **kw: calls.__setitem__("evaluate", calls["evaluate"] + 1) or {"primary": None}

    _run(s.tick_once())                          # disarmed
    assert calls == {"manage": 1, "evaluate": 0}
    assert s.last_result["evaluate"] == {"skipped": "disarmed"}

    s.arm()
    _run(s.tick_once())                          # armed
    assert calls == {"manage": 2, "evaluate": 1}
    assert s.last_result["evaluate"] == {"primary": None}
    assert s.ticks == 2


def test_evaluate_called_with_dry_run_false_when_armed(monkeypatch):
    db = _fresh(monkeypatch)
    from app.smart_index_scalper.scheduler import SmartScalperScheduler
    s = SmartScalperScheduler(profile="BALANCED", poll_sec=15)
    s._market_open = lambda: True
    s.engine.manage = lambda **kw: {"managed": []}
    seen = {}
    def fake_eval(symbols, **kw):
        seen.update(kw); seen["symbols"] = symbols
        return {"ok": True}
    s.engine.evaluate = fake_eval
    s.arm()
    _run(s.tick_once())
    assert seen.get("dry_run") is False, "an armed tick must pass dry_run=False (else it can never open a trade)"


def test_leader_election_only_leader_ticks(monkeypatch):
    db = _fresh(monkeypatch)
    from app.smart_index_scalper.scheduler import SmartScalperScheduler, LEASE_KEY
    a = SmartScalperScheduler(profile="BALANCED", poll_sec=15, owner="owner-a")
    b = SmartScalperScheduler(profile="BALANCED", poll_sec=15, owner="owner-b")
    assert db.lease_acquire(LEASE_KEY, "owner-a", 30) is True
    assert db.lease_acquire(LEASE_KEY, "owner-b", 30) is False   # a already holds it
    db.lease_release(LEASE_KEY, "owner-a")


def test_status_never_claims_live_trading():
    from app.smart_index_scalper.scheduler import SCHEDULER
    st = SCHEDULER.status()
    assert st["live_trading"] is False
    assert "armed" in st and "is_leader" in st and "poll_sec" in st


def test_scheduler_module_has_no_order_path():
    src = Path(__file__).parents[1] / "app" / "smart_index_scalper"
    joined = "\n".join(p.read_text() for p in src.glob("*.py"))
    for banned in ("place_order", "placeOrder", "OrderManager", "live_trading = true",
                   "live_trading=true", "LIVE_TRADING = True"):
        assert banned not in joined, banned
    assert "from ..engines.paper_trading import" in joined
    assert "Safeguards" in joined


def test_start_stop_lifecycle_is_idempotent_and_releases_lease(monkeypatch):
    db = _fresh(monkeypatch)
    from app.smart_index_scalper.scheduler import SmartScalperScheduler, LEASE_KEY

    async def go():
        s = SmartScalperScheduler(profile="BALANCED", poll_sec=60, owner="owner-x")
        s._market_open = lambda: False       # keep ticks cheap/no-op
        s.start()
        s.start()                            # idempotent — must not spawn a 2nd task
        await asyncio.sleep(0.05)
        assert s.is_leader is True
        await s.stop()
        assert db.lease_owner(LEASE_KEY) != "owner-x"
    _run(go())
