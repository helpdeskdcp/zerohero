"""Trade-level + daily-equity metrics for the trend-swing harness, plus the
crisis-window (crisis-alpha) diagnostic."""
from __future__ import annotations

import math
import statistics

_ANN = math.sqrt(252)


def _max_consec_losses(trades):
    run = best = 0
    for t in trades:
        if t["r_multiple"] <= 0:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def trade_metrics(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0}
    wins = [t for t in trades if t["r_multiple"] > 0]
    losses = [t for t in trades if t["r_multiple"] <= 0]
    R = [t["r_multiple"] for t in trades]
    gw = sum(t["r_multiple"] for t in wins)
    gl = sum(t["r_multiple"] for t in losses)
    eq = peak = dd = 0.0
    for r in R:
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {
        "n": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n, 4),
        "avg_win_R": round(gw / len(wins), 4) if wins else 0.0,
        "avg_loss_R": round(gl / len(losses), 4) if losses else 0.0,
        "expectancy_R": round(sum(R) / n, 4),
        "profit_factor": round(gw / abs(gl), 3) if gl < 0 else 9.99,
        "net_R": round(sum(R), 2),
        "net_points": round(sum(t["points"] for t in trades), 1),
        "max_drawdown_R": round(dd, 3),
        "max_consec_losses": _max_consec_losses(trades),
        "sl_rate": round(sum(t["sl_rate"] for t in trades) / n, 4),
        "avg_hold_days": round(sum(t["held_days"] for t in trades) / n, 2),
        "avg_mfe_R": round(sum(t.get("mfe_R", 0.0) for t in trades) / n, 3),
    }


def daily_metrics(daily_R: list[dict]) -> dict:
    rs = [d["r"] for d in daily_R]
    if len(rs) < 60:
        return {"days": len(rs)}
    m = statistics.mean(rs)
    sd = statistics.pstdev(rs)
    dn = [r for r in rs if r < 0]
    dsd = statistics.pstdev(dn) if len(dn) > 1 else 0.0
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    years = len(rs) / 252.0
    return {
        "days": len(rs),
        "sharpe": round(m / sd * _ANN, 3) if sd > 1e-12 else None,
        "sortino": round(m / dsd * _ANN, 3) if dsd > 1e-12 else None,
        "total_R": round(sum(rs), 2),
        "R_per_year": round(sum(rs) / years, 2) if years else None,
        "equity_maxdd_R": round(dd, 2),
        "exposure": round(sum(d["in_pos"] for d in daily_R) / len(daily_R), 3),
        "worst_day_R": round(min(rs), 3), "best_day_R": round(max(rs), 3),
    }


def by_year(trades: list[dict]) -> dict:
    g: dict = {}
    for t in trades:
        g.setdefault(t["year"], []).append(t)
    return {y: trade_metrics(v) for y, v in sorted(g.items())}


def by_side(trades: list[dict]) -> dict:
    return {s: trade_metrics([t for t in trades if t["direction"] == s])
            for s in ("LONG", "SHORT")}


def positive_years(yr_tbl: dict, min_n: int = 5) -> list[str]:
    return [y for y, m in yr_tbl.items() if m.get("n", 0) >= min_n and m.get("net_R", -9) > 0]


def crisis_windows(bars: list[dict], cfg: dict) -> list[dict]:
    """Peak -> trough index declines of >= crisis_min_decline_pct within
    <= crisis_max_days. Non-overlapping, largest first."""
    c = [b["c"] for b in bars]
    dates = [b["date"] for b in bars]
    md, mx = cfg["crisis_min_decline_pct"], cfg["crisis_max_days"]
    wins = []
    i = 0
    while i < len(c) - 2:
        peak = c[i]
        trough = peak
        tj = i
        for j in range(i + 1, min(len(c), i + mx + 1)):
            if c[j] < trough:
                trough = c[j]
                tj = j
        dec = (peak - trough) / peak if peak else 0.0
        if dec >= md and tj > i:
            wins.append({"start": dates[i], "trough": dates[tj], "decline_pct": round(dec, 3),
                         "i0": i, "i1": tj})
            i = tj + 1
        else:
            i += 1
    wins.sort(key=lambda w: -w["decline_pct"])
    return wins


def crisis_performance(daily_R: list[dict], bars: list[dict], windows: list[dict]) -> dict:
    dmap = {d["date"]: d["r"] for d in daily_R}
    in_crisis = out_crisis = 0.0
    crisis_dates = set()
    for w in windows:
        for k in range(w["i0"], w["i1"] + 1):
            crisis_dates.add(bars[k]["date"])
    for d in daily_R:
        if d["date"] in crisis_dates:
            in_crisis += d["r"]
        else:
            out_crisis += d["r"]
    return {
        "n_windows": len(windows),
        "worst_windows": [{"start": w["start"], "trough": w["trough"],
                           "decline_pct": w["decline_pct"]} for w in windows[:6]],
        "R_during_crises": round(in_crisis, 2),
        "R_outside_crises": round(out_crisis, 2),
        "crisis_alpha_positive": in_crisis > 0,
    }
