"""The 18-item per-engine report + a robust OOS composite rank (NOT win-rate
driven). Everything in R (each trade risks 1R -> vol-normalized). `points` are
raw unit-size index points, reported for reference only."""
from __future__ import annotations

import math
import statistics


def _sharpe(rs: list[float]) -> float | None:
    if len(rs) < 20:
        return None
    m = statistics.mean(rs)
    sd = statistics.pstdev(rs)
    return round(m / sd * math.sqrt(252), 3) if sd > 1e-9 else None   # ~ per-trade -> annualised proxy


def _sortino(rs: list[float]) -> float | None:
    if len(rs) < 20:
        return None
    m = statistics.mean(rs)
    dn = [r for r in rs if r < 0]
    dd = statistics.pstdev(dn) if len(dn) > 1 else 0.0
    return round(m / dd * math.sqrt(252), 3) if dd > 1e-9 else None


def _max_consec_losses(trades: list[dict]) -> int:
    run = best = 0
    for t in trades:
        if t["r_multiple"] <= 0:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def summarize(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0}
    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    R = [t["r_multiple"] for t in trades]
    P = [t["points"] for t in trades]
    gw = sum(t["points"] for t in wins)
    gl = sum(t["points"] for t in losses)          # <= 0
    # equity curve in R
    eq = 0.0
    peak = 0.0
    dd = 0.0
    for r in R:
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    hold = [t["held_bars"] for t in trades]
    return {
        "n": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n, 4),
        "avg_win_R": round(sum(t["r_multiple"] for t in wins) / len(wins), 4) if wins else 0.0,
        "avg_loss_R": round(sum(t["r_multiple"] for t in losses) / len(losses), 4) if losses else 0.0,
        "avg_win_points": round(gw / len(wins), 2) if wins else 0.0,
        "avg_loss_points": round(gl / len(losses), 2) if losses else 0.0,
        "expectancy_R": round(sum(R) / n, 4),
        "expectancy_points": round(sum(P) / n, 3),
        "profit_factor": round(gw / abs(gl), 3) if gl < 0 else 9.99,
        "net_points": round(sum(P), 1), "net_R": round(sum(R), 2),
        "max_drawdown_R": round(dd, 3),
        "sharpe": _sharpe(R), "sortino": _sortino(R),
        "t1_rate": round(sum(1 for t in trades if t["t1_hit"]) / n, 4),
        "t2_rate": round(sum(1 for t in trades if t["t2_hit"]) / n, 4),
        "t3_rate": round(sum(1 for t in trades if t["t3_hit"]) / n, 4),
        "sl_rate": round(sum(t["sl_rate"] for t in trades) / n, 4),
        "avg_hold_bars": round(sum(hold) / n, 2),
        "max_consec_losses": _max_consec_losses(trades),
    }


def by_key(trades: list[dict], keyfn) -> dict:
    g: dict = {}
    for t in trades:
        g.setdefault(keyfn(t), []).append(t)
    return {k: summarize(v) for k, v in sorted(g.items(), key=lambda kv: str(kv[0]))}


def stability(train: dict, oos: dict) -> float:
    """1 - relative expectancy degradation, clipped to [0, 1]."""
    te, oe = train.get("expectancy_R"), oos.get("expectancy_R")
    if te is None or oe is None or not train.get("n") or not oos.get("n"):
        return 0.0
    if abs(te) < 1e-6:
        return 1.0 if oe >= 0 else 0.0
    deg = abs(te - oe) / abs(te)
    return round(max(0.0, 1.0 - deg), 3)


def regime_robustness(regime_tbl: dict) -> float:
    """weight of regimes with positive OOS expectancy / total (n-weighted)."""
    tot = pos = 0
    for m in regime_tbl.values():
        nn = m.get("n", 0)
        if nn < 8:
            continue
        tot += nn
        if m.get("expectancy_R", -9) > 0:
            pos += nn
    return round(pos / tot, 3) if tot else 0.0


def composite(oos: dict, train: dict, regime_tbl: dict, w: dict,
              min_n: int = 40) -> dict:
    """0-100. Sub-scores each in [0,1] with sane anchors. NOT win-rate driven.
    Multiplied by a sample-sufficiency factor so a lucky sub-threshold OOS
    sample cannot rank first."""
    if not oos.get("n"):
        return {"score": 0.0, "sub": {}, "note": "no OOS trades"}
    e = oos["expectancy_R"]
    pf = oos["profit_factor"]
    dd = oos["max_drawdown_R"]
    sub = {
        "expectancy_R": _clip((e + 0.05) / 0.30),          # -0.05R -> 0 ; +0.25R -> 1
        "profit_factor": _clip((pf - 0.8) / (1.6 - 0.8)),  # 0.8 -> 0 ; 1.6 -> 1
        "drawdown_R": _clip(1.0 - (abs(dd) / max(oos["n"], 1) / 0.20)),  # dd/n vs 0.20R
        "stability": stability(train, oos),
        "sample_size": _clip(oos["n"] / 200.0),
        "regime_robustness": regime_robustness(regime_tbl),
    }
    raw = 100.0 * sum(w[k] * sub[k] for k in w)
    suff = _clip(oos["n"] / max(1, min_n))          # < min_n OOS trades -> discounted
    return {"score": round(raw * suff, 1), "raw_score": round(raw, 1),
            "sample_sufficiency": round(suff, 3),
            "sub": {k: round(v, 3) for k, v in sub.items()}}


def _clip(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def hour_bucket(t: dict) -> str:
    m = t["minute_of_day"]
    if m < 10 * 60:
        return "open_0915_1000"
    if m < 12 * 60:
        return "morning_1000_1200"
    if m < 13 * 60 + 30:
        return "midday_1200_1330"
    return "afternoon_1330+"
