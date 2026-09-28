"""Bridges data/research/*/ backtest trades (real entry/exit OPTION
premiums) to app.institutional_edge.costs's real cost models -- so a
re-backtest's "net of cost" number comes from the SAME cost logic the rest
of this codebase uses, not a duplicated/invented estimate per script."""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]  # .../backend
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.institutional_edge.costs import estimate_cost, estimate_index_option_cost  # noqa: E402


def net_pnl_index_option(trade: dict, lot_size: int, *, lots: int = 1,
                         slippage_points: float = 0.75) -> dict:
    """trade must have entry_premium/exit_premium (real captured or
    simulated option prices, never index points). Adds gross_pnl/cost_total/
    net_pnl/cost_status -- uses estimate_index_option_cost() ("FORMULA_ESTIMATE",
    NSE regulatory rates), never a fabricated cost."""
    entry_p, exit_p = trade["entry_premium"], trade["exit_premium"]
    gross = (exit_p - entry_p) * lot_size * lots
    cost = estimate_index_option_cost(entry_p, exit_p, lot_size, lots=lots, slippage_points=slippage_points)
    return {**trade, "gross_pnl": round(gross, 2), "cost_total": cost["total_cost"],
            "net_pnl": round(gross - cost["total_cost"], 2), "cost_status": cost["status"]}


def net_pnl_mcx_option(trade: dict, exchange: str, segment: str, lot_size: int, *,
                       slippage_points: float = 0.0) -> dict:
    """Same idea for a real-contract-note-validated MCX profile
    (estimate_cost(), status "OK")."""
    entry_p, exit_p = trade["entry_premium"], trade["exit_premium"]
    gross = (exit_p - entry_p) * lot_size
    cost = estimate_cost(exchange, segment, slippage_points=slippage_points)
    total_cost = cost.total_cost if cost.total_cost is not None else 0.0
    return {**trade, "gross_pnl": round(gross, 2), "cost_total": total_cost,
            "net_pnl": round(gross - total_cost, 2), "cost_status": cost.status}
