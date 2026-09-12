"""
app/microstructure/api.py -- read-only routes. Direct handler calls (this
repo's convention).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.microstructure import api  # noqa: E402


def _fake_h1h7(events):
    return lambda symbol, session_date, *, tf="5m", only_last=False, persist=True: {
        "symbol": symbol, "events": events, "bar_count": len(events),
    }


def test_status_returns_a_translated_state(monkeypatch):
    from app.microstructure import evaluator
    monkeypatch.setattr(evaluator.orderflow_service, "h1h7_state",
                        _fake_h1h7([{"state": "H1_CONT"}]))
    out = api.status("NIFTY", "2026-09-01")
    assert out["state"] == "BREAKOUT"


def test_status_without_option_context_by_default(monkeypatch):
    from app.microstructure import evaluator
    monkeypatch.setattr(evaluator.orderflow_service, "h1h7_state",
                        _fake_h1h7([{"state": "NEUTRAL"}]))
    out = api.status("NIFTY", "2026-09-01")
    assert "option_context" not in out


def test_status_with_option_context_falls_back_gracefully_on_fetch_error(monkeypatch):
    from app.microstructure import evaluator
    monkeypatch.setattr(evaluator.orderflow_service, "h1h7_state",
                        _fake_h1h7([{"state": "NEUTRAL"}]))
    # no real chain data available in this test env -> get_chain will raise/fail;
    # the route must still return a usable result, just without option_context
    out = api.status("NIFTY", "2026-09-01", with_option_context=1, live=0)
    assert out["state"] == "BALANCED"
