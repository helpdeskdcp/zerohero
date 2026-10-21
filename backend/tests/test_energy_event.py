from datetime import date, datetime

import pytest

from app.energy_event import rule as R, shadow as SH
from app.forward_test import ledger as L


def test_slots():
    assert R.release_slot(date(2026, 10, 22))[0] == (20, 0) and R.release_slot(date(2026, 10, 29))[0] == (20, 0)
    assert R.release_slot(date(2026, 11, 5))[0] == (21, 0) and R.release_slot(date(2026, 11, 19))[0] == (21, 0)
    assert R.release_slot(date(2026, 11, 26))[0] is None and R.release_slot(date(2026, 10, 21))[0] is None and R.release_slot(date(2027, 3, 11))[0] is None


def test_trigger_threshold_and_direction():
    b = lambda o, c: [(0, o, o, o, o), (60, o, o, o, o), (120, o, o, o, c)]
    assert R.trigger(b(100, 100.29)) is None and R.trigger(b(100, 100.30))["dir"] == "CE" and R.trigger(b(100, 99.69))["dir"] == "PE"
    assert R.trigger(b(100, 99.71)) is None


def test_plan_and_spread():
    assert R.plan(10.0) == {"sl": 7.0, "targets": [14.0]}
    assert R.spread_ok(9.8, 10.0) and not R.spread_ok(9.0, 10.0) and not R.spread_ok(None, 10.0)
    assert R.nearest_strike([280, 285, 290], 287.6) == 290 and R.nearest_strike([280, 290], 285) == 280


R_EP = datetime(2026, 10, 22, 20, 0, tzinfo=R.IST).timestamp()
CH = lambda: {"2026-10-23": {"strikes": [280.0, 285.0, 290.0]}}


def bars_up(k, r): return [(int(R_EP), 280.0, 281, 279.9, 280.5), (int(R_EP) + 60, 280.5, 282, 280, 281.5), (int(R_EP) + 120, 281.5, 282.5, 281, 281.2)]


def run(tmp_path, bars=bars_up, snaps=None, now=R_EP + 200, **kw):
    return SH.run_day(now, bars_fn=bars, key_fn=lambda: "MCX:1", chain_fn=CH, snaps_fn=snaps or (lambda: []), ledger=str(tmp_path / "a.jsonl"), calendar_fn=lambda: True, sleep=lambda s: None, **kw)


def test_signal_flow_and_idempotence(tmp_path):
    snaps = lambda: [{"t": R_EP + 150, "bid": 1, "ask": 2}, {"t": R_EP + 200, "bid": 9.8, "ask": 10.0}]
    r = run(tmp_path, snaps=snaps); assert r["status"] == "SIGNAL" and r["dir"] == "CE" and r["strike"] == 280.0
    s = r["signal"]; assert (s["entry"], s["sl"], s["targets"], s["participant"], s["entry_mode"]) == (10.0, 7.0, [14.0], "ZEROHERO_AI_EVENT", "MARKET") and s["events"][0]["t"] == R_EP + 1800
    assert run(tmp_path, snaps=snaps)["status"] == "ALREADY_RECORDED" and L.verify_chain(str(tmp_path / "a.jsonl"))[0]


def test_every_empty_branch_is_recorded(tmp_path):
    flat = lambda k, r: [(int(R_EP) + 60 * i, 280.0, 280.1, 279.9, 280.0) for i in range(3)]
    assert run(tmp_path, bars=flat)["status"] == "NO_TRIGGER"
    t2 = tmp_path / "b"; t2.mkdir()
    assert run(t2, bars=lambda k, r: [])["status"] == "INACTIVE"
    t3 = tmp_path / "c"; t3.mkdir()
    assert run(t3, snaps=lambda: [])["status"] == "NO_TRADE"
    t4 = tmp_path / "d"; t4.mkdir()
    assert run(t4, snaps=lambda: [{"t": R_EP + 200, "bid": 8.0, "ask": 10.0}])["reason"].startswith("spread")


def test_outside_window_and_non_thursday_write_nothing(tmp_path):
    assert run(tmp_path, now=R_EP + 60)["status"] == "OUTSIDE_WINDOW"
    assert SH.run_day(datetime(2026, 10, 21, 20, 5, tzinfo=R.IST).timestamp(), ledger=str(tmp_path / "x.jsonl"))["status"] == "NOT_THURSDAY" and not (tmp_path / "x.jsonl").exists()


def test_dense_capture_is_requested_for_the_chosen_contract_and_signal_is_exposed_for_scoring(tmp_path, monkeypatch):
    from app.histcap import priority
    calls = []; monkeypatch.setattr(priority, "request_watch", lambda *a, **k: calls.append(a))
    snaps = lambda: [{"t": R_EP + 200, "bid": 9.8, "ask": 10.0}]
    r = run(tmp_path, snaps=snaps); assert r["status"] == "SIGNAL" and calls == [("NATURALGAS", "2026-10-23", 280.0, "CE")]
    sigs = SH.scoring_signals("2026-10-01", "2026-12-31", path=str(tmp_path / "a.jsonl")); assert len(sigs) == 1 and sigs[0]["participant"] == "ZEROHERO_AI_EVENT" and sigs[0]["id"] == "EV-2026-10-22"
    assert SH.scoring_signals("2026-11-01", "2026-12-31", path=str(tmp_path / "a.jsonl")) == []
    from app.arena import leaderboard as LB
    assert "ZEROHERO_AI_EVENT" in LB.DEFAULT_DISPLAY and "separate" in LB.DEFAULT_DISPLAY["ZEROHERO_AI_EVENT"]
