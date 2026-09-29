"""Performance metrics (spec B6) -- computed from the event-driven engine's
own trade list, never assumed/invented. Every number here is either directly
in the trade dicts (engine.backtester._finish's r_multiple_gross/net,
mfe_r/mae_r, holding_bars) or a straightforward aggregate of them."""
from __future__ import annotations

import numpy as np

MIN_SAMPLE_DEFAULT = 30


def _sharpe_sortino(r: np.ndarray) -> tuple[float | None, float | None]:
    if len(r) < 2 or r.std() == 0:
        return None, None
    sharpe = float(r.mean() / r.std())
    downside = r[r < 0]
    sortino = float(r.mean() / downside.std()) if len(downside) and downside.std() != 0 else None
    return round(sharpe, 3), (round(sortino, 3) if sortino is not None else None)


def _max_drawdown_r(r: np.ndarray) -> float:
    if len(r) == 0:
        return 0.0
    equity = np.cumsum(r)
    peak = np.maximum.accumulate(equity)
    dd = peak - equity
    return round(float(dd.max()), 3)


def compute_performance(trades: list[dict], min_sample: int = MIN_SAMPLE_DEFAULT) -> dict:
    """GLOBAL metrics across every trade passed in (caller filters by
    setup/RR bucket for the per-setup/per-RR breakdowns spec B6 asks for --
    same function, different trade-list slices)."""
    n = len(trades)
    if n == 0:
        return {"n": 0, "verdict": "INSUFFICIENT_SAMPLE", "expectancy_r": 0.0}

    gross = np.array([t["r_multiple_gross"] for t in trades])
    net = np.array([t["r_multiple_net"] for t in trades])
    wins_net = net[net > 0]
    losses_net = net[net <= 0]
    pf_gross = float(gross[gross > 0].sum() / abs(gross[gross <= 0].sum())) if (gross <= 0).any() and gross[gross <= 0].sum() != 0 else None
    pf_net = float(wins_net.sum() / abs(losses_net.sum())) if len(losses_net) and losses_net.sum() != 0 else None
    sharpe, sortino = _sharpe_sortino(net)

    return {
        "n": n,
        "verdict": "OK" if n >= min_sample else "INSUFFICIENT_SAMPLE",
        "win_rate": round(len(wins_net) / n, 4),
        "expectancy_r": round(float(net.mean()), 4),
        "profit_factor_gross": round(pf_gross, 3) if pf_gross is not None else None,
        "profit_factor_net": round(pf_net, 3) if pf_net is not None else None,
        "max_drawdown_r": _max_drawdown_r(net),
        "sharpe": sharpe, "sortino": sortino,
        "avg_win_r": round(float(wins_net.mean()), 4) if len(wins_net) else None,
        "avg_loss_r": round(float(losses_net.mean()), 4) if len(losses_net) else None,
        "avg_mfe_r": round(float(np.mean([t["mfe_r"] for t in trades])), 4),
        "avg_mae_r": round(float(np.mean([t["mae_r"] for t in trades])), 4),
        "avg_holding_bars": round(float(np.mean([t["holding_bars"] for t in trades])), 2),
        "total_r_gross": round(float(gross.sum()), 3),
        "total_r_net": round(float(net.sum()), 3),
    }


def per_group(trades: list[dict], key: str, min_sample: int = MIN_SAMPLE_DEFAULT) -> dict:
    groups: dict = {}
    for t in trades:
        groups.setdefault(t.get(key, "UNKNOWN"), []).append(t)
    return {k: compute_performance(v, min_sample) for k, v in groups.items()}


def sub_period(trades: list[dict], freq: str = "M", min_sample: int = MIN_SAMPLE_DEFAULT) -> dict:
    """spec B8: per-month/per-quarter breakdown, keyed by entry_ts's period."""
    groups: dict = {}
    for t in trades:
        period = t["entry_ts"].tz_localize(None).to_period(freq)
        groups.setdefault(str(period), []).append(t)
    return {k: compute_performance(v, min_sample) for k, v in sorted(groups.items())}
