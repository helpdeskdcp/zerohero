"""app.ai.behavior_ai (schema validation) and app.ai.fusion (authority
rules) -- all pure/mocked, no real network calls."""
from app.ai import behavior_ai
from app.ai.openrouter_client import AIResult
from app.ai.fusion import fuse_decision


# ---------------------------------------------------------------- behavior_ai

def _ok_client_result(**over):
    data = {"regime": "TREND", "profile_match": True, "profile_match_confidence": 80,
           "signal_validation": "PASS", "confidence": 75, "risk": "LOW",
           "warnings": [], "reason_codes": []}
    data.update(over)
    return AIResult(status="OK", data=data, model_used="test/model", latency_ms=12.0)


def test_analyze_with_ai_valid_response(monkeypatch):
    monkeypatch.setattr(behavior_ai._client, "chat_completion_json", lambda **k: _ok_client_result())
    r = behavior_ai.analyze_with_ai({"symbol": "NIFTY"})
    assert r.status == "OK"
    assert r.confidence == 75.0
    assert r.signal_validation == "PASS"


def test_analyze_with_ai_unavailable_passthrough(monkeypatch):
    monkeypatch.setattr(behavior_ai._client, "chat_completion_json",
                        lambda **k: AIResult(status="UNAVAILABLE", data=None, model_used=None,
                                            latency_ms=None, error="no key"))
    r = behavior_ai.analyze_with_ai({"symbol": "NIFTY"})
    assert r.status == "UNAVAILABLE"
    assert r.confidence is None
    assert r.risk == "UNKNOWN"


def test_analyze_with_ai_rejects_missing_keys(monkeypatch):
    bad = AIResult(status="OK", data={"regime": "TREND"}, model_used="m", latency_ms=1.0)
    monkeypatch.setattr(behavior_ai._client, "chat_completion_json", lambda **k: bad)
    r = behavior_ai.analyze_with_ai({"symbol": "NIFTY"})
    assert r.status == "SCHEMA_INVALID"


def test_analyze_with_ai_rejects_bad_enum_value(monkeypatch):
    result = _ok_client_result(risk="EXTREME")   # not in {LOW,MEDIUM,HIGH}
    monkeypatch.setattr(behavior_ai._client, "chat_completion_json", lambda **k: result)
    r = behavior_ai.analyze_with_ai({"symbol": "NIFTY"})
    assert r.status == "SCHEMA_INVALID"


def test_analyze_with_ai_rejects_non_numeric_confidence(monkeypatch):
    result = _ok_client_result(confidence="high")
    monkeypatch.setattr(behavior_ai._client, "chat_completion_json", lambda **k: result)
    r = behavior_ai.analyze_with_ai({"symbol": "NIFTY"})
    assert r.status == "SCHEMA_INVALID"


def test_build_ai_context_is_compact_not_a_raw_dump():
    ctx = behavior_ai.build_ai_context("NIFTY", {"regime": "TREND", "behavior_confidence": 60,
                                                  "signal_quality": 70, "risk_state": "NORMAL",
                                                  "trend": "UP", "volatility": "NORMAL"},
                                       {"cost_model_status": "OK", "instrument_validation_status": "DEFAULT",
                                        "regime": "NORMAL_DAY"},
                                       {"signal_type": "NONE", "direction": "BULLISH", "signal_score": 70})
    assert set(ctx.keys()) == {"symbol", "behavior_regime", "trend", "volatility",
                               "behavior_confidence", "signal_quality", "risk_state",
                               "deterministic_signal_type", "deterministic_direction",
                               "deterministic_score", "cost_model_status",
                               "instrument_validation_status", "regime_profile"}


# ---------------------------------------------------------------- fusion

def test_deterministic_no_trade_stays_no_trade_regardless_of_ai():
    d = fuse_decision(deterministic_decision="NO_TRADE", deterministic_score=90,
                      behavior={"regime": "TREND", "profile": "NIFTY", "risk_state": "NORMAL",
                               "signal_quality": 90},
                      ai_result={"status": "OK", "signal_validation": "PASS", "confidence": 95,
                                "risk": "LOW", "profile_match": True, "warnings": []},
                      cost_model_status="OK")
    assert d.final_state == "NO_TRADE"
    assert "deterministic_no_trade" in d.reason_codes


def test_ai_unavailable_leaves_deterministic_decision_unchanged():
    d = fuse_decision(deterministic_decision="BUY_CE", deterministic_score=70,
                      behavior={"regime": "TREND", "profile": "NIFTY", "risk_state": "NORMAL",
                               "signal_quality": 70},
                      ai_result={"status": "UNAVAILABLE"},
                      cost_model_status="OK")
    assert d.final_state == "BUY"
    assert any("ai_unavailable" in r for r in d.reason_codes)


def test_ai_signal_validation_fail_forces_no_trade():
    d = fuse_decision(deterministic_decision="BUY_CE", deterministic_score=70,
                      behavior={"regime": "TREND", "profile": "NIFTY", "risk_state": "NORMAL",
                               "signal_quality": 70},
                      ai_result={"status": "OK", "signal_validation": "FAIL", "confidence": 40,
                                "risk": "LOW", "profile_match": True, "warnings": []},
                      cost_model_status="OK")
    assert d.final_state == "NO_TRADE"
    assert "ai_signal_validation_fail" in d.reason_codes


def test_ai_high_risk_downgrades_but_never_flips_direction():
    d = fuse_decision(deterministic_decision="BUY_CE", deterministic_score=70,
                      behavior={"regime": "TREND", "profile": "NIFTY", "risk_state": "NORMAL",
                               "signal_quality": 70},
                      ai_result={"status": "OK", "signal_validation": "PASS", "confidence": 60,
                                "risk": "HIGH", "profile_match": True, "warnings": []},
                      cost_model_status="OK")
    assert d.final_state == "WEAK_BUY"      # downgraded one step, still on the buy side


def test_sell_side_downgrade_never_crosses_into_buy():
    d = fuse_decision(deterministic_decision="BUY_PE", deterministic_score=70,
                      behavior={"regime": "TREND", "profile": "NIFTY", "risk_state": "NORMAL",
                               "signal_quality": 70},
                      ai_result={"status": "OK", "signal_validation": "PASS", "confidence": 60,
                                "risk": "HIGH", "profile_match": True, "warnings": []},
                      cost_model_status="OK")
    assert d.final_state == "WEAK_SELL"


def test_ai_cannot_upgrade_a_downgraded_state_past_strong():
    """Even with a perfect AI opinion, fusion never invents a stronger state
    than the deterministic engine's own base BUY/SELL -- there is no
    upgrade path in this function at all, only downgrade-or-neutral."""
    d = fuse_decision(deterministic_decision="BUY_CE", deterministic_score=70,
                      behavior={"regime": "TREND", "profile": "NIFTY", "risk_state": "NORMAL",
                               "signal_quality": 70},
                      ai_result={"status": "OK", "signal_validation": "PASS", "confidence": 99,
                                "risk": "LOW", "profile_match": True, "warnings": []},
                      cost_model_status="OK")
    assert d.final_state == "BUY"     # not STRONG_BUY -- no upgrade mechanism exists


def test_uncalibrated_cost_model_is_flagged_in_reason_codes():
    d = fuse_decision(deterministic_decision="BUY_CE", deterministic_score=70,
                      behavior={"regime": "TREND", "profile": "NIFTY", "risk_state": "NORMAL",
                               "signal_quality": 70},
                      ai_result={"status": "UNAVAILABLE"},
                      cost_model_status="UNCALIBRATED")
    assert "cost_model_uncalibrated" in d.reason_codes
