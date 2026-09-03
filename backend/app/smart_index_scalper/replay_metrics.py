"""
Replay metrics + calibration reliability (slice 5/6, spec section 26).

Pure functions over the list of simulated trades that
`replay.SmartScalperReplay.run()` produces. Same metric definitions as
`journal._metrics` so live and replay numbers are comparable.

Sample gate: below MIN_SESSIONS distinct sessions OR MIN_TRADES trades the
aggregate is returned but flagged `descriptive_only` and the calibration table
is withheld — a handful of trades from 1-2 captured sessions is NOT an estimate
of forward performance (spec section 26: "No profitability claim until this
runs" — and it only *starts* to mean something once real sessions accumulate).
"""
from __future__ import annotations

from collections import defaultdict

MIN_SESSIONS = 8
MIN_TRADES = 20
_CONF_BUCKETS = ((0, 50), (50, 60), (60, 70), (70, 80), (80, 101))


def _num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def trade_metrics(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "status": "NO_TRADES"}
    pnls = [_num(t.get("pnl")) or 0.0 for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gw, gl = sum(wins), -sum(losses)
    rs = [_num(t.get("r_multiple")) for t in trades if _num(t.get("r_multiple")) is not None]
    mfes = [_num(t.get("mfe")) for t in trades if _num(t.get("mfe")) is not None]
    maes = [_num(t.get("mae")) for t in trades if _num(t.get("mae")) is not None]
    holds = [_num(t.get("hold_min")) for t in trades if _num(t.get("hold_min")) is not None]
    eq = peak = mdd = 0.0
    for p in pnls:                       # trades are appended in chronological order
        eq += p
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    return {
        "n": n,
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n, 4),
        "gross_profit": round(gw, 2), "gross_loss": round(gl, 2),
        "profit_factor": round(gw / gl, 3) if gl > 0 else None,
        "expectancy": round(sum(pnls) / n, 4),
        "avg_win": round(gw / len(wins), 3) if wins else None,
        "avg_loss": round(-gl / len(losses), 3) if losses else None,
        "avg_r_multiple": round(sum(rs) / len(rs), 3) if rs else None,
        "max_drawdown": round(mdd, 2),
        "avg_mfe": round(sum(mfes) / len(mfes), 3) if mfes else None,
        "avg_mae": round(sum(maes) / len(maes), 3) if maes else None,
        "avg_hold_min": round(sum(holds) / len(holds), 1) if holds else None,
        "false_signal_rate": round(len(losses) / n, 4),
        "exit_reason_mix": _count(trades, "exit_reason"),
    }


def _count(trades, key):
    c = defaultdict(int)
    for t in trades:
        c[str(t.get(key) or "NONE")] += 1
    return dict(c)


def reliability(trades: list[dict]) -> dict:
    """Confidence-bucket -> realised win rate + ECE. Only meaningful with a real
    sample; the caller withholds this block below the gate."""
    rows = []
    N = len(trades)
    ece = 0.0
    for lo, hi in _CONF_BUCKETS:
        b = [t for t in trades if lo <= (_num(t.get("confidence")) or 0) < hi]
        if not b:
            continue
        wr = sum(1 for t in b if (_num(t.get("pnl")) or 0) > 0) / len(b)
        ac = sum(_num(t.get("confidence")) or 0 for t in b) / len(b) / 100.0
        ece += len(b) / N * abs(wr - ac)
        rows.append({"bucket": f"{lo}-{hi - 1}", "n": len(b),
                     "avg_confidence": round(ac * 100, 1),
                     "realised_win_rate": round(wr, 3),
                     "gap": round(wr - ac, 3)})
    return {"buckets": rows, "ece": round(ece, 4) if rows else None,
            "verdict": _cal_verdict(ece) if rows else "NO_DATA"}


def _cal_verdict(ece: float) -> str:
    if ece <= 0.08:
        return "WELL_CALIBRATED"
    if ece <= 0.15:
        return "ACCEPTABLE"
    return "MISCALIBRATED"


def summarize(trades: list[dict], *, session_keys: set, min_sessions: int = MIN_SESSIONS,
              min_trades: int = MIN_TRADES) -> dict:
    n_sess, n_tr = len(session_keys), len(trades)
    ok = n_sess >= min_sessions and n_tr >= min_trades
    descriptive = not ok

    def block(rows):
        m = trade_metrics(rows)
        if descriptive and m.get("n"):
            m["descriptive_only"] = True
        return m

    by_p, by_i, by_r = defaultdict(list), defaultdict(list), defaultdict(list)
    for t in trades:
        by_p[t.get("profile") or "UNKNOWN"].append(t)
        by_i[t.get("symbol") or "UNKNOWN"].append(t)
        by_r[t.get("market_regime") or "UNKNOWN"].append(t)

    return {
        "status": "OK" if ok else "INSUFFICIENT_SAMPLE",
        "sample": {"sessions": n_sess, "trades": n_tr,
                   "min_sessions": min_sessions, "min_trades": min_trades},
        "overall": block(trades),
        "by_profile": {k: block(v) for k, v in by_p.items()},
        "by_instrument": {k: block(v) for k, v in by_i.items()},
        "by_market_regime": {k: block(v) for k, v in by_r.items()},
        "calibration": reliability(trades) if ok else {
            "status": "INSUFFICIENT_SAMPLE",
            "note": f"reliability table withheld until >= {min_sessions} sessions "
                    f"and >= {min_trades} trades ({n_sess}/{n_tr} so far)"},
        "note": "STRICT-CAUSAL replay over real captured data. "
                + ("Descriptive only — NOT an estimate of forward performance; "
                   "no profitability claim (spec section 26)." if descriptive
                   else "Sample gate met; treat as a first calibration read, not a guarantee."),
    }
