"""
On-demand orchestrator for the Institutional Edge layer's read-only API.

Same posture as app.structural_break.evaluator: nothing here runs on a
schedule, each call is triggered by an HTTP GET, and state machines live in
a process-lifetime in-memory registry (durable history is in store.py's own
DB, not this registry -- a restart loses in-memory hysteresis but never
loses a past evaluation record).

EV is computed over the CONDITIONAL subset (the rows where the named
hypothesis actually held), not the unconditional baseline -- "if this
condition holds, what's the expected value of acting on it" is the
question section 12 is actually asking; conditional_edge.py's own baseline
comparison (the whole population) answers the separate question of whether
the condition is better than average.
"""
from __future__ import annotations

from .. import db
from . import conditions, ev
from .conditional_edge import conditional_edge
from .edge_score import EdgeStateMachine, compute_evidence
from .store import store

# Cost-model instrument identification -- only MCX NaturalGas/CrudeOil have
# a validated cost profile (costs.py); every other symbol below still routes
# through estimate_cost() and comes back honestly UNCALIBRATED rather than
# guessing an exchange/segment pairing that would look validated but isn't.
_INSTRUMENT_SEGMENTS = {
    "NATURALGAS": ("MCX", "NATURALGAS_OPTION"),
    "CRUDEOIL": ("MCX", "CRUDEOIL_OPTION"),
    "CRUDEOILM": ("MCX", "CRUDEOIL_OPTION"),
}


def _segment_for(instrument: str) -> tuple[str, str]:
    return _INSTRUMENT_SEGMENTS.get(str(instrument or "").upper(),
                                     ("UNKNOWN", f"{instrument}_OPTION"))


_machines: dict[tuple[str, str], EdgeStateMachine] = {}


def _machine_for(key: tuple[str, str]) -> EdgeStateMachine:
    if key not in _machines:
        _machines[key] = EdgeStateMachine()
    return _machines[key]


def evaluate_now(instrument: str, condition_label: str, *, source: str = "LIVE",
                  limit: int = 1000) -> dict:
    cond_fn = conditions.condition_fn(condition_label)
    rows = list(reversed(db.list_scalp_signals(source=source, symbol=instrument, limit=limit)))

    cond_result = conditional_edge(rows, cond_fn, condition_label=condition_label)

    usable = [r for r in rows if r.get("outcome") in ("WIN", "LOSS", "FLAT")]
    cond_rows = [r for r in usable if cond_fn(r)]
    exchange, segment = _segment_for(instrument)
    ev_result = ev.compute_ev(cond_rows, exchange=exchange, segment=segment)

    key = (str(instrument).upper(), condition_label)
    sm = _machine_for(key)
    out = sm.evaluate(cond_result, ev_result)
    evidence = compute_evidence(cond_result, ev_result)

    regime = rows[-1].get("regime") if rows else None
    store().log_evaluation(instrument=key[0], condition_label=condition_label,
                           conditional_result=cond_result, ev_result=ev_result,
                           evidence=evidence, state=out["state"], regime=regime)

    return {
        "instrument": key[0], "condition_label": condition_label,
        "n_rows_considered": len(rows), "regime": regime,
        "conditional_edge": cond_result.to_dict(), "ev": ev_result.to_dict(),
        **out,
    }


def tracked_edges() -> list[dict]:
    return [{"instrument": k[0], "condition_label": k[1], "state": sm.state,
            "n_evaluations": len(sm.history)} for k, sm in _machines.items()]
