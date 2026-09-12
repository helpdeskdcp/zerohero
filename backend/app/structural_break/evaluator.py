"""
On-demand orchestrator for the Structural Break layer's read-only API.

Nothing here runs automatically or on a schedule -- no cron, no hook into
autoscalp/runner.py's live decision loop. Each call to `evaluate_now()` is
triggered by an HTTP GET (api.py) and does what a future scheduled runner
would do per tick: fetch the same rows every monitor needs (once, so every
monitor sees an identical row set -- performance_monitor.resolved_rows(),
the same fetch performance_monitor.py itself uses, avoiding a second,
possibly-inconsistent, re-derivation of that query), run the three monitors,
feed the state machine, and persist any resulting transition to the audit
log. Building an actual periodic scheduler that calls this without an
incoming request is a separate decision belonging to whoever wires this
layer into a live operational loop -- this file only makes each individual
evaluation possible and internally consistent.

State machines live in a process-lifetime, in-memory registry keyed by
regime_profiles.scope_key(). Restarting the process loses in-memory
hysteresis (history/streak counters) but never loses the record of past
transitions -- every one was already written to audit_log.py's durable
store the moment it happened, so `history()`/`why()` survive a restart even
though live continuity doesn't.
"""
from __future__ import annotations

from . import feature_drift, prediction_drift
from .audit_log import audit_log
from .break_score import StructuralBreakStateMachine
from .performance_monitor import PerformanceMonitor, resolved_rows
from .regime_profiles import scope_key

# Comfortably covers both feature_drift's default baseline+current (150+30)
# and prediction_drift's default baseline+min-eval (100+10) requirements.
_FETCH_LIMIT = 500

_machines: dict[str, StructuralBreakStateMachine] = {}
_monitor = PerformanceMonitor()


def _machine_for(key: str) -> StructuralBreakStateMachine:
    if key not in _machines:
        _machines[key] = StructuralBreakStateMachine()
    return _machines[key]


def evaluate_now(symbol: str, *, regime: str | None = None, source: str = "LIVE",
                  candidate_window: dict | None = None) -> dict:
    key = scope_key(symbol, regime)
    sm = _machine_for(key)

    rows = resolved_rows(source=source, symbol=symbol, regime=regime, limit=_FETCH_LIMIT)
    perf_report = _monitor.evaluate(symbol=symbol, regime=regime, source=source)
    feature_report = feature_drift.evaluate_feature_drift(rows)
    prediction_report = prediction_drift.evaluate_prediction_drift(rows)

    events_before = len(sm.events)
    out = sm.evaluate(perf_report, feature_report, prediction_report,
                       candidate_window=candidate_window)

    for ev in sm.events[events_before:]:
        audit_log().log_event(symbol, ev, current_regime=regime,
                               performance_metrics=perf_report, drift_metrics=feature_report,
                               adaptation_observations=out.get("adaptation_observations"))

    return {
        "symbol": symbol, "regime": regime, "scope_key": key,
        "n_rows_considered": len(rows),
        "performance": perf_report, "feature_drift": feature_report,
        "prediction_drift": prediction_report,
        **out,
    }


def tracked_scopes() -> list[dict]:
    """Every (symbol[, regime]) this process has evaluated at least once
    since startup, with its current state -- the read-only "what's being
    watched right now" overview for /status."""
    return [{"scope_key": k, "state": sm.state, "n_evaluations": len(sm.history)}
            for k, sm in _machines.items()]
