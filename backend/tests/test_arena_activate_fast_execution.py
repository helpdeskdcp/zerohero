import importlib.util, json, os
import pytest

p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "arena_activate_fast_execution.py")
sp = importlib.util.spec_from_file_location("afe", p); M = importlib.util.module_from_spec(sp); sp.loader.exec_module(M)


def test_activation_writes_the_rule_keys_keeps_the_others_and_logs_the_change(tmp_path):
    rf = tmp_path / "rules.json"; rf.write_text(json.dumps({"sl_at_fill_from_ts": 1.5, "gap_chase_rules_from_ts": 2.5})); ch = tmp_path / "changes.md"; ch.write_text("# log\n")
    d = M.activate(3.0, now=1_800_000_000.0, rules_path=str(rf), changes_path=str(ch))
    r = json.loads(rf.read_text()); assert r["latency_schedule"] == [[1_800_000_000.0, 3.0]] and r["exit_now_latency_from_ts"] == 1_800_000_000.0
    assert r["sl_at_fill_from_ts"] == 1.5 and r["gap_chase_rules_from_ts"] == 2.5 and d == r                      # existing rules untouched
    assert "20 s -> 3 s" in ch.read_text() and "Never retroactive" in ch.read_text() and not (tmp_path / "rules.json.tmp").exists()
    with pytest.raises(AssertionError): M.activate(30.0, now=1.0, rules_path=str(rf), changes_path=None)         # may only shorten (<= 20 s)


def test_a_second_step_keeps_the_first_and_is_never_retroactive(tmp_path):
    from app.arena import settle as S
    rf = tmp_path / "rules.json"; rf.write_text(json.dumps({"latency_from_ts": 1000.0, "latency_sec": 3.0, "exit_now_latency_from_ts": 1000.0})); ch = tmp_path / "c.md"; ch.write_text("")
    M.activate(1.0, now=5000.0, rules_path=str(rf), changes_path=str(ch))
    r = json.loads(rf.read_text()); assert r["latency_schedule"] == [[1000.0, 3.0], [5000.0, 1.0]] and "3 s -> 1 s" in ch.read_text()          # the old single step was seeded, not lost
    old = S.RULES_FILE; S.RULES_FILE = str(rf)
    try:
        assert S.latency_at(500.0) == 20.0 and S.latency_at(1000.0) == 3.0 and S.latency_at(4999.9) == 3.0 and S.latency_at(5000.0) == 1.0 and S.latency_at(9e9) == 1.0
        assert S.latency_at(6000.0, default=0.5) == 0.5                                                              # only ever shortens
    finally: S.RULES_FILE = old
    with pytest.raises(AssertionError): M.activate(0.5, now=4000.0, rules_path=str(rf), changes_path=None)            # a step cannot start before the previous one


def test_latency_file_without_a_schedule_still_works(tmp_path):
    from app.arena import settle as S
    rf = tmp_path / "r.json"; rf.write_text(json.dumps({"latency_from_ts": 100.0, "latency_sec": 3.0})); old = S.RULES_FILE; S.RULES_FILE = str(rf)
    try: assert S.latency_at(50.0) == 20.0 and S.latency_at(150.0) == 3.0
    finally: S.RULES_FILE = old
