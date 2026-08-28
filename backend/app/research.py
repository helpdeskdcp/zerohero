"""
AI-RESEARCH-ANALYSIS — descriptive aggregation over LOGGED rows only.
No fabricated data, no forward-looking prediction, no accuracy claim.
Probabilities are RULE_BASED (transparent rule output), NOT ML and NOT
statistically calibrated.
"""
from datetime import datetime, timezone
from . import db


def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _strategy_stats(trades: list) -> dict:
    """Descriptive edge metrics for one bucket of CLOSED trades. All from
    logged rows only — expectancy here is realised, not a forward claim."""
    closed = [t for t in trades if str(t.get("status", "")).upper() == "CLOSED"]
    if not closed:
        return {"closed": 0}
    wins = [t for t in closed if (t.get("pnl") or 0) > 0]
    losses = [t for t in closed if (t.get("pnl") or 0) < 0]
    gross_win = sum(t.get("pnl") or 0 for t in wins)
    gross_loss = sum(t.get("pnl") or 0 for t in losses)  # negative
    n = len(closed)
    win_rate = len(wins) / n
    avg_win = (gross_win / len(wins)) if wins else 0.0
    avg_loss = (gross_loss / len(losses)) if losses else 0.0  # negative
    # expectancy per trade in currency, and in R (avg_win / |avg_loss|)
    expectancy = (gross_win + gross_loss) / n
    payoff = (avg_win / abs(avg_loss)) if avg_loss != 0 else None
    holds = []
    for t in closed:
        o, c = _parse_ts(t.get("opened_ts")), _parse_ts(t.get("closed_ts"))
        if o and c:
            holds.append((c - o).total_seconds())
    by_exit, by_setup = {}, {}
    for t in closed:
        er = t.get("exit_reason") or "UNKNOWN"
        by_exit[er] = by_exit.get(er, 0) + 1
        st = t.get("setup") or "—"
        s = by_setup.setdefault(st, {"n": 0, "pnl": 0.0, "wins": 0})
        s["n"] += 1
        s["pnl"] = round(s["pnl"] + (t.get("pnl") or 0), 2)
        if (t.get("pnl") or 0) > 0:
            s["wins"] += 1
    return {
        "closed": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(1000 * win_rate) / 10,
        "avg_win": round(100 * avg_win) / 100,
        "avg_loss": round(100 * avg_loss) / 100,
        "payoff_ratio": round(100 * payoff) / 100 if payoff is not None else None,
        "expectancy_per_trade": round(100 * expectancy) / 100,
        "profit_factor": round(100 * gross_win / abs(gross_loss)) / 100 if gross_loss != 0 else None,
        "total_realized_pnl": round(100 * (gross_win + gross_loss)) / 100,
        "avg_hold_sec": round(sum(holds) / len(holds)) if holds else None,
        "exit_reason_breakdown": by_exit,
        "by_setup": by_setup,
        "edge_note": (
            "FACT: expectancy_per_trade = realised mean PnL over closed rows in this "
            "bucket. Positive = the rule set has BEEN profitable on logged paper fills; "
            "not a prediction of future results."
        ),
    }


def aggregate_research() -> dict:
    signals = db.list_signals(limit=100000)
    trades = db.list_trades(limit=100000)

    by_decision, by_regime, by_risk = {}, {}, {}
    prob_sum, prob_n = 0.0, 0
    for s in signals:
        by_decision[s.get("decision") or "UNKNOWN"] = by_decision.get(s.get("decision") or "UNKNOWN", 0) + 1
        by_regime[s.get("market_regime") or "UNKNOWN"] = by_regime.get(s.get("market_regime") or "UNKNOWN", 0) + 1
        by_risk[s.get("risk_status") or "UNKNOWN"] = by_risk.get(s.get("risk_status") or "UNKNOWN", 0) + 1
        p = s.get("probability")
        if p is not None and p > 0:
            prob_sum += p
            prob_n += 1

    closed = [t for t in trades if str(t.get("status", "")).upper() == "CLOSED"]
    wins = losses = flat = 0
    pnl_sum = gross_win = gross_loss = 0.0
    for t in closed:
        pnl = t.get("pnl") or 0
        pnl_sum += pnl
        r = str(t.get("result") or "").upper()
        if r == "WIN" or pnl > 0:
            wins += 1
            gross_win += pnl
        elif r == "LOSS" or pnl < 0:
            losses += 1
            gross_loss += pnl
        else:
            flat += 1

    def _bucket(t):
        return str(t.get("strategy") or "CORE").upper()
    scalp_trades = [t for t in trades if _bucket(t) == "SCALP"]
    manual_trades = [t for t in trades if _bucket(t) == "MANUAL"]
    core_trades = [t for t in trades if _bucket(t) not in ("SCALP", "MANUAL")]

    open_trades = len([t for t in trades if str(t.get("status", "")).upper() == "OPEN"])
    win_rate = round(1000 * wins / len(closed)) / 10 if closed else None
    profit_factor = round(100 * gross_win / abs(gross_loss)) / 100 if gross_loss != 0 else None
    avg_pnl = round(100 * pnl_sum / len(closed)) / 100 if closed else None

    return {
        "generated_ts": datetime.now(timezone.utc).isoformat(),
        "probability_basis": "RULE_BASED_PROBABILITY",
        "probability_disclaimer": (
            "FACT: probabilities are deterministic rule outputs, NOT machine-learned and "
            "NOT statistically calibrated. No predictive-accuracy claim is made."
        ),
        "data_basis": (
            "DESCRIPTIVE: computed only from rows already written to ai_signals_log and "
            "ai_paper_trades. No forward prediction."
        ),
        "signals": {
            "total": len(signals),
            "by_decision": by_decision,
            "by_market_regime": by_regime,
            "by_risk_status": by_risk,
            "avg_rule_probability": round(10 * prob_sum / prob_n) / 10 if prob_n else None,
        },
        "paper_trades": {
            "total": len(trades),
            "open": open_trades,
            "closed": len(closed),
            "wins": wins, "losses": losses, "flat": flat,
            "win_rate_pct": win_rate,
            "avg_pnl_per_closed": avg_pnl,
            "total_realized_pnl": round(100 * pnl_sum) / 100,
            "profit_factor": profit_factor,
            "note": "FACT: realized paper-trade outcomes only; not indicative of future results.",
        },
        "by_strategy": {
            "SCALP": _strategy_stats(scalp_trades),
            "MANUAL": _strategy_stats(manual_trades),
            "CORE": _strategy_stats(core_trades),
        },
        "live_trading": False,
    }
