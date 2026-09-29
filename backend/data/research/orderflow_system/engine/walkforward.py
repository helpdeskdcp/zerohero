"""
Walk-forward analysis (spec B3/B4/B5): rolling train/test windows over the
in-sample range, plus one final, untouched out-of-sample holdout. Every
number here comes from engine.backtester.run_setups (the official
event-driven engine) re-run on each window's own slice -- never derived by
slicing a single full-history run's trade list after the fact (that would
let a trade whose ENTRY falls in one window but which was SIMULATED using
data from the next window leak information across the boundary).
"""
from __future__ import annotations

import pandas as pd

from engine.backtester import run_setups
from metrics.performance import compute_performance


def split_oos_holdout(df: pd.DataFrame, oos_holdout_days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (in_sample_df, oos_df). The OOS slice is the LAST
    oos_holdout_days calendar days -- untouched by any walk-forward fold or
    parameter choice (spec B4)."""
    cutoff = df.index.max().normalize() - pd.Timedelta(days=oos_holdout_days)
    return df[df.index <= cutoff], df[df.index > cutoff]


def walk_forward_folds(df: pd.DataFrame, setups: list, cfg: dict) -> list[dict]:
    """Rolling train_days/test_days/step_days folds over `df` (normally the
    in-sample slice from split_oos_holdout). Each fold's test-window trades
    are simulated using ONLY that fold's own data -- setups are re-built
    fresh per fold by the caller (main.py) so FSM state never leaks across
    fold boundaries either."""
    wf = cfg["walkforward"]
    dates = df.index.normalize().unique().sort_values()
    train_days, test_days, step_days = wf["train_days"], wf["test_days"], wf["step_days"]
    folds = []
    start = 0
    while start + train_days + test_days <= len(dates):
        train_start, train_end = dates[start], dates[start + train_days - 1]
        test_start, test_end = dates[start + train_days], dates[min(start + train_days + test_days - 1, len(dates) - 1)]
        test_df = df[(df.index.normalize() >= test_start) & (df.index.normalize() <= test_end)]
        if not test_df.empty:
            trades = run_setups(test_df, setups, cfg)
            perf = compute_performance(trades, cfg["backtest"]["min_sample_for_verdict"])
            folds.append({"train_start": str(train_start.date()), "train_end": str(train_end.date()),
                         "test_start": str(test_start.date()), "test_end": str(test_end.date()),
                         "n_trades": len(trades), "expectancy_r": perf["expectancy_r"],
                         "verdict": perf["verdict"]})
        start += step_days
    return folds


def champion_vs_challenger(champion_oos_expectancy: float, challenger_oos_expectancy: float,
                           challenger_folds: list[dict], cfg: dict) -> dict:
    """Spec U4 acceptance criteria -- a challenger config is adopted ONLY if
    ALL of these hold; a change that only improves in-sample (never checked
    here -- the caller must pass OOS numbers) is rejected by construction."""
    acc = cfg["acceptance"]
    wf = cfg["walkforward"]
    if champion_oos_expectancy == 0:
        drop_pct = 100.0 if challenger_oos_expectancy < 0 else 0.0
    else:
        drop_pct = (champion_oos_expectancy - challenger_oos_expectancy) / abs(champion_oos_expectancy) * 100
    positive_folds = sum(1 for f in challenger_folds if f["expectancy_r"] > 0)
    n_required, m_of = wf["min_windows_positive_of_total"]
    checks = {
        "oos_expectancy_drop_ok": drop_pct <= acc["oos_expectancy_drop_max_pct"],
        "walkforward_consistency_ok": positive_folds >= n_required and len(challenger_folds) >= 1,
    }
    return {"adopted": all(checks.values()), "checks": checks, "oos_expectancy_drop_pct": round(drop_pct, 2),
           "positive_folds": positive_folds, "total_folds": len(challenger_folds)}
