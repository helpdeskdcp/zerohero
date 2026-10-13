"""Capital-aware personalization: ONE master signal, evaluated independently for each tenant.

A shared signal never bypasses an individual's risk checks: every tenant gets either an eligible,
sized contract or an explicit list of rejection reasons. Inputs are that tenant's own profile/state only
(no cross-tenant data is ever read here). PAPER mode only: live modes are refused (G6 is separately gated).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ...institutional_edge import costs as _costs
from .master import MasterSignal


@dataclass(frozen=True)
class TenantState:
    open_positions: int = 0
    realized_pnl_today: float = 0.0
    consecutive_losses: int = 0
    capital_available: float = 0.0


@dataclass
class PersonalSignal:
    tenant_id: str
    master_signal_id: str
    eligible: bool
    direction: str
    contract: dict | None = None
    band: str | None = None
    lots: int = 0
    qty: int = 0
    entry_premium: float | None = None         # the executable price: ASK for a buy (never LTP)
    premium_cost: float | None = None
    max_loss: float | None = None
    fees_round_trip: float | None = None
    cost_model_status: str = "UNCALIBRATED"
    sl_basis: str = "PREMIUM_FRACTION_ASSUMED"
    rejections: list = field(default_factory=list)   # tenant-level reasons
    contract_rejections: list = field(default_factory=list)   # per-candidate reasons (band, strike, reason)
    warnings: list = field(default_factory=list)


def personalize(master: MasterSignal, candidates: dict, tenant: dict, state: TenantState, *, lot_size: int,
                exchange: str, segment: str, slippage_points: float = 0.0, bands_order=("ATM", "ITM", "OTM"),
                default_sl_fraction: float | None = None) -> PersonalSignal:
    """tenant: a TenantStore profile dict (execution_mode, status, risk, virtual_capital)."""
    ps = PersonalSignal(tenant["tenant_id"], master.signal_id, False, master.direction)
    rej = ps.rejections
    if tenant.get("status") != "ACTIVE":
        rej.append("TENANT_SUSPENDED")
    if tenant.get("execution_mode") != "PAPER":
        rej.append("LIVE_NOT_AVAILABLE")                      # only PAPER exists until the separately gated live adapters
    if not master.data_ok:
        rej += ["DATA_NOT_OK"] + list(master.blocked_reasons)
    elif not master.tradable:
        rej.append("MASTER_SIGNAL_NO_TRADE")
    risk = tenant["risk"]
    cap = float(tenant["virtual_capital"])
    if state.open_positions >= int(risk["max_open_positions"]):
        rej.append("MAX_OPEN_POSITIONS")
    if state.realized_pnl_today <= -(float(risk["max_daily_loss_pct"]) / 100.0) * cap:
        rej.append("DAILY_LOSS_LIMIT_REACHED")
    if state.consecutive_losses >= int(risk["max_consecutive_losses"]):
        rej.append("CONSECUTIVE_LOSS_LIMIT_REACHED")
    if rej:
        return ps
    sl_frac = master.premium_sl_fraction if master.premium_sl_fraction else default_sl_fraction
    if not sl_frac:
        rej.append("NO_STOP_LOSS_BASIS")                     # cannot size risk without a stop: never guess one
        return ps
    est = _costs.estimate_cost(exchange, segment, slippage_points=slippage_points)
    ps.cost_model_status = est.status
    ps.fees_round_trip = est.total_cost if est.status == "OK" else None
    if est.status != "OK":
        ps.warnings.append("COST_MODEL_UNCALIBRATED")
    budget_loss = (float(risk["max_trade_loss_pct"]) / 100.0) * cap
    flat = []
    for band in bands_order:
        for c in candidates.get(band, []):
            if c.status == "ELIGIBLE":
                flat.append((c.score or 0.0, band, c))
            else:
                ps.contract_rejections.append({"band": band, "strike": c.strike, "reason": c.reject_reason})
    flat.sort(key=lambda t: -t[0])
    if not flat:
        rej.append("NO_ELIGIBLE_CONTRACT")
        return ps
    for score, band, c in flat:
        ask = c.raw.get("ask")
        if not isinstance(ask, (int, float)) or ask <= 0:
            ps.contract_rejections.append({"band": band, "strike": c.strike, "reason": "NO_EXECUTABLE_ASK"})
            continue
        per_lot_cost = ask * lot_size + (ps.fees_round_trip or 0.0)
        per_lot_loss = ask * sl_frac * lot_size + (ps.fees_round_trip or 0.0)
        lots_cash = math.floor(state.capital_available / per_lot_cost) if per_lot_cost > 0 else 0
        lots_loss = math.floor(budget_loss / per_lot_loss) if per_lot_loss > 0 else 0
        if lots_cash < 1:
            ps.contract_rejections.append({"band": band, "strike": c.strike, "reason": "INSUFFICIENT_CAPITAL",
                                           "needed": round(per_lot_cost, 2), "available": round(state.capital_available, 2)})
            continue
        if lots_loss < 1:
            ps.contract_rejections.append({"band": band, "strike": c.strike, "reason": "EXCEEDS_TRADE_LOSS_LIMIT",
                                           "loss_per_lot": round(per_lot_loss, 2), "budget": round(budget_loss, 2)})
            continue
        lots = min(lots_cash, lots_loss)
        ps.eligible, ps.contract, ps.band, ps.lots, ps.qty = True, c.raw, band, lots, lots * lot_size
        ps.entry_premium, ps.premium_cost = ask, round(ask * lots * lot_size, 2)
        ps.max_loss = round(per_lot_loss * lots, 2)
        return ps
    rej.append("NO_AFFORDABLE_CONTRACT_WITHIN_RISK_LIMITS")
    return ps
