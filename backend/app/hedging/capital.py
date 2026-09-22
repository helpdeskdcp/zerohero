"""
Paper capital ledger for the hedging engine. PAPER mode only in this
phase -- there is no live-capital concept here at all yet; that would be
a separate, explicitly-gated addition later (matching app.execution's own
triple-gate convention for LIVE anything). No broker call, no order.

Persisted via db.get_setting/set_setting (same pattern as app.combos),
under its own key so it never collides with autoscalp's or combos' state.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from .. import db

_KEY = "hedging_paper_capital"
DEFAULT_STARTING_CAPITAL = 50_000.0
DEFAULT_MAX_RISK_PCT = 0.02          # 2% of available capital per trade, not hardcoded per-call


@dataclass
class PaperCapitalState:
    starting_capital: float
    available_capital: float
    allocated_margin: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    updated_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def load() -> PaperCapitalState:
    """First call ever (no persisted state) starts fresh at
    DEFAULT_STARTING_CAPITAL -- never silently resets an existing ledger."""
    try:
        raw = db.get_setting(_KEY)
        if raw:
            d = json.loads(raw)
            return PaperCapitalState(**d)
    except Exception:
        pass
    return PaperCapitalState(starting_capital=DEFAULT_STARTING_CAPITAL,
                             available_capital=DEFAULT_STARTING_CAPITAL)


def save(state: PaperCapitalState) -> None:
    state.updated_at = datetime.now(timezone.utc).isoformat()
    db.set_setting(_KEY, json.dumps(state.to_dict()))


def reset(starting_capital: float = DEFAULT_STARTING_CAPITAL) -> PaperCapitalState:
    """Explicit-only -- never called implicitly by size_position/load."""
    state = PaperCapitalState(starting_capital=starting_capital, available_capital=starting_capital)
    save(state)
    return state


def size_position(*, available_capital: float, max_loss_per_lot: float,
                  max_risk_pct: float = DEFAULT_MAX_RISK_PCT,
                  max_hedge_cost_per_lot: float | None = None) -> dict:
    """Deterministic lot sizing -- NEVER a hardcoded lot count. Two
    independent caps, the tighter one wins:
      1. risk cap:    lots such that lots * max_loss_per_lot <= available_capital * max_risk_pct
      2. capital cap: lots such that lots * max_hedge_cost_per_lot <= available_capital
                       (can't spend more than you have, even under the risk cap)
    Returns 0 lots (never negative, never fractional) when either cap allows
    less than one full lot -- that is a real NO_TRADE-by-sizing outcome, not
    an error."""
    if available_capital <= 0 or max_loss_per_lot <= 0:
        return {"lots": 0, "reason": "no capital or non-positive max_loss_per_lot"}
    risk_budget = available_capital * max_risk_pct
    lots_by_risk = math.floor(risk_budget / max_loss_per_lot)
    lots = lots_by_risk
    if max_hedge_cost_per_lot and max_hedge_cost_per_lot > 0:
        lots_by_capital = math.floor(available_capital / max_hedge_cost_per_lot)
        lots = min(lots, lots_by_capital)
    lots = max(0, lots)
    return {
        "lots": lots,
        "risk_budget": round(risk_budget, 2),
        "lots_by_risk_cap": lots_by_risk,
        "lots_by_capital_cap": (math.floor(available_capital / max_hedge_cost_per_lot)
                                if max_hedge_cost_per_lot else None),
        "reason": None if lots > 0 else "risk/capital caps allow less than 1 full lot",
    }


def allocate(state: PaperCapitalState, *, margin: float, cost: float) -> PaperCapitalState:
    """Locks margin + spends premium cost for a new paper position. Never
    allows available_capital to go negative -- caller must size_position()
    first; this is the enforcement backstop, not the sizing logic itself."""
    spend = margin + cost
    if spend > state.available_capital:
        raise ValueError(f"allocate({spend}) exceeds available_capital({state.available_capital})")
    state.available_capital = round(state.available_capital - spend, 2)
    state.allocated_margin = round(state.allocated_margin + margin, 2)
    return state


def release(state: PaperCapitalState, *, margin: float, realized_pnl: float) -> PaperCapitalState:
    """Frees allocated margin and books realized P&L on a paper exit."""
    state.allocated_margin = round(max(0.0, state.allocated_margin - margin), 2)
    state.available_capital = round(state.available_capital + margin + realized_pnl, 2)
    state.realized_pnl = round(state.realized_pnl + realized_pnl, 2)
    return state
