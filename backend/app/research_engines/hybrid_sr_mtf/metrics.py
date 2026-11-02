"""Option-equivalent P&L, metrics, bootstrap and regimes for the hybrid S/R research. Index points -> one lot of an ATM weekly option with a fixed delta; friction = the monitored cost model used for autoscalp
(charges + measured half-spread, app.autoscalp.net_edge._friction_rs) + two extra brokerage orders for the 40/30/30 scale-out. Theta and gamma are NOT modelled (stated limitation)."""
from __future__ import annotations

import numpy as np
import pandas as pd

LOT = {"NIFTY": 75, "BANKNIFTY": 30, "SENSEX": 20}
PREMIUM = {"NIFTY": 150.0, "BANKNIFTY": 350.0, "SENSEX": 400.0}                   # a typical ATM weekly premium in the morning session (assumption, taken from the captured quotes)
DELTA = 0.5
EXTRA_LEG_COST = 2 * 20.0 * 1.18


def net_rs(und: str, pts: float) -> tuple[float, float]:
    """(net, friction) in rupees for one lot; pts = composite index points of the trade."""
    from ...autoscalp.net_edge import _friction_rs
    lot = LOT[und]; p = PREMIUM[und]; opt_move = DELTA * pts; fr = _friction_rs(und, p, max(p + opt_move, 0.05), lot) + EXTRA_LEG_COST
    return DELTA * pts * lot - fr, fr


def regimes(prep) -> dict[int, str]:
    """day -> 'bull' / 'bear' / 'sideways' from the return of the previous 30 sessions (known at the open of the day)."""
    days = list(dict.fromkeys(prep.day.tolist())); close = {d: prep.c[np.flatnonzero(prep.day == d)[-1]] for d in days} if len(days) < 50 else None
    if close is None:
        s = pd.Series(prep.c, index=prep.day).groupby(level=0).last(); close = s.to_dict()
    out = {}
    for i, d in enumerate(days):
        if i < 31: out[d] = "n/a"; continue
        r = close[days[i - 1]] / close[days[i - 31]] - 1
        out[d] = "bull" if r > 0.03 else "bear" if r < -0.03 else "sideways"
    return out


def boot_ci(net: np.ndarray, days: np.ndarray, B: int = 2000, seed: int = 11, q: float = 5.0):
    """Bootstrap by session day (trades of one day are not independent) of the mean net per trade; 90 % interval."""
    if len(net) < 8: return (float("nan"), float("nan"))
    s = pd.Series(net).groupby(days); sums = s.sum().to_numpy(); cnt = s.count().to_numpy()
    if len(sums) < 4: return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed); out = []
    for _ in range(B):
        k = rng.integers(0, len(sums), len(sums)); out.append(sums[k].sum() / cnt[k].sum())
    return (float(np.percentile(out, q)), float(np.percentile(out, 100 - q)))


def summarize(trades: list[dict], und: str) -> dict:
    if not trades: return {"n": 0}
    t = pd.DataFrame(trades).sort_values("t_entry"); nets = []; fr = []
    for x in t.itertuples(): a, b = net_rs(und, x.pts); nets.append(a); fr.append(b)
    t["net"] = nets; t["friction"] = fr; w = t[t.net > 0]; lo = t[t.net <= 0]; cum = t.net.cumsum().to_numpy(); dd = float((np.maximum.accumulate(cum) - cum).max()) if len(cum) else 0.0
    run = mx = 0
    for v in t.net.to_numpy():
        run = run + 1 if v <= 0 else 0; mx = max(mx, run)
    lo_ci, hi_ci = boot_ci(t.net.to_numpy(), t.day.to_numpy())
    gross_pos = float(w.net.sum()); gross_neg = float(-lo.net.sum())
    return {"n": int(len(t)), "win_rate": round(float((t.net > 0).mean()), 4), "profit_factor": round(gross_pos / gross_neg, 3) if gross_neg > 0 else None, "net_total_rs": round(float(t.net.sum()), 0),
            "net_per_trade_rs": round(float(t.net.mean()), 1), "ci90_per_trade": [round(lo_ci, 1), round(hi_ci, 1)], "avg_win_rs": round(float(w.net.mean()), 1) if len(w) else None,
            "avg_loss_rs": round(float(lo.net.mean()), 1) if len(lo) else None, "payoff": round(float(w.net.mean() / -lo.net.mean()), 2) if len(w) and len(lo) and lo.net.mean() < 0 else None,
            "gross_pts_per_trade": round(float(t.pts.mean()), 2), "friction_rs_per_trade": round(float(t.friction.mean()), 1), "R_mean": round(float(t.R.mean()), 3), "max_drawdown_rs": round(dd, 0),
            "max_consec_losses": int(mx), "mae_R_mean": round(float((t.mae / t.risk).mean()), 3), "mfe_R_mean": round(float((t.mfe / t.risk).mean()), 3), "t1_hit": round(float(t.hit_t1.mean()), 3),
            "t2_hit": round(float(t.hit_t2.mean()), 3), "t3_hit": round(float(t.hit_t3.mean()), 3), "sl_exits": round(float(t.reason.isin(["SL"]).mean()), 3), "ambiguous_bars": int(t.ambiguous.sum()), "days": int(t.day.nunique())}
