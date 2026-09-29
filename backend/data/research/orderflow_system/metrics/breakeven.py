"""Break-even win-rate checks (spec B7, mandatory in every report).

Pure break-even WR for a reward:risk ratio RR (reward=RR*R, risk=1*R):
    WR * RR - (1 - WR) * 1 = 0  =>  WR = 1 / (1 + RR)
  RR=1 -> 50.0%, RR=2 -> 33.3%, RR=3 -> 25.0% -- matches the spec's own
  stated reference values exactly.

Cost-adjusted break-even WR additionally accounts for the average real cost
drag per trade (avg_cost_r = mean(r_multiple_gross - r_multiple_net) across
the actual trades, in R units -- from engine.costs' real formula estimate,
never invented):
    WR * RR - (1 - WR) * 1 - avg_cost_r = 0  =>  WR = (1 + avg_cost_r) / (1 + RR)
This is always >= the pure break-even WR (costs only ever raise the bar)."""
from __future__ import annotations


def pure_breakeven_wr(rr: float) -> float:
    return round(1.0 / (1.0 + rr), 4)


def cost_adjusted_breakeven_wr(rr: float, avg_cost_r: float) -> float:
    return round((1.0 + avg_cost_r) / (1.0 + rr), 4)


def breakeven_check(trades: list[dict], rr: float) -> dict:
    """PASS/FAIL for one (setup, RR) bucket -- actual net win rate vs the
    cost-adjusted break-even WR, with the margin. Never labels anything
    "profitable"; PASS only means "cleared the cost-adjusted break-even
    bar on this sample", nothing stronger."""
    n = len(trades)
    if n == 0:
        return {"n": 0, "verdict": "INSUFFICIENT_SAMPLE"}
    wins = sum(1 for t in trades if t["r_multiple_net"] > 0)
    actual_wr = wins / n
    avg_cost_r = sum(t["r_multiple_gross"] - t["r_multiple_net"] for t in trades) / n
    pure_be = pure_breakeven_wr(rr)
    cost_be = cost_adjusted_breakeven_wr(rr, avg_cost_r)
    margin = actual_wr - cost_be
    return {
        "n": n, "rr": rr, "actual_win_rate": round(actual_wr, 4),
        "pure_breakeven_wr": pure_be, "avg_cost_r": round(avg_cost_r, 4),
        "cost_adjusted_breakeven_wr": cost_be, "margin": round(margin, 4),
        "result": "PASS" if margin > 0 else "FAIL",
    }


def breakeven_table(trades_by_setup: dict[str, list[dict]], rr_targets: list[float]) -> dict:
    """{setup_tag: {rr: breakeven_check(...)}} -- the full PASS/FAIL table
    spec B7 requires printed in every report."""
    return {tag: {rr: breakeven_check(trades, rr) for rr in rr_targets}
           for tag, trades in trades_by_setup.items()}
