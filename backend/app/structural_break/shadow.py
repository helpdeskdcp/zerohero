"""
Shadow model comparison -- section F of the spec.

What this file does NOT do: it does not run a second live model instance.
No re-parameterization/re-specification engine exists yet (spec section E's
steps 5-6, "collect new-regime data / re-specify / re-parameterize" are a
separate, much larger modeling task than a comparison framework, and are
honestly out of scope here).

What this file DOES provide: the comparison and promotion-decision framework
that break_score.py's own module docstring says the ADAPTATION -> VALIDATION
-> RECOVERED transitions are waiting on. Given the OLD model's resolved-trade
metrics and a CANDIDATE's resolved-trade metrics over the same shadow period
-- both computed by performance_monitor.py's identical WindowMetrics logic,
so the numbers mean the same thing on both sides regardless of what produced
the underlying rows -- this decides whether the candidate is good enough to
promote.

Promotion is capped at RECOVERED, a DARK/shadow state (adaptation.py's
CLEAR_STATES already lifts the Safeguards halt there). Actually deploying a
promoted candidate as the new live model remains a separate, manual step
outside this layer entirely -- spec K: never auto-deploy to live execution.
This file cannot and does not touch order execution, broker credentials, or
autoscalp/runner.py's live decision loop.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .performance_monitor import DEFAULT_WINDOWS

# Minimum candidate sample size before a promotion decision is even
# attempted -- reuses performance_monitor's own "medium" window size (60),
# the same "stable read" bar already established elsewhere in this codebase,
# rather than inventing a new number here.
MIN_SHADOW_N = DEFAULT_WINDOWS["medium"]

# Core metrics compared for promotion, and which direction counts as
# "better". Deliberately NOT win_rate alone -- spec J: promotion must reflect
# genuine improvement, not a metric that can look better while expectancy/PF
# quietly get worse. win_rate is still visible in the underlying window
# report, just not used as a promotion criterion by itself.
_METRIC_DIRECTIONS = {
    "expectancy_points": "higher",
    "profit_factor": "higher",
    "directional_accuracy": "higher",
    "ece": "lower",                 # lower calibration error is better
    "max_drawdown_points": "higher",  # less negative is better
}
# The bottom-line P&L metric -- a candidate that regresses on expectancy is
# never promoted regardless of how many other metrics improve.
_MUST_NOT_REGRESS = "expectancy_points"
# Simple majority of the 5 metrics, not unanimity (unanimity would make any
# single noisy metric a veto; a single metric alone can't force promotion
# either, since expectancy is checked separately and always required).
_MAJORITY_REQUIRED = 3


@dataclass
class MetricComparison:
    metric: str
    old: float | None
    candidate: float | None
    better_or_equal: bool | None   # None when either side didn't report this metric

    def to_dict(self):
        return asdict(self)


@dataclass
class ShadowComparison:
    status: str    # "OK" | "INSUFFICIENT_CANDIDATE_DATA" | "INSUFFICIENT_OLD_DATA"
    old_n: int
    candidate_n: int
    metrics: list
    metrics_better_or_equal: int
    metrics_compared: int
    expectancy_regressed: bool | None
    should_promote: bool
    reason: str

    def to_dict(self):
        return asdict(self)


def _cmp(name: str, old_val, cand_val) -> MetricComparison:
    direction = _METRIC_DIRECTIONS[name]
    if old_val is None or cand_val is None:
        return MetricComparison(metric=name, old=old_val, candidate=cand_val, better_or_equal=None)
    ok = (cand_val >= old_val) if direction == "higher" else (cand_val <= old_val)
    return MetricComparison(metric=name, old=old_val, candidate=cand_val, better_or_equal=ok)


def compare(old_window: dict, candidate_window: dict) -> ShadowComparison:
    """Pure function -- one shadow-comparison snapshot. Both args are
    WindowMetrics.to_dict()-shaped (performance_monitor.py); this function
    doesn't care what produced the underlying rows (LIVE old-model trades vs
    a candidate's shadow trades), only that both were scored by the same
    metric logic."""
    old_n, cand_n = old_window.get("n", 0), candidate_window.get("n", 0)

    if candidate_window.get("status") != "OK" or cand_n < MIN_SHADOW_N:
        return ShadowComparison(
            status="INSUFFICIENT_CANDIDATE_DATA", old_n=old_n, candidate_n=cand_n,
            metrics=[], metrics_better_or_equal=0, metrics_compared=0,
            expectancy_regressed=None, should_promote=False,
            reason=f"candidate n={cand_n} < MIN_SHADOW_N={MIN_SHADOW_N} (or status not OK) "
                   "-- not enough shadow trades yet to judge",
        )
    if old_window.get("status") != "OK":
        return ShadowComparison(
            status="INSUFFICIENT_OLD_DATA", old_n=old_n, candidate_n=cand_n,
            metrics=[], metrics_better_or_equal=0, metrics_compared=0,
            expectancy_regressed=None, should_promote=False,
            reason="old-model window has insufficient data to compare against",
        )

    comparisons = [_cmp(name, old_window.get(name), candidate_window.get(name))
                   for name in _METRIC_DIRECTIONS]
    scored = [c for c in comparisons if c.better_or_equal is not None]
    better_or_equal = sum(1 for c in scored if c.better_or_equal)

    expectancy_cmp = next(c for c in comparisons if c.metric == _MUST_NOT_REGRESS)
    expectancy_regressed = (expectancy_cmp.better_or_equal is False)

    should = bool(scored) and not expectancy_regressed and better_or_equal >= _MAJORITY_REQUIRED

    if expectancy_regressed:
        reason = (f"REJECTED: {_MUST_NOT_REGRESS} regressed vs old model "
                  f"(candidate={expectancy_cmp.candidate}, old={expectancy_cmp.old})")
    elif better_or_equal < _MAJORITY_REQUIRED:
        reason = (f"REJECTED: only {better_or_equal}/{len(scored)} metrics better-or-equal, "
                  f"need >= {_MAJORITY_REQUIRED}")
    else:
        reason = (f"PROMOTE: {better_or_equal}/{len(scored)} metrics better-or-equal, "
                  f"{_MUST_NOT_REGRESS} not regressed")

    return ShadowComparison(
        status="OK", old_n=old_n, candidate_n=cand_n,
        metrics=[c.to_dict() for c in comparisons],
        metrics_better_or_equal=better_or_equal, metrics_compared=len(scored),
        expectancy_regressed=expectancy_regressed, should_promote=should, reason=reason,
    )


class ShadowValidator:
    """Stateful companion to break_score.StructuralBreakStateMachine's
    VALIDATION state -- requires a candidate to clear `compare()` on several
    CONSECUTIVE evaluations, not one lucky comparison, mirroring the same
    hysteresis principle used throughout this layer (section D's escalate/
    de-escalate design): one good comparison could be noise, several in a row
    is evidence. One instance per adaptation episode (break_score.py creates
    a fresh one each time it re-enters ADAPTATION)."""
    CONSECUTIVE_PASSES_REQUIRED = 3

    def __init__(self):
        self.consecutive_passes = 0
        self.attempts = 0
        self.last_comparison: ShadowComparison | None = None

    def evaluate(self, old_window: dict, candidate_window: dict) -> dict:
        cmp = compare(old_window, candidate_window)
        self.last_comparison = cmp
        self.attempts += 1
        self.consecutive_passes = self.consecutive_passes + 1 if cmp.should_promote else 0
        return {
            "comparison": cmp.to_dict(),
            "consecutive_passes": self.consecutive_passes,
            "passes_required": self.CONSECUTIVE_PASSES_REQUIRED,
            "attempts": self.attempts,
            "promote_now": self.consecutive_passes >= self.CONSECUTIVE_PASSES_REQUIRED,
        }
