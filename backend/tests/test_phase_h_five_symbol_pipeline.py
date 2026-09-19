"""
Phase H -- end-to-end pipeline validation for all 5 watchlist instruments:

    InstrumentProfile -> RegimeProfile -> select_effective_profile()
    -> behavior_ai -> openrouter_client -> fusion -> shadow decision

No real network call (no OPENROUTER_API_KEY exists in this environment --
verified separately in the Phase H report); AI-configured paths are
exercised via a mocked openrouter_client so the FUSION/SHADOW wiring is
proven end-to-end without a real API key.
"""
import json

import pytest

from app import instrument_profiles as ip
from app.effective_profile import select_effective_profile
from app.behavior_engine import analyze_behavior
from app.ai import openrouter_client as oc
from app.ai import behavior_ai
from app.ai import fusion as _fusion
from app.ai import shadow as sh

_SYMBOLS = ("NIFTY", "BANKNIFTY", "SENSEX", "NATURALGAS", "CRUDEOIL")


def _sig_for(symbol: str) -> dict:
    """Deterministic, realistic per-symbol test signal -- entry/atr scaled
    per instrument so volatility classification is sane, not copy-pasted
    NIFTY numbers onto a commodity."""
    scale = {"NIFTY": 22000.0, "BANKNIFTY": 48000.0, "SENSEX": 74000.0,
            "NATURALGAS": 280.0, "CRUDEOIL": 5800.0}[symbol]
    return {"decision": "BUY_CE", "regime": "TRENDING_UP", "signal_type": "RESISTANCE_BREAKOUT",
           "direction": "BULLISH", "mtf_alignment": 45.0, "signal_score": 68.0,
           "atr": scale * 0.01, "entry": scale}


@pytest.mark.parametrize("symbol", _SYMBOLS)
def test_instrument_profile_resolves_for_every_watchlist_symbol(symbol):
    prof = ip.get_instrument_profile(symbol)
    assert prof.symbol == symbol
    assert prof.lot_size.status == ip.ParamStatus.VERIFIED
    # No calibration value is invented for UNCALIBRATED-cost symbols
    if symbol in ("NIFTY", "BANKNIFTY", "SENSEX"):
        assert prof.cost_model_status == "UNCALIBRATED"
    else:
        assert prof.cost_model_status == "OK"


@pytest.mark.parametrize("symbol", _SYMBOLS)
def test_effective_profile_deterministic_for_every_symbol(symbol):
    a = select_effective_profile(symbol, "TRENDING_UP", is_expiry_day=False)
    b = select_effective_profile(symbol, "TRENDING_UP", is_expiry_day=False)
    assert a.to_dict() == b.to_dict()
    assert a.symbol == symbol


@pytest.mark.parametrize("symbol", _SYMBOLS)
def test_behavior_engine_produces_sane_output_for_every_symbol(symbol):
    b = analyze_behavior(symbol, _sig_for(symbol), ip.get_instrument_profile(symbol).cost_model_status)
    assert b.regime in ("TREND", "RANGE", "BREAKOUT", "REVERSAL", "CHOP")
    assert 0.0 <= b.behavior_confidence <= 100.0
    assert 0.0 <= b.signal_quality <= 100.0


@pytest.mark.parametrize("symbol", _SYMBOLS)
def test_full_pipeline_config_required_when_ai_unconfigured(symbol, fresh_db, monkeypatch):
    """No OPENROUTER_API_KEY exists in this environment -- the full chain
    must still complete and log a shadow decision, with ai_status
    reflecting the real config state, never a fabricated success."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    eff = select_effective_profile(symbol, "TRENDING_UP", False)
    result = sh.run_shadow_decision(symbol, _sig_for(symbol), eff.to_dict())
    assert result is not None
    rows = fresh_db.list_shadow_decisions(symbol=symbol)
    assert len(rows) == 1
    assert rows[0]["ai_status"] == "CONFIG_REQUIRED"
    assert rows[0]["fused_final_state"] in ("BUY", "WEAK_BUY", "STRONG_BUY")


@pytest.mark.parametrize("symbol", _SYMBOLS)
def test_full_pipeline_with_mocked_ai_success_for_every_symbol(symbol, fresh_db, monkeypatch):
    """Proves the complete chain -- including a real (mocked) OpenRouter
    round trip through behavior_ai -- reaches fusion and shadow correctly
    for every instrument, not just NIFTY."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake-test-only")
    monkeypatch.setenv("OPENROUTER_MODEL", "test/phase-h-model")
    ok_data = {"regime": "TREND", "profile_match": True, "profile_match_confidence": 70,
              "signal_validation": "PASS", "confidence": 65, "risk": "LOW",
              "warnings": [], "reason_codes": []}
    monkeypatch.setattr(oc, "chat_completion_json",
                        lambda **k: oc.AIResult(status="OK", data=ok_data,
                                               model_used="test/phase-h-model", latency_ms=22.0))
    eff = select_effective_profile(symbol, "TRENDING_UP", False)
    result = sh.run_shadow_decision(symbol, _sig_for(symbol), eff.to_dict())
    rows = fresh_db.list_shadow_decisions(symbol=symbol)
    assert rows[0]["ai_status"] == "OK"
    assert json.loads(rows[0]["ai_json"])["confidence"] == 65
    assert result["final_state"] in ("BUY", "WEAK_BUY", "STRONG_BUY")


def test_ai_failure_never_blocks_the_deterministic_pipeline(fresh_db, monkeypatch):
    """AI timeout/error must never propagate -- the shadow decision still
    gets written with a real failure status, not silently skipped, and
    never raises into the caller."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")
    monkeypatch.setenv("OPENROUTER_MODEL", "test/model")

    def _timeout(**k):
        return oc.AIResult(status="TIMEOUT", data=None, model_used=None, latency_ms=None,
                           error="simulated timeout")
    monkeypatch.setattr(oc, "chat_completion_json", _timeout)
    eff = select_effective_profile("NIFTY", "TRENDING_UP", False)
    result = sh.run_shadow_decision("NIFTY", _sig_for("NIFTY"), eff.to_dict())
    assert result is not None
    rows = fresh_db.list_shadow_decisions(symbol="NIFTY")
    assert rows[0]["ai_status"] == "TIMEOUT"
    # deterministic decision (BUY_CE) still stands -- AI failure is neutral
    assert result["final_state"] in ("BUY", "WEAK_BUY", "STRONG_BUY")


def test_no_broker_reference_anywhere_in_the_five_symbol_pipeline_modules():
    import inspect
    modules = [ip, sh, _fusion, behavior_ai]
    forbidden = ("place_order", "placeOrder", "modifyOrder", "cancelOrder", "SmartConnect")
    for mod in modules:
        src = inspect.getsource(mod)
        for f in forbidden:
            assert f not in src, f"found forbidden broker reference {f!r} in {mod.__name__}"
