"""Offline replay of the unified policy over the CLOSED paper ledger (read-only). Sequential: only trades the policy ALLOWS update its state (trades_today, consecutive losses,
realised P&L, open positions, cooldown); a blocked trade is simply removed and the allowed trades' P&L is taken as recorded. That ignores path dependence (a different set of
entries would have changed later fills), so the numbers are a DIAGNOSTIC of what the rules would have filtered, not a forecast. Inputs the ledger does not contain (feed age,
spread, EV, spot) are reported NOT_EVALUABLE, never guessed."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from .engine import Engine

IST = timezone(timedelta(hours=5, minutes=30))


def _t(s: str) -> datetime: return datetime.fromisoformat(s).astimezone(IST)


def load_ledger(db_path: str, strategy: str | None = None) -> list[dict]:
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30); c.row_factory = sqlite3.Row
    q = "SELECT trade_id,opened_ts,closed_ts,market,underlying,option_type,direction,strategy,entry,stop_loss,target_1,pnl FROM ai_paper_trades WHERE status='CLOSED' AND pnl IS NOT NULL AND opened_ts IS NOT NULL AND closed_ts IS NOT NULL"
    args = []
    if strategy and strategy != "ALL": q += " AND strategy=?"; args.append(strategy)
    rows = [dict(r) for r in c.execute(q + " ORDER BY opened_ts", args)]; c.close()
    return rows


def replay(rows: list[dict], engine: Engine, *, capital: float = 200000.0) -> dict:
    allowed: list[dict] = []; out = []
    for r in rows:
        ot, ct = _t(r["opened_ts"]), _t(r["closed_ts"]); day = ot.date()
        same_day = [a for a in allowed if a["_ot"].date() == day]
        closed_before = sorted([a for a in same_day if a["_ct"] <= ot], key=lambda a: a["_ct"])
        realised = sum(a["pnl"] for a in closed_before)
        cl = 0
        for a in reversed(closed_before):
            if a["pnl"] < 0: cl += 1
            else: break
        last_loss = next((a for a in reversed(closed_before) if a["pnl"] < 0), None)
        open_now = [a for a in same_day if a["_ot"] <= ot < a["_ct"]]
        rr = None
        if r["entry"] and r["stop_loss"] and r["target_1"] and r["entry"] > r["stop_loss"]: rr = (r["target_1"] - r["entry"]) / (r["entry"] - r["stop_loss"])
        ctx = {"exchange": r["market"], "minute_of_day": ot.hour * 60 + ot.minute, "is_holiday": ot.weekday() >= 5, "trades_today": len(same_day),
               "consecutive_losses": cl, "daily_pnl_pct": realised / capital * 100.0, "open_positions": len(open_now),
               "duplicate_open_position": any(a["underlying"] == r["underlying"] and a["option_type"] == r["option_type"] for a in open_now),
               "minutes_since_last_loss": ((ot - last_loss["_ct"]).total_seconds() / 60.0) if last_loss else 1e9, "premium": r["entry"], "reward_risk": rr,
               "kill_switch_active": False, "live_orders_requested": False, "evidence_status": "UNVALIDATED"}
        d = engine.evaluate(ctx, on_missing="skip")
        out.append({"trade_id": r["trade_id"], "pnl": r["pnl"], "allow": d.allow, "blocked_by": [b["rule"] for b in d.blocked_by], "date": str(day), "strategy": r["strategy"]})
        if d.allow: allowed.append({**r, "_ot": ot, "_ct": ct})
    return {"rows": out, "summary": summarize(out), "not_evaluable": sorted({n["rule"] for n in engine.evaluate(ctx, on_missing="skip").not_evaluable}) if rows else []}


def _grp(rs):
    n = len(rs)
    if not n: return {"n": 0, "sum": 0.0, "mean": None, "win_rate": None}
    return {"n": n, "sum": round(sum(r["pnl"] for r in rs), 2), "mean": round(sum(r["pnl"] for r in rs) / n, 2), "win_rate": round(sum(1 for r in rs if r["pnl"] > 0) / n, 4)}


def summarize(rows: list[dict]) -> dict:
    by_rule: dict[str, int] = {}
    for r in rows:
        for b in r["blocked_by"]: by_rule[b] = by_rule.get(b, 0) + 1
    days = {r["date"] for r in rows}
    return {"trades": len(rows), "days": len(days), "all": _grp(rows), "allowed": _grp([r for r in rows if r["allow"]]), "blocked": _grp([r for r in rows if not r["allow"]]), "blocked_by_rule": dict(sorted(by_rule.items(), key=lambda x: -x[1]))}
