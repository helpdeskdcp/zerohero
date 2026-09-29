"""
Parameter-sensitivity screening (spec B5). DISCLOSED DESIGN CHOICE: this
sweeps the SAME event-driven engine (engine.backtester.run_setups) across a
parameter grid, rather than a separately-implemented fast/vectorized
approximation of the strategy logic. Given how small this dataset's actual
signal counts are (a handful of trades over 5 years -- see the v1 report),
a genuinely separate vectorized re-implementation of the stateful
confirm/retest FSM would be a large amount of extra code whose only purpose
is speed, at real risk of drifting from the official engine's actual
behavior (the two would need to agree on every look-ahead-sensitive edge
case). Re-running the real engine across a small grid is both simpler and
strictly more faithful; it is still clearly labeled SCREENING, and its
numbers are NEVER used as the official backtest result -- only
engine/walkforward.py's OOS run is.
"""
from __future__ import annotations

import copy

from engine.backtester import run_setups
from metrics.performance import compute_performance


def sweep(df, setup_factory, setup_key: str, param_grid: dict, cfg: dict) -> list[dict]:
    """param_grid: {"delta_mult": [1.0, 1.5, 2.0], "stop_buffer_ticks": [3, 5, 8]}
    (or any subset of this setup's own config keys). Returns one row per
    grid point: params + n_trades + expectancy_r + a robustness flag."""
    import itertools
    keys = list(param_grid)
    rows = []
    for combo in itertools.product(*param_grid.values()):
        trial_cfg = copy.deepcopy(cfg)
        for k, v in zip(keys, combo):
            trial_cfg[setup_key][k] = v
        setup = setup_factory(trial_cfg)
        trades = run_setups(df, [setup], trial_cfg)
        perf = compute_performance(trades, trial_cfg["backtest"]["min_sample_for_verdict"])
        rows.append({**dict(zip(keys, combo)), "n_trades": len(trades),
                    "expectancy_r": perf["expectancy_r"], "verdict": perf["verdict"]})
    return rows


def robustness_score(rows: list[dict]) -> float:
    """Fraction of swept param sets whose expectancy stayed > 0 -- spec B5's
    "flag any param set whose edge collapses when perturbed." With an
    all-zero-trade grid (as here, given the real signal scarcity) this is
    reported as 0.0, not silently omitted -- a low score is itself the
    honest finding, not a reason to hide the metric."""
    scored = [r for r in rows if r["n_trades"] > 0]
    if not scored:
        return 0.0
    return sum(1 for r in scored if r["expectancy_r"] > 0) / len(scored)
