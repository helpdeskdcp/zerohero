import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.active_arm import run as AR, shadow as SH

IST = timezone(timedelta(hours=5, minutes=30))


def ts(day, hhmm): return datetime.fromisoformat(f"{day}T{hhmm}:00+05:30").timestamp()


def sigd(**kw):
    d = {"decision": "BUY_CE", "strike": 23000.0, "expiry": "27OCT2026", "entry": 100.0, "stop_loss": 85.0, "target_1": 120.0, "target_2": 140.0, "max_hold_sec": 1500.0, "regime": "RANGE", "signal_type": "SUPPORT_REVERSAL",
         "confidence": "MEDIUM", "probability": 0.55, "reason": "x"}
    d.update(kw); return d


def test_relaxed_changes_only_filters_and_never_mutates_input():
    cfg = {"symbol": "NIFTY", "strike_step": 50, "filters": {"block_regimes": ["RANGE"], "block_tod": ["AFTERNOON"]}, "sr_gates": {"enabled": False}, "profile": {"a": [1, 2]}}
    r = SH.relaxed(cfg)
    assert r["filters"] == {"block_regimes": [], "block_signal_types": [], "block_tod": []} and {k: v for k, v in r.items() if k != "filters"} == {k: v for k, v in cfg.items() if k != "filters"}
    assert cfg["filters"]["block_regimes"] == ["RANGE"]                                                            # original untouched
    r["profile"]["a"].append(3); assert cfg["profile"]["a"] == [1, 2]                                              # deep copy


def test_disabled_under_pytest_unless_forced_and_never_raises(tmp_path, monkeypatch):
    p = str(tmp_path / "a.db")
    assert SH.observe("NIFTY", {"decision": "NO_TRADE"}, {}, {}, [], atm=1, calib={}, avg_win=None, avg_loss=None, leg_bars_fn=None, tod=None, decide_fn=lambda *a, **k: sigd(), path=p) is False and not os.path.exists(p)
    monkeypatch.setenv("ACTIVE_ARM_SHADOW", "force")
    boom = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    assert SH.observe("NIFTY", {}, {}, {}, [], atm=1, calib={}, avg_win=None, avg_loss=None, leg_bars_fn=None, tod=None, decide_fn=boom, path=p) is False          # exceptions swallowed
    assert SH.observe("NIFTY", None, None, None, None, atm=1, calib=None, avg_win=None, avg_loss=None, leg_bars_fn=None, tod=None, path=p) is False


def test_record_labels_common_vs_active_only_and_dedups(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVE_ARM_SHADOW", "force"); SH._last.clear(); p = str(tmp_path / "a.db")
    fn = lambda *a, **k: sigd()
    frozen_block = {"decision": "NO_TRADE"}; frozen_take = {"decision": "BUY_CE"}
    kw = dict(atm=1, calib={}, avg_win=None, avg_loss=None, leg_bars_fn=None, tod="MORNING", decide_fn=fn, path=p)
    assert SH.observe("NIFTY", frozen_block, {}, {}, [], now=1000.0, **kw) is True
    assert SH.observe("NIFTY", frozen_block, {}, {}, [], now=1300.0, **kw) is False                                  # < 10 min, same (symbol, direction)
    assert SH.observe("NIFTY", frozen_take, {}, {}, [], now=1700.0, **kw) is True                                    # after 600 s
    assert SH.observe("BANKNIFTY", frozen_take, {}, {}, [], now=1300.0, **kw) is True
    rows = SH.load(p); assert [(r["symbol"], r["frozen_blocked"]) for r in rows] == [("NIFTY", 1), ("BANKNIFTY", 0), ("NIFTY", 0)]
    assert not SH.record("NIFTY", {"decision": "NO_TRADE"}, {}, None, path=p) and not SH.record("NIFTY", sigd(entry=None), {}, None, path=p)


def test_selection_daily_cap_and_one_open_per_underlying(tmp_path, monkeypatch):
    day = "2026-10-27"
    rows = [{"id": i + 1, "ts": ts(day, f"{10 + i // 6}:{(i % 6) * 10:02d}"), "symbol": "NIFTY", "decision": "BUY_CE", "strike": 23000.0, "expiry": "27OCT2026", "entry": 100.0, "stop_loss": 85.0, "target_1": 120.0, "target_2": 140.0,
             "max_hold_sec": 1500.0, "frozen_blocked": i % 2} for i in range(14)]
    called = []
    monkeypatch.setattr(AR.S, "load_snaps", lambda *a, **k: [])
    monkeypatch.setattr(AR.SM, "half_spread", lambda *a, **k: {"half_spread": 0.01, "source": "t"})
    monkeypatch.setattr(AR.S, "settle", lambda s, sn, **k: called.append(s["id"]) or {"status": "NO_DATA:test", "t": s["t"]})
    res = AR.select_and_settle(rows)
    # NO_DATA keeps the underlying busy for max_hold (25 min): signals are 10 min apart -> only every 3rd is taken (10:00, 10:30, 11:00, ...), daily cap 10 never reached here
    assert [r["t"] for r in res] == sorted(r["t"] for r in res) and len(res) == len({r["t"] for r in res}) and all(res[i + 1]["t"] - res[i]["t"] >= 1500 for i in range(len(res) - 1))
    many = [{**rows[0], "id": 100 + i, "symbol": f"U{i}", "ts": ts(day, "10:00") + i} for i in range(15)]
    called.clear(); res = AR.select_and_settle(many); assert len(res) == AR.MAX_PER_DAY and len(called) == AR.MAX_PER_DAY


def test_group_labels_time_exit_event_and_participant():
    r = {"id": 7, "ts": ts("2026-10-27", "10:00"), "symbol": "NIFTY", "decision": "BUY_PE", "strike": 23000.0, "expiry": "2026-10-27", "entry": 100.0, "stop_loss": 85.0, "target_1": 120.0, "target_2": None, "max_hold_sec": 1500.0, "frozen_blocked": 1}
    s = AR.to_signal(r)
    assert s["participant"] == "ZEROHERO_AI_ACTIVE" and s["group"] == "ACTIVE_ONLY" and s["option_type"] == "PE" and s["targets"] == [120.0] and s["events"] == [{"t": r["ts"] + 1500.0, "action": "EXIT_NOW"}]
    assert AR.to_signal({**r, "expiry": "garbage"}) is None


def test_settles_through_the_arena_engine_with_time_exit(tmp_path, monkeypatch):
    day = "2026-10-27"; t0 = ts(day, "10:00")
    snaps = [{"t": t0 + dt, "ltp": l, "bid": b, "ask": a} for dt, l, b, a in [(30, 100, 99, 100.5), (600, 104, 103, 105), (1400, 106, 105, 107), (1560, 108, 107, 109), (2000, 125, 124, 126)]]
    monkeypatch.setattr(AR.S, "load_snaps", lambda *a, **k: snaps); monkeypatch.setattr(AR.SM, "half_spread", lambda *a, **k: {"half_spread": 0.01, "source": "t"})
    row = {"id": 1, "ts": t0, "symbol": "NIFTY", "decision": "BUY_CE", "strike": 23000.0, "expiry": "27OCT2026", "entry": 100.0, "stop_loss": 85.0, "target_1": 130.0, "target_2": None, "max_hold_sec": 1500.0, "frozen_blocked": 0}
    out = AR.select_and_settle([row]); r = out[0]
    assert r["status"] == "SETTLED" and r["exit_reason"] == "EMERGENCY" and r["exit"] == 107 and r["group"] == "COMMON" and r["participant"] == "ZEROHERO_AI_ACTIVE"    # exit at the first bid after t0+1500+20s
    sm = AR.summarize(out); assert sm["selected"] == 1 and sm["verdict"] == "ACCUMULATING" and sm["groups"]["COMMON"]["settled"] == 1 and sm["groups"]["ACTIVE_ONLY"]["signals"] == 0


def test_runner_hook_is_guarded_log_only_and_barrier_holds():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "app", "autoscalp", "runner.py")).read()
    i = src.index("_arm_shadow.observe"); blk = src[i - 500:i + 450]
    assert "try:" in blk and "except Exception:" in src[i:i + 500] and src.index("sig[\"ev_mode\"] = _ev_mode") < i
    assert "arena" not in open(os.path.join(root, "app", "active_arm", "shadow.py")).read().lower()                  # the recorder knows nothing about the arena
    for f in ("shadow.py", "run.py"):
        s = open(os.path.join(root, "app", "active_arm", f)).read()
        for bad in ("requests", "urllib", "socket", "app.execution", "place_order", "subprocess"): assert bad not in s, (f, bad)


def test_arena_standings_include_the_labelled_active_arm(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.arena import api as A, run as R, submit as SU
    monkeypatch.setattr(SU, "PATH", str(tmp_path / "h.jsonl")); monkeypatch.setenv("ARENA_TOKEN_HUMAN", "tok"); monkeypatch.setenv("ARENA_START", "2999-01-01"); monkeypatch.setenv("ARENA_END", "2999-02-01")
    A._hits.clear(); A._cache.update(t=0.0, v=None)
    monkeypatch.setattr(R, "ai_signals", lambda *a, **k: []); monkeypatch.setattr(R, "human_signals", lambda *a, **k: [])
    app = FastAPI(); app.include_router(A.router)
    j = TestClient(app).get("/api/arena/public/standings", headers={"X-Arena-Token": "tok"}).json()
    assert "active" in j and j["active"]["verdict"] in ("ACCUMULATING", "UNAVAILABLE") and "ai" in j and "human" in j
