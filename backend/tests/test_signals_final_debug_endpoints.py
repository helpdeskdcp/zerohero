"""/api/signals/final (only the active APPROVED signal, if any) and
/api/signals/debug (raw candidate audit trail) -- section 12/18 of the
final-signal-gate spec."""
from app.api import autoscalp_routes as routes
from app.signal_gate.final_signal_gate import _ACTIVE_SIGNAL, mark_active, mark_resolved


def setup_function(_):
    _ACTIVE_SIGNAL.clear()


def test_signals_final_is_empty_when_nothing_approved(fresh_db):
    r = routes.api_signals_final()
    assert r["active_signals"] == []
    assert "gate_enabled" in r


def test_signals_final_reports_an_active_approved_signal(fresh_db):
    mark_active("NIFTY", "BULLISH", "fp123")
    r = routes.api_signals_final()
    syms = {a["symbol"] for a in r["active_signals"]}
    assert "NIFTY" in syms


def test_signals_final_drops_a_resolved_signal(fresh_db):
    mark_active("NIFTY", "BULLISH", "fp123")
    mark_resolved("NIFTY")
    r = routes.api_signals_final()
    assert r["active_signals"] == []


def test_signals_debug_returns_raw_signal_rows(fresh_db):
    assert routes.api_signals_debug(limit=50) == []
