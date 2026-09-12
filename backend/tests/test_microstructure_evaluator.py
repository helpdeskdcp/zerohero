"""
app/microstructure/evaluator.py -- thin wrapper over
orderflow.service.h1h7_state. The service call is monkeypatched (no real
captured bars needed) since this module's own job is just fetch-and-
translate, already covered unit-by-unit by test_microstructure_state.py and
existing orderflow tests.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.microstructure import evaluator  # noqa: E402
from app.optionchain.analytics import MaxPain, OiWalls, Pcr  # noqa: E402


def _fake_h1h7(events):
    return lambda symbol, session_date, *, tf="5m", only_last=False, persist=True: {
        "symbol": symbol, "events": events, "bar_count": len(events) or 5,
    }


def test_evaluate_translates_the_latest_event(monkeypatch):
    monkeypatch.setattr(evaluator.orderflow_service, "h1h7_state",
                        _fake_h1h7([{"state": "H7_TRAP"}]))
    out = evaluator.evaluate("NIFTY", "2026-09-01")
    assert out["symbol"] == "NIFTY"
    assert out["state"] == "REJECTION"
    assert out["source_h1h7_state"] == "H7_TRAP"


def test_evaluate_uses_the_last_event_when_multiple_exist(monkeypatch):
    monkeypatch.setattr(evaluator.orderflow_service, "h1h7_state",
                        _fake_h1h7([{"state": "NEUTRAL"}, {"state": "H1_CONT"}]))
    out = evaluator.evaluate("NIFTY", "2026-09-01")
    assert out["state"] == "BREAKOUT"


def test_evaluate_with_no_events_reports_unknown(monkeypatch):
    monkeypatch.setattr(evaluator.orderflow_service, "h1h7_state", _fake_h1h7([]))
    out = evaluator.evaluate("NIFTY", "2026-09-01")
    assert out["state"] == "UNKNOWN"


def test_evaluate_never_touches_execution_or_broker_state(monkeypatch):
    monkeypatch.setattr(evaluator.orderflow_service, "h1h7_state",
                        _fake_h1h7([{"state": "NEUTRAL"}]))
    out = evaluator.evaluate("NIFTY", "2026-09-01")
    forbidden = {"order_id", "broker", "execution", "credentials"}
    assert forbidden.isdisjoint(out.keys())


def test_option_context_attached_when_provided_but_never_changes_state(monkeypatch):
    monkeypatch.setattr(evaluator.orderflow_service, "h1h7_state",
                        _fake_h1h7([{"state": "NEUTRAL"}]))
    chain_analytics = {
        "pcr": Pcr(status="ok", pcr_oi=1.35),
        "max_pain": MaxPain(status="ok", max_pain_strike=24100.0, distance_pts=50.0),
        "oi_walls": OiWalls(status="ok", nearest_resistance=24200.0, nearest_support=24000.0),
    }
    out = evaluator.evaluate("NIFTY", "2026-09-01", chain_analytics=chain_analytics)
    assert out["state"] == "BALANCED"   # unchanged by option context
    assert out["option_context"]["pcr_oi"] == 1.35
    assert out["option_context"]["max_pain_strike"] == 24100.0
    assert out["option_context"]["nearest_resistance"] == 24200.0


def test_no_option_context_key_when_not_provided(monkeypatch):
    monkeypatch.setattr(evaluator.orderflow_service, "h1h7_state",
                        _fake_h1h7([{"state": "NEUTRAL"}]))
    out = evaluator.evaluate("NIFTY", "2026-09-01")
    assert "option_context" not in out
