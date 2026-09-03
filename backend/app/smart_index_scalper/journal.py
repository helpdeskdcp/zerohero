"""
Paper-trade journal + metrics (spec section 43) — slice 4/6.

Reads closed SMART_SCALPER rows from ai_paper_trades (joined with
smart_scalper_signals for regime/profile context) and computes the standard
research metrics. Nothing is trained; nothing claims profitability.
"""
from __future__ import annotations

from collections import defaultdict

from .. import db


def _closed_rows(limit=5000):
    rows = [t for t in db.list_trades(strategy="SMART_SCALPER", limit=limit)
            if t.get("status") == "CLOSED" and t.get("pnl") is not None]
    sigs = {s["signal_id"]: s for s in db.list_smart_scalper_signals(limit=limit)}
    for t in rows:
        s = sigs.get(t.get("signal_id")) or {}
        t["_profile"] = s.get("profile")
        t["_regime"] = t.get("market_regime") or s.get("market_regime")
        t["_instrument"] = t.get("underlying")
    return rows


def _metrics(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0, "status": "NO_TRADES"}
    pnls = [float(t["pnl"]) for t in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    mfes = [t.get("mfe") for t in rows if t.get("mfe") is not None]
    maes = [t.get("mae") for t in rows if t.get("mae") is not None]
    # equity curve max drawdown (chronological — rows come newest-first, reverse)
    eq = peak = mdd = 0.0
    for p in reversed(pnls):
        eq += p
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    r_mults = []
    for t in rows:
        entry, sl = t.get("entry"), t.get("stop_loss")
        risk = abs(entry - sl) if (entry and sl) else None
        pts = (t.get("exit_price") - entry) if (t.get("exit_price") is not None and entry is not None) else None
        if risk and pts is not None:
            r_mults.append(pts / risk)
    return {
        "n": n,
        "win_rate": round(len(wins) / n, 4),
        "wins": len(wins), "losses": len(losses),
        "gross_profit": round(gross_win, 2), "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "expectancy": round(sum(pnls) / n, 4),
        "avg_win": round(gross_win / len(wins), 2) if wins else None,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
        "avg_r_multiple": round(sum(r_mults) / len(r_mults), 3) if r_mults else None,
        "max_drawdown": round(mdd, 2),
        "avg_mfe": round(sum(mfes) / len(mfes), 3) if mfes else None,
        "avg_mae": round(sum(maes) / len(maes), 3) if maes else None,
        "false_signal_rate": round(len(losses) / n, 4),
        "exit_reason_mix": _count(rows, "exit_reason"),
    }


def _count(rows, key):
    c = defaultdict(int)
    for r in rows:
        c[str(r.get(key) or "NONE")] += 1
    return dict(c)


def journal(limit=5000) -> dict:
    rows = _closed_rows(limit)
    by_profile = defaultdict(list)
    by_instrument = defaultdict(list)
    by_regime = defaultdict(list)
    for t in rows:
        by_profile[t.get("_profile") or "UNKNOWN"].append(t)
        by_instrument[t.get("_instrument") or "UNKNOWN"].append(t)
        by_regime[t.get("_regime") or "UNKNOWN"].append(t)
    return {
        "overall": _metrics(rows),
        "by_profile": {k: _metrics(v) for k, v in by_profile.items()},
        "by_instrument": {k: _metrics(v) for k, v in by_instrument.items()},
        "by_market_regime": {k: _metrics(v) for k, v in by_regime.items()},
        "open_positions": len([t for t in db.list_trades(status="OPEN", strategy="SMART_SCALPER", limit=50)]),
        "note": "UNCALIBRATED research journal — no profitability claim. "
                "Metrics stabilise only after slice-5 replay + a real sample.",
    }
