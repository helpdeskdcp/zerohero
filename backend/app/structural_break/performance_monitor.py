"""
Rolling-window performance monitor -- section A of the Structural Break spec.

Reads FROM the existing `scalp_signals` table (via `db.list_scalp_signals`,
never a new table) and reuses `autoscalp.calibration_report`'s existing
reliability/ECE binning rather than re-deriving it. This file only adds the
ROLLING-WINDOW slicing and the metrics calibration_report doesn't already
compute (avg_win/avg_loss split, consecutive-loss streaks, directional
accuracy, a simpler prediction-error companion to Brier).

Multiple windows, sized in SIGNAL COUNT not calendar time -- signal frequency
varies enormously by symbol/regime (memory: NIFTY frozen, MCX symbols firing
far more often), so a fixed calendar window would mean wildly different
statistical power across symbols. Defaults (short=20, medium=60, long=150):
short=20 matches calibration_report.py's own existing
`min_for_stable_metrics` floor (the smallest sample already treated as
"stable enough" elsewhere in this codebase); medium/long scale up 3x each,
giving three genuinely different vantage points (recent / intermediate /
baseline) without an arbitrary jump.

This module NEVER decides anything -- it returns per-window metric dicts.
break_score.py is the only place that turns these numbers into a verdict,
and it does so with hysteresis across windows precisely so one bad short
window (a small losing streak) cannot by itself look like a structural break.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .. import db
from ..autoscalp.calibration_report import _reliability

DEFAULT_WINDOWS = {"short": 20, "medium": 60, "long": 150}


def _resolved_rows(*, source="LIVE", symbol=None, regime=None, limit=500) -> list[dict]:
    """Chronological (oldest first) resolved rows with a usable probability +
    outcome -- same filter as calibration_report._resolved_rows, but keeping
    the full row (need direction/mfe/regime/created_ts, not just the 4-tuple).

    The regime filter is applied IN THE SQL QUERY (db.list_scalp_signals),
    not after fetching a limited page -- filtering client-side after a
    LIMIT would silently starve a regime whose matching rows aren't among
    the most-recently-inserted `limit` rows (caught by
    test_symbol_and_regime_filters_isolate_the_right_rows while building
    this: two regimes' rows interleaved by insertion order, and the older
    regime's rows fell off a client-side-filtered window entirely)."""
    rows = db.list_scalp_signals(source=source, status="CLOSED", symbol=symbol,
                                  regime=regime, limit=limit)
    rows = list(reversed(rows))  # list_scalp_signals is id DESC; we want oldest-first
    return [r for r in rows if r.get("probability") is not None
            and r.get("outcome") in ("WIN", "LOSS", "FLAT")]


def window_metrics(rows: list[dict], *, min_n: int = 5) -> dict:
    """Public entry point for other structural_break modules
    (backtest_compare.py) that need to score an arbitrary row slice with the
    exact same per-window metric logic PerformanceMonitor itself uses,
    without going through the DB fetch."""
    return _window_metrics(rows, min_n=min_n).to_dict()


def resolved_rows(*, source="LIVE", symbol=None, regime=None, limit=500) -> list[dict]:
    """Public entry point for other structural_break modules (feature_drift,
    prediction_drift, via evaluator.py) that need the EXACT SAME row set
    this monitor scores from, rather than re-deriving their own fetch and
    risking the client-side-filter-after-LIMIT bug this module already fixed
    once (see module docstring)."""
    return _resolved_rows(source=source, symbol=symbol, regime=regime, limit=limit)


@dataclass
class WindowMetrics:
    n: int
    status: str                              # "OK" | "INSUFFICIENT_DATA"
    win_rate: float | None = None
    expectancy_points: float | None = None
    profit_factor: float | None = None
    avg_win_points: float | None = None
    avg_loss_points: float | None = None
    max_drawdown_points: float | None = None
    consecutive_losses_current: int = 0
    consecutive_losses_max: int = 0
    directional_accuracy: float | None = None
    directional_accuracy_method: str | None = None
    brier: float | None = None
    ece: float | None = None
    mean_predicted: float | None = None
    prediction_error_mae: float | None = None

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if v is not None or k in ("n", "status")}


def _window_metrics(rows: list[dict], *, min_n: int = 5) -> WindowMetrics:
    n = len(rows)
    if n < min_n:
        return WindowMetrics(n=n, status="INSUFFICIENT_DATA")

    probs = [max(0.0, min(1.0, float(r["probability"]))) for r in rows]
    wins = [1 if r["outcome"] == "WIN" else 0 for r in rows]
    pnls = [float(r["points"]) for r in rows if r.get("points") is not None]

    win_rate = sum(wins) / n
    gross_win = sum(x for x in pnls if x > 0)
    gross_loss = -sum(x for x in pnls if x < 0)
    pf = round(gross_win / gross_loss, 3) if gross_loss > 0 else None
    expectancy = round(sum(pnls) / len(pnls), 4) if pnls else None
    wins_pts = [x for x in pnls if x > 0]
    losses_pts = [x for x in pnls if x <= 0]
    avg_win = round(sum(wins_pts) / len(wins_pts), 4) if wins_pts else None
    avg_loss = round(sum(losses_pts) / len(losses_pts), 4) if losses_pts else None

    eq = peak = mdd = 0.0
    for x in pnls:
        eq += x
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)

    # consecutive losses: current streak at the END of the window, and the
    # worst streak seen ANYWHERE inside the window (both matter -- "current"
    # answers "are we in one right now", "max" answers "how bad has it been")
    cur_streak = max_streak = 0
    for w in wins:
        if w == 0:
            cur_streak += 1
            max_streak = max(max_streak, cur_streak)
        else:
            cur_streak = 0
    consecutive_losses_current = cur_streak  # streak ends at the last element by construction

    # directional accuracy: distinct from win_rate -- uses MFE (max favourable
    # excursion, already captured per-trade) as "did the market move the
    # called direction at any point", independent of whether the option
    # ultimately closed positive after theta/spread. A WIN always counts (it
    # closed positive, so it was directionally right); a LOSS with mfe>0 was
    # directionally right but timing/decay/stop took the P&L away. Falls back
    # to win_rate (documented, not silently) when no row has mfe captured.
    mfe_rows = [r for r in rows if r.get("mfe") is not None]
    if mfe_rows:
        correct = sum(1 for r in mfe_rows
                      if r["outcome"] == "WIN" or float(r["mfe"]) > 0)
        directional_accuracy = round(correct / len(mfe_rows), 4)
        da_method = "win_or_mfe_positive"
    else:
        directional_accuracy = round(win_rate, 4)
        da_method = "fallback_win_rate_no_mfe_captured"

    brier = round(sum((p - w) ** 2 for p, w in zip(probs, wins)) / n, 4)
    _table, ece = _reliability(list(zip(probs, wins)))
    mean_predicted = round(sum(probs) / n, 4)
    # simpler, more directly interpretable companion to Brier's squared error
    prediction_error_mae = round(sum(abs(p - w) for p, w in zip(probs, wins)) / n, 4)

    return WindowMetrics(
        n=n, status="OK", win_rate=round(win_rate, 4), expectancy_points=expectancy,
        profit_factor=pf, avg_win_points=avg_win, avg_loss_points=avg_loss,
        max_drawdown_points=round(mdd, 3),
        consecutive_losses_current=consecutive_losses_current,
        consecutive_losses_max=max_streak,
        directional_accuracy=directional_accuracy, directional_accuracy_method=da_method,
        brier=brier, ece=ece, mean_predicted=mean_predicted,
        prediction_error_mae=prediction_error_mae,
    )


class PerformanceMonitor:
    """`.evaluate(symbol=..., regime=...)` -> {short, medium, long, windows}.

    Pure read: pulls resolved LIVE scalp_signals rows once, slices them into
    the 3 windows (most-recent-N by signal count), computes each window's
    metrics independently. No state persists across calls -- the caller
    (break_score.py) is responsible for comparing successive evaluate()
    results over time if it wants to track a metric's own drift (via
    drift.py's detectors, fed this monitor's output).
    """

    def __init__(self, windows: dict | None = None, *, fetch_limit: int | None = None,
                 min_n: int = 5):
        self.windows = dict(windows or DEFAULT_WINDOWS)
        self.fetch_limit = fetch_limit or max(self.windows.values())
        self.min_n = min_n

    def evaluate(self, *, symbol: str | None = None, regime: str | None = None,
                 source: str = "LIVE") -> dict:
        rows = _resolved_rows(source=source, symbol=symbol, regime=regime,
                               limit=self.fetch_limit)
        out = {"symbol": symbol, "regime": regime, "windows": dict(self.windows),
               "n_available": len(rows)}
        for label, size in self.windows.items():
            window_rows = rows[-size:] if size else rows
            out[label] = _window_metrics(window_rows, min_n=self.min_n).to_dict()
        return out
