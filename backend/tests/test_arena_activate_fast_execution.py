import importlib.util, json, os
import pytest

p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "arena_activate_fast_execution.py")
sp = importlib.util.spec_from_file_location("afe", p); M = importlib.util.module_from_spec(sp); sp.loader.exec_module(M)


def test_activation_writes_the_rule_keys_keeps_the_others_and_logs_the_change(tmp_path):
    rf = tmp_path / "rules.json"; rf.write_text(json.dumps({"sl_at_fill_from_ts": 1.5, "gap_chase_rules_from_ts": 2.5})); ch = tmp_path / "changes.md"; ch.write_text("# log\n")
    d = M.activate(3.0, now=1_800_000_000.0, rules_path=str(rf), changes_path=str(ch))
    r = json.loads(rf.read_text()); assert r["latency_from_ts"] == 1_800_000_000.0 and r["latency_sec"] == 3.0 and r["exit_now_latency_from_ts"] == 1_800_000_000.0
    assert r["sl_at_fill_from_ts"] == 1.5 and r["gap_chase_rules_from_ts"] == 2.5 and d == r                      # existing rules untouched
    assert "20 s -> 3 s" in ch.read_text() and "Never retroactive" in ch.read_text() and not (tmp_path / "rules.json.tmp").exists()
    with pytest.raises(AssertionError): M.activate(30.0, now=1.0, rules_path=str(rf), changes_path=None)         # may only shorten (<= 20 s)
