"""
Backtest validation: baseline-static vs structural-break-adaptive -- section
J of the spec ("successful only if it improves robustness out-of-sample,
not merely historical backtest profit").

Deliberately does NOT re-run app/backtest/runner.py's option-candle-level
replay harness (run_backtest/run_ablation). That harness answers a
different question -- does THIS strategy make money over historical option
premiums -- and comparing it against an "adaptive" variant would require a
second, re-parameterized strategy to actually run, which nothing in this
build has produced (shadow.py's own docstring: no re-specification/
re-parameterization engine exists yet, that's a separate modeling task).

What this file answers instead, with what IS built: given a chronological
sequence of ALREADY-RESOLVED trades (the same scalp_signals-shaped rows
every other file in this layer consumes), would this structural-break layer
-- run walk-forward, tick by tick, exactly as evaluator.py does live -- have
improved robustness by refusing new entries while in a HALT state
(STRUCTURAL_BREAK / ADAPTATION)? No look-ahead: at each tick the state
machine only sees rows strictly before that tick, same discipline as
backtest/runner.py's frozen-TRAIN-then-TEST split, applied at trade-sequence
granularity instead of bar/candle granularity.

  "baseline" = the trades exactly as they happened, no gating at all.
  "adaptive" = the same sequence with every trade that occurred while the
               walk-forward state machine was in a HALT state removed --
               simulating adaptation.py's Safeguards halt actually being in
               force at that moment.
Both are scored with performance_monitor.window_metrics() (the same P&L math
PerformanceMonitor itself uses), so the two numbers are directly comparable.

Two of the spec's requested metrics are honestly NOT computable here, and
are reported as `None` with an explanation rather than a fabricated proxy:
false-structural-break-detections and missed-structural-breaks both require
independently-labeled ground-truth regime-break dates, which don't exist in
this codebase. Inventing a proxy for a metric whose entire purpose is
validating this layer's OWN detections would be circular.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .break_score import StructuralBreakStateMachine
from .feature_drift import evaluate_feature_drift
from .performance_monitor import DEFAULT_WINDOWS, window_metrics
from .prediction_drift import evaluate_prediction_drift

HALT_STATES = ("STRUCTURAL_BREAK", "ADAPTATION")
# Walk forward in the same increment performance_monitor's own "short"
# window uses -- not a new number, reusing the one already established as
# "enough rows for a stable-ish read" elsewhere in this layer.
DEFAULT_TICK_N = DEFAULT_WINDOWS["short"]

_UNCOMPUTABLE_NOTE = (
    "false_structural_break_detections and missed_structural_breaks require "
    "independently-labeled ground-truth regime-break dates, which this codebase does not "
    "have -- reported as None rather than a fabricated proxy."
)


@dataclass
class ComparisonReport:
    n_rows: int
    tick_n: int
    n_ticks: int
    n_model_switches: int          # transitions INTO a HALT state during the walk
    n_trades_gated: int            # trades excluded from the adaptive path
    baseline: dict
    adaptive: dict
    false_structural_break_detections: None = None
    missed_structural_breaks: None = None
    note: str = _UNCOMPUTABLE_NOTE

    def to_dict(self):
        return asdict(self)


def compare_static_vs_adaptive(rows: list[dict], *, tick_n: int = DEFAULT_TICK_N,
                                min_rows_before_first_tick: int = 30) -> ComparisonReport:
    """rows: chronological (oldest first) scalp_signals-shaped dicts, same
    contract as performance_monitor.resolved_rows() / feature_drift.py /
    prediction_drift.py."""
    n = len(rows)
    sm = StructuralBreakStateMachine()
    gated_indices: set[int] = set()
    n_switches = 0

    tick_start = min_rows_before_first_tick
    n_ticks = 0
    while tick_start < n:
        tick_end = min(tick_start + tick_n, n)
        history_so_far = rows[:tick_start]   # strictly before this tick -- no look-ahead

        perf_report = {label: window_metrics(history_so_far[-size:] if size else history_so_far)
                       for label, size in DEFAULT_WINDOWS.items()}
        feature_report = evaluate_feature_drift(history_so_far)
        prediction_report = evaluate_prediction_drift(history_so_far)

        state_before = sm.state
        out = sm.evaluate(perf_report, feature_report, prediction_report)
        if out["state"] in HALT_STATES and state_before not in HALT_STATES:
            n_switches += 1
        if out["state"] in HALT_STATES:
            gated_indices.update(range(tick_start, tick_end))

        tick_start = tick_end
        n_ticks += 1

    adaptive_rows = [r for i, r in enumerate(rows) if i not in gated_indices]

    return ComparisonReport(
        n_rows=n, tick_n=tick_n, n_ticks=n_ticks,
        n_model_switches=n_switches, n_trades_gated=len(gated_indices),
        # min_n=1: this report is explicitly labeled with n_rows/n_trades_gated
        # so the caller can judge sample size themselves, rather than this
        # function silently hiding a small comparison behind INSUFFICIENT_DATA.
        baseline=window_metrics(rows, min_n=1),
        adaptive=window_metrics(adaptive_rows, min_n=1),
    )
