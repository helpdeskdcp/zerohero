"""
Conditional Edge -- section 12: Conditional Edge = P(outcome|condition) -
P(outcome|baseline).

`baseline` here is the UNCONDITIONAL population (every resolved row passed
in), not the complement of the condition -- "how much better does this
specific condition perform than the overall average" is what the spec's own
wording ("baseline") most naturally means, and it's what makes the number
useful: a positive edge says "this condition is a better-than-average
subset," which a complement-based comparison would not directly say.

Reuses the same scalp_signals-shaped row contract every other file in this
codebase's drift/performance layers already uses (probability, outcome,
points, mfe, regime, component_scores JSON, atr, pcr, ...) -- this module
adds nothing new to what's captured, it only re-slices it by an arbitrary
caller-supplied condition function.

Statistical honesty, consistent with this codebase's existing conventions
(structural_break's drift.py, calibration_report.py): a plain two-proportion
Wald confidence interval (no scipy -- not installed in this venv, matching
drift.py's own pure-Python KS implementation), and an explicit minimum
sample size below which the result is INSUFFICIENT_DATA rather than a
misleadingly precise number from a handful of rows. "Do not blindly combine
all features" (spec) is enforced by NOT providing any feature-search/
auto-combination helper here -- callers name one condition at a time.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from statistics import median, pvariance

MIN_SAMPLE_N = 30   # below this, a proportion estimate and its CI are unreliable enough to mislead


def _is_win(row: dict) -> bool:
    return row.get("outcome") == "WIN"


@dataclass
class ConditionalEdgeResult:
    status: str                          # "OK" | "INSUFFICIENT_DATA"
    condition_label: str
    n_condition: int
    n_baseline: int
    p_condition: float | None = None     # P(win | condition)
    p_baseline: float | None = None      # P(win), unconditional
    conditional_edge: float | None = None
    edge_ci95_low: float | None = None
    edge_ci95_high: float | None = None
    mean_return_condition: float | None = None
    median_return_condition: float | None = None
    variance_return_condition: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def conditional_edge(rows: list[dict], condition_fn, *, condition_label: str,
                      min_n: int = MIN_SAMPLE_N) -> ConditionalEdgeResult:
    """rows: scalp_signals-shaped dicts with usable `outcome` (and ideally
    `points`). condition_fn: row -> bool, applied to select the conditional
    subset; the baseline is ALL of `rows`."""
    usable = [r for r in rows if r.get("outcome") in ("WIN", "LOSS", "FLAT")]
    cond_rows = [r for r in usable if condition_fn(r)]
    n_cond, n_base = len(cond_rows), len(usable)

    if n_cond < min_n or n_base < min_n:
        return ConditionalEdgeResult(status="INSUFFICIENT_DATA", condition_label=condition_label,
                                      n_condition=n_cond, n_baseline=n_base)

    p_cond = sum(1 for r in cond_rows if _is_win(r)) / n_cond
    p_base = sum(1 for r in usable if _is_win(r)) / n_base
    edge = p_cond - p_base

    # two-proportion Wald 95% CI on the difference (SE combines both samples'
    # own binomial variance -- standard textbook formula, no scipy needed)
    se = math.sqrt(p_cond * (1 - p_cond) / n_cond + p_base * (1 - p_base) / n_base)
    ci_low, ci_high = round(edge - 1.96 * se, 4), round(edge + 1.96 * se, 4)

    pts = [float(r["points"]) for r in cond_rows if r.get("points") is not None]
    mean_ret = round(sum(pts) / len(pts), 4) if pts else None
    median_ret = round(median(pts), 4) if pts else None
    var_ret = round(pvariance(pts), 4) if len(pts) >= 2 else None

    return ConditionalEdgeResult(
        status="OK", condition_label=condition_label, n_condition=n_cond, n_baseline=n_base,
        p_condition=round(p_cond, 4), p_baseline=round(p_base, 4), conditional_edge=round(edge, 4),
        edge_ci95_low=ci_low, edge_ci95_high=ci_high,
        mean_return_condition=mean_ret, median_return_condition=median_ret,
        variance_return_condition=var_ret)
