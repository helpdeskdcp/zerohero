"""app.ai.shadow.run_shadow_decision -- direct unit coverage (Phase G).
Real DB writes (fresh_db, isolated), mocked AI calls only -- no network."""
import json

from app.ai import groq_client as oc
from app.ai import shadow as sh


def _sig(**over):
    base = {"decision": "BUY_CE", "regime": "RANGE", "signal_type": "NONE",
           "direction": "BULLISH", "mtf_alignment": 20.0, "signal_score": 60.0,
           "atr": 5.0, "entry": 500.0}
    base.update(over)
    return base


def _eff(cost_status="UNCALIBRATED"):
    return {"cost_model_status": cost_status, "instrument_validation_status": "DEFAULT",
           "regime": "NORMAL_DAY"}


def test_config_required_when_no_key_configured(fresh_db, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = sh.run_shadow_decision("NIFTY", _sig(signal_type="RESISTANCE_BREAKOUT"), _eff())
    assert result is not None
    rows = fresh_db.list_shadow_decisions(symbol="NIFTY")
    assert len(rows) == 1
    assert rows[0]["ai_status"] == "CONFIG_REQUIRED"


def test_skipped_not_required_when_ai_configured_but_not_needed(fresh_db, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model-a")
    # RANGE regime, mtf_alignment=20 (outside the 35-65 ambiguous band) -> not ai_required
    sh.run_shadow_decision("NIFTY", _sig(regime="RANGE", signal_type="NONE", mtf_alignment=20.0), _eff())
    rows = fresh_db.list_shadow_decisions(symbol="NIFTY")
    assert rows[0]["ai_status"] == "SKIPPED_NOT_REQUIRED"


def test_ai_called_and_persisted_when_required_and_configured(fresh_db, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model-a")
    ok_data = {"regime": "TREND", "profile_match": True, "profile_match_confidence": 80,
              "signal_validation": "PASS", "confidence": 75, "risk": "LOW",
              "warnings": [], "reason_codes": []}
    monkeypatch.setattr(oc, "chat_completion_json",
                        lambda **k: oc.AIResult(status="OK", data=ok_data,
                                               model_used="test/model-a", latency_ms=15.0))
    result = sh.run_shadow_decision("BANKNIFTY", _sig(signal_type="RESISTANCE_BREAKOUT"), _eff())
    rows = fresh_db.list_shadow_decisions(symbol="BANKNIFTY")
    assert rows[0]["ai_status"] == "OK"
    assert json.loads(rows[0]["ai_json"])["confidence"] == 75
    assert rows[0]["fused_final_state"] in ("BUY", "WEAK_BUY", "STRONG_BUY")
    assert result["final_state"] == rows[0]["fused_final_state"]


def test_never_raises_when_ai_call_itself_errors(fresh_db, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model-a")

    def _boom(**k):
        raise RuntimeError("simulated internal failure")
    monkeypatch.setattr(oc, "chat_completion_json", _boom)
    result = sh.run_shadow_decision("CRUDEOIL", _sig(signal_type="RESISTANCE_BREAKOUT"), _eff("OK"))
    assert result is None    # best-effort: failed cleanly, no exception propagated
    assert fresh_db.list_shadow_decisions(symbol="CRUDEOIL") == []


def test_deterministic_no_trade_still_recorded_in_shadow_log(fresh_db, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    sh.run_shadow_decision("NIFTY", _sig(decision="NO_TRADE"), _eff())
    rows = fresh_db.list_shadow_decisions(symbol="NIFTY")
    assert rows[0]["fused_final_state"] == "NO_TRADE"
    assert rows[0]["deterministic_decision"] == "NO_TRADE"


def test_shadow_row_carries_profile_and_regime():
    # pure behavior mapping check reused by shadow -- no DB needed
    from app.behavior_engine import analyze_behavior
    b = analyze_behavior("SENSEX", _sig(signal_type="SUPPORT_REVERSAL"), "UNCALIBRATED")
    assert b.profile == "SENSEX"
    assert b.regime == "REVERSAL"
