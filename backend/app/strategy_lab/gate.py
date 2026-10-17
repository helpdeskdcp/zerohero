"""Promotion gate -- pure numpy, deterministic (seeded). Decides whether a strategy's trade list earns PAPER allocation.

Inputs are per-trade GROSS bps (signed in the trade direction, before costs), the trading day of each trade (ordinal), and the round-trip cost
in bps. Everything is judged NET of cost. All thresholds live in strategy_policy.json -> "promotion" (validated, fail-closed). The gate can only
return PROMOTE_TO_PAPER; there is no path to live from here. INSUFFICIENT_SAMPLE is a legitimate, expected answer."""
from __future__ import annotations

import numpy as np

PROMOTE, HOLD, REJECT, INSUFFICIENT = "PROMOTE_TO_PAPER", "HOLD", "REJECT", "INSUFFICIENT_SAMPLE"


def _day_cluster_ci(net: np.ndarray, days: np.ndarray, boot: int, seed: int) -> tuple[float, float]:
    """95% CI of the mean net bps, resampling whole DAYS (trades within a day are not independent)."""
    ud, inv = np.unique(days, return_inverse=True)
    s = np.bincount(inv, weights=net, minlength=len(ud))
    c = np.bincount(inv, minlength=len(ud)).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(ud), size=(boot, len(ud)))
    means = s[idx].sum(1) / c[idx].sum(1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def evaluate(gross_bps, days, cost_bps: float, params: dict, *, seed: int = 7) -> dict:
    g = np.asarray(gross_bps, dtype=float)
    d = np.asarray(days)
    n = len(g)
    out = {"n": n, "cost_bps": cost_bps, "failed": [], "verdict": INSUFFICIENT}
    if n == 0:
        out["failed"] = ["NO_TRADES"]
        return out
    order = np.arange(n)                                                 # caller passes trades in chronological order
    net = g - cost_bps
    nd = int(len(np.unique(d)))
    out.update({"distinct_days": nd, "gross_mean_bps": float(g.mean()), "net_mean_bps": float(net.mean()), "win_rate": float((net > 0).mean())})
    wins, losses = net[net > 0].sum(), -net[net <= 0].sum()
    out["profit_factor"] = float(wins / losses) if losses > 0 else float("inf")
    if n < params["min_trades"] or nd < params["min_distinct_days"]:
        out["failed"] = [f"SAMPLE n={n}<{params['min_trades']}" if n < params["min_trades"] else f"DAYS {nd}<{params['min_distinct_days']}"]
        return out                                                       # too little data to judge either way
    lo, hi = _day_cluster_ci(net, d, params["bootstrap"], seed)
    out["net_ci95_bps"] = (lo, hi)
    folds = np.array_split(order, params["folds"])
    fold_means = [float(net[f].mean()) for f in folds]
    out["fold_net_means_bps"] = fold_means
    stress = float((g - cost_bps * params["cost_stress_multiplier"]).mean())
    out["net_mean_at_stressed_cost_bps"] = stress
    top = np.sort(net)[::-1][:5]
    pos_total = net[net > 0].sum()
    share = float(top[top > 0].sum() / pos_total) if pos_total > 0 else 1.0
    ex_top5 = float(np.sort(net)[:-5].mean()) if n > 5 else float("nan")
    out.update({"top5_profit_share": share, "net_mean_ex_top5_bps": ex_top5})
    eq = np.cumsum(net)
    dd = float((np.maximum.accumulate(eq) - eq).max())
    out["max_drawdown_bps"] = dd
    f = out["failed"]
    if out["net_mean_bps"] < params["min_net_mean_bps"]:
        f.append("NET_EXPECTANCY")
    if lo <= 0:
        f.append("NET_CI_LOWER_NOT_POSITIVE")
    if sum(m > 0 for m in fold_means) < params["min_positive_folds"]:
        f.append("WALK_FORWARD_FOLDS")
    if params["last_fold_must_be_positive"] and fold_means[-1] <= 0:
        f.append("LAST_FOLD_NEGATIVE")
    if stress <= 0:
        f.append("COST_STRESS")
    if share > params["max_top5_profit_share"] or not (ex_top5 > 0):
        f.append("CONCENTRATION")
    if out["net_mean_bps"] > 0 and dd > params["max_drawdown_over_total_net"] * out["net_mean_bps"] * n:
        f.append("DRAWDOWN")
    out["verdict"] = PROMOTE if not f else (HOLD if len(f) <= 1 and "NET_EXPECTANCY" not in f else REJECT)
    return out
