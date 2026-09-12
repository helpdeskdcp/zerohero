"""
Thin wrapper: fetch the real, already-captured bars via
app.orderflow.service.h1h7_state (never re-derived here), translate its
latest event through state.classify().

Stateless by design -- section 13 doesn't ask for a lifecycle/state-machine
like section 12's Edge Score does ("Every state requires evidence/reason
codes" reads as a per-observation classification, not something to track
hysteresis over) -- so this module holds no history and needs no store.

Optional option-chain context (`chain_analytics`, e.g. the dict from
app.optionchain.analytics.compute_all()) can be attached to the result as
supplementary evidence (OI walls, PCR, max pain distance) -- section 13's
"Evaluate: Price + OI + Volume, Price + OI + IV" -- WITHOUT letting it
override the price-action-derived state itself: combining a price-action
read and an option-chain read into one verdict without a validated
combination rule would manufacture false precision. The caller must fetch
that chain itself (this module never triggers a live/network chain fetch on
its own) -- keeps this read-only over already-captured data only.
"""
from __future__ import annotations

from ..orderflow import service as orderflow_service
from .state import MicrostructureState, classify


def evaluate(symbol: str, session_date: str, *, tf: str = "5m",
             chain_analytics: dict | None = None) -> dict:
    h1h7 = orderflow_service.h1h7_state(symbol, session_date, tf=tf, only_last=True)
    events = h1h7.get("events") or []
    latest_event = events[-1] if events else {}
    result: MicrostructureState = classify(latest_event)

    out = {
        "symbol": symbol.upper(), "session_date": session_date, "tf": tf,
        "bar_count": h1h7.get("bar_count", 0),
        **result.to_dict(),
    }
    if chain_analytics is not None:
        out["option_context"] = _summarize_chain(chain_analytics)
    return out


def _as_dict(obj) -> dict | None:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    to_dict = getattr(obj, "to_dict", None)
    return to_dict() if callable(to_dict) else None


def _summarize_chain(chain_analytics: dict) -> dict:
    """Purely descriptive extraction -- never used to change `state` above.
    `chain_analytics` is the dict app.optionchain.analytics.compute_all()
    returns (dataclass instances, each with its own .to_dict())."""
    pcr = _as_dict(chain_analytics.get("pcr")) or {}
    max_pain = _as_dict(chain_analytics.get("max_pain")) or {}
    walls = _as_dict(chain_analytics.get("oi_walls")) or {}
    return {
        "pcr_oi": pcr.get("pcr_oi"),
        "max_pain_strike": max_pain.get("max_pain_strike"),
        "max_pain_distance_pts": max_pain.get("distance_pts"),
        "nearest_resistance": walls.get("nearest_resistance"),
        "nearest_support": walls.get("nearest_support"),
    }
