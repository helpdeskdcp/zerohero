"""app.reverse_engineering.groq_verify -- downgrade-only merge guarantee,
mocked groq client (no real network calls)."""
from app.reverse_engineering import groq_verify as gv
from app.ai.groq_client import AIResult


def test_config_required_leaves_engine_state_unchanged(monkeypatch):
    from app.ai import groq_client
    monkeypatch.setattr(groq_client, "is_available", lambda: False)
    result = gv.verify({"state": "CONFIRMED"})
    assert result["status"] == "CONFIG_REQUIRED"
    assert result["final_state"] == "CONFIRMED"


def test_agreement_keeps_confirmed():
    merged = gv.merge_verification({"state": "CONFIRMED"},
                                   {"agrees_with_engine": True, "contradiction_found": False,
                                    "contradiction_note": "", "confidence": 80})
    assert merged["final_state"] == "CONFIRMED"


def test_contradiction_downgrades_confirmed_to_review():
    merged = gv.merge_verification({"state": "CONFIRMED"},
                                   {"agrees_with_engine": False, "contradiction_found": True,
                                    "contradiction_note": "OI disagrees", "confidence": 60})
    assert merged["final_state"] == "REVIEW"


def test_contradiction_downgrades_setup_forming_to_review():
    merged = gv.merge_verification({"state": "SETUP_FORMING"},
                                   {"agrees_with_engine": False, "contradiction_found": True,
                                    "contradiction_note": "x", "confidence": 50})
    assert merged["final_state"] == "REVIEW"


def test_contradiction_on_no_trade_never_upgrades():
    merged = gv.merge_verification({"state": "NO_TRADE"},
                                   {"agrees_with_engine": False, "contradiction_found": True,
                                    "contradiction_note": "x", "confidence": 90})
    assert merged["final_state"] == "NO_TRADE"


def test_contradiction_on_invalidated_never_upgrades():
    merged = gv.merge_verification({"state": "INVALIDATED"},
                                   {"agrees_with_engine": False, "contradiction_found": True,
                                    "contradiction_note": "x", "confidence": 90})
    assert merged["final_state"] == "INVALIDATED"


def test_verify_end_to_end_ok_response(monkeypatch):
    from app.ai import groq_client
    monkeypatch.setattr(groq_client, "is_available", lambda: True)
    monkeypatch.setattr(groq_client, "chat_completion_json", lambda **k: AIResult(
        status="OK", data={"agrees_with_engine": False, "contradiction_found": True,
                          "contradiction_note": "volume disagrees", "confidence": 55},
        model_used="test/model", latency_ms=5.0))
    result = gv.verify({"state": "CONFIRMED"}, regime="TREND_UP", underlying="NIFTY")
    assert result["status"] == "OK"
    assert result["final_state"] == "REVIEW"


def test_verify_schema_invalid_leaves_engine_state_unchanged(monkeypatch):
    from app.ai import groq_client
    monkeypatch.setattr(groq_client, "is_available", lambda: True)
    monkeypatch.setattr(groq_client, "chat_completion_json",
                        lambda **k: AIResult(status="OK", data={"only_one_key": True},
                                            model_used="m", latency_ms=1.0))
    result = gv.verify({"state": "CONFIRMED"})
    assert result["status"] == "SCHEMA_INVALID"
    assert result["final_state"] == "CONFIRMED"


def test_payload_is_compact_not_a_raw_dump():
    payload = gv.build_verification_payload(
        {"state": "CONFIRMED", "confidence_bucket": "HIGH", "evidence_for": ["price_action"],
         "evidence_against": [], "missing_evidence": ["oi"]},
        regime="TREND_UP", underlying="NIFTY")
    assert set(payload.keys()) == {"underlying", "regime", "engine_state",
                                   "engine_confidence_bucket", "evidence_for",
                                   "evidence_against", "missing_evidence"}
