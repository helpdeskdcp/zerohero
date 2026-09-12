"""
Gross / Net / Risk-adjusted EV -- section 12: "EV = P(win) x AverageWin -
P(loss) x AverageLoss. Calculate: Gross EV, Net EV, Risk-adjusted EV."

Same formula shape as the LIVE per-trade gate (engines/option_engine.py's
ev_gate), deliberately -- this is not a competing definition of EV, it's the
itemized, population-level (not per-trade) version for research: Gross EV
(no costs) vs Net EV (after this layer's own itemized costs.py, not
ev_gate's rough est_cost_r config haircut) vs Risk-adjusted EV (Net EV
normalized by the population's own average loss magnitude, the natural
"R" analog when working from historical outcomes rather than one trade's
defined stop distance).

Costs are converted from costs.py's rupees-per-lot into the same `points`
unit scalp_signals already uses (points x lot_size = rupee P&L, the
convention paper_trading.py's own quantity field already assumes), so Net
EV stays in the same unit as Gross EV -- never mixing points and rupees.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean

from . import costs

MIN_SAMPLE_N = 30


@dataclass
class EVResult:
    status: str                      # "OK" | "INSUFFICIENT_DATA" | "UNCALIBRATED_COST"
    n: int
    win_rate: float | None = None
    avg_win_points: float | None = None
    avg_loss_points: float | None = None
    gross_ev_points: float | None = None
    cost_points: float | None = None
    net_ev_points: float | None = None
    risk_adjusted_ev: float | None = None   # net_ev / avg_loss_points -- population "R"
    cost_note: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def compute_ev(rows: list[dict], *, exchange: str, segment: str,
                slippage_points: float = 0.0, min_n: int = MIN_SAMPLE_N) -> EVResult:
    """rows: scalp_signals-shaped dicts with `outcome` + `points`."""
    usable = [r for r in rows if r.get("outcome") in ("WIN", "LOSS", "FLAT")
              and r.get("points") is not None]
    if len(usable) < min_n:
        return EVResult(status="INSUFFICIENT_DATA", n=len(usable))

    n = len(usable)
    win_rate = sum(1 for r in usable if r["outcome"] == "WIN") / n
    wins = [float(r["points"]) for r in usable if r["outcome"] == "WIN"]
    losses = [-float(r["points"]) for r in usable if r["outcome"] == "LOSS"]   # positive magnitude
    avg_win = mean(wins) if wins else 0.0
    avg_loss = mean(losses) if losses else 0.0
    gross_ev = round(win_rate * avg_win - (1 - win_rate) * avg_loss, 4)

    cost = costs.estimate_cost(exchange, segment, slippage_points=slippage_points)
    if cost.status != "OK":
        return EVResult(status="UNCALIBRATED_COST", n=n, win_rate=round(win_rate, 4),
                        avg_win_points=round(avg_win, 4), avg_loss_points=round(avg_loss, 4),
                        gross_ev_points=gross_ev, cost_note=cost.note)

    net_ev = round(gross_ev - cost.total_cost_points, 4)
    risk_adjusted = round(net_ev / avg_loss, 4) if avg_loss > 0 else None

    return EVResult(status="OK", n=n, win_rate=round(win_rate, 4),
                    avg_win_points=round(avg_win, 4), avg_loss_points=round(avg_loss, 4),
                    gross_ev_points=gross_ev, cost_points=cost.total_cost_points,
                    net_ev_points=net_ev, risk_adjusted_ev=risk_adjusted,
                    cost_note=cost.note)
