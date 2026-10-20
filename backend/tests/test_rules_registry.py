"""Registry drift tests: the inventory must match the code. A new env flag, a renamed gate or a changed default FAILS here until rules.yaml is updated."""
import os

import pytest

from app.rules_registry import report, scan as S

REG = S.load_registry()
RESULT = S.scan()
MODES = {"ENFORCED", "ADVISORY", "OFF_BY_DEFAULT", "SHADOW", "DARK", "OFFLINE"}
EVIDENCE = {"SAFETY_RAIL", "UNVALIDATED", "DERIVED_FROM_LIVE_OUTCOMES", "NEGATIVE", "VALIDATED_OOS", "DRAFT"}
EFFECTS = {"BLOCKS_ORDERS", "BLOCKS_ENTRY", "FILTERS_SIGNAL", "LIMITS", "ADVISORY", "OFFLINE_GATE", "CLASSIFIER"}


def test_schema_and_unique_ids():
    ids = [r["id"] for r in REG["rules"]]
    assert len(ids) == len(set(ids)) and len(ids) >= 30
    for r in REG["rules"]:
        assert r["mode"] in MODES and r["effect"] in EFFECTS and r["evidence"]["kind"] in EVIDENCE, r["id"]
        assert r["where"]["file"].startswith("app/") and r["layer"] and r["name"], r["id"]
        for p in r.get("params") or []: assert {"name", "concept", "value", "source"} <= set(p), (r["id"], p)


def test_no_drift_between_registry_and_code():
    assert RESULT["problems"] == [], RESULT["problems"]
    for r in RESULT["rules"]:
        assert r["checks"]["file_exists"], r["id"]
        assert r["checks"]["symbol_exists"] is not False, f"{r['id']}: symbol {r['where']['symbol']} not found"
        for p in r["checks"]["params"]: assert p["match"], f"{r['id']}.{p['name']}: registry {p['registered']!r} vs code {p['actual']!r} ({p['error']})"


def test_every_env_flag_is_classified():
    assert RESULT["unclassified_env"] == [], f"classify these env flags in rules.yaml (rule-relevant -> env_flags with a rule): {RESULT['unclassified_env']}"


def test_safety_switches_point_at_real_rules():
    ids = {r["id"] for r in REG["rules"]}
    for e in REG["env_flags"]:
        if e.get("rule"): assert e["rule"] in ids, e
    sw = {e["name"] for e in REG["env_flags"] if e["category"] == "SAFETY_SWITCH"}
    assert {"CHANAKYA_ALLOW_LIVE", "CHANAKYA_LIVE_CONFIRM_TOKEN"} <= sw


def test_live_gate_defaults_block():
    env = {e["name"]: e for e in RESULT["env"]}
    assert env["CHANAKYA_ALLOW_LIVE"]["defaults"] == [None]                                       # unset => blocked
    rule = next(r for r in REG["rules"] if r["id"] == "EXEC-LIVE-GATE")
    assert rule["mode"] == "ENFORCED" and rule["effect"] == "BLOCKS_ORDERS"


def test_no_enforced_rule_claims_alpha_validation():
    for r in REG["rules"]:
        if r["mode"] == "ENFORCED": assert r["evidence"]["kind"] != "VALIDATED_OOS", f"{r['id']}: no rule has independent OOS evidence yet; update only with a real report ref"


def test_validated_claims_need_refs():
    for r in REG["rules"]:
        if r["evidence"]["kind"] in ("VALIDATED_OOS", "SAFETY_RAIL") and r["mode"] == "ENFORCED" and r["id"].startswith(("EXEC-", "HOF-POLICY-EXECUTION")):
            assert r["evidence"].get("refs"), f"{r['id']}: safety rail without a test ref"


def test_conflicts_are_detected_and_reported():
    c = {x["concept"]: x for x in RESULT["conflicts"]}
    assert c["max_trades_per_day"]["severity"] == "ENFORCED_CONFLICT" and c["max_consecutive_losses"]["severity"] == "ENFORCED_CONFLICT"
    assert c["daily_loss_cap"]["severity"] == "REVIEW_UNITS"
    md = report.render(RESULT)
    assert "max_trades_per_day" in md and "NOT validated as alpha" in md and "Coverage gaps" in md


def test_scanner_detects_a_tampered_registry(tmp_path):
    import yaml
    bad = yaml.safe_load(open(os.path.join(S.HERE, "rules.yaml")))
    next(r for r in bad["rules"] if r["id"] == "SG-MAX-TRADES")["params"][0]["value"] = 999
    bad["rules"][0]["where"]["symbol"] = "no_such_function"
    bad["env_flags"] = [e for e in bad["env_flags"] if e["name"] != "PA_OF_LAB"]
    p = tmp_path / "bad.yaml"; p.write_text(yaml.safe_dump(bad))
    r = S.scan(str(p))
    assert any("SG-MAX-TRADES" in x for x in r["problems"]) and any("EXEC-LIVE-GATE" in x for x in r["problems"])
    assert "PA_OF_LAB" in r["unclassified_env"]


def test_api_read_only_and_kill_switch(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.rules_registry import api
    app = FastAPI(); app.include_router(api.router); c = TestClient(app)
    j = c.get("/api/rules/inventory").json(); assert len(j["rules"]) == len(REG["rules"]) and j["problems"] == []
    assert "ZeroHero rules inventory" in c.get("/api/rules/report").text
    assert c.post("/api/rules/inventory").status_code == 405
    monkeypatch.setenv("RULES_REGISTRY", "0"); assert c.get("/api/rules/inventory").status_code == 503


def test_registry_modules_have_no_write_or_network_calls():
    d = os.path.dirname(S.__file__)
    for f in ("scan.py", "report.py", "api.py"):
        s = open(os.path.join(d, f)).read()
        for bad in ("requests", "urllib", "socket", "subprocess", "os.remove", "shutil.rmtree", "execute(", "app.execution"): assert bad not in s, (f, bad)
