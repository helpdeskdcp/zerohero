"""Paper trading: hypothetical positions only. There is NO broker, order-manager or execution import anywhere in this
package (enforced by tests/test_smart_chart_security.py) - a BUY/SELL marker is never an order.

How a portfolio works: each `step()` re-runs the saved strategy on the latest CLOSED bars (PineTS broker emulator: signal on bar N
close -> simulated fill at bar N+1 open, plus configured slippage/commission) and records, idempotently, every trade whose
ENTRY is at/after the portfolio start time. History before the start is never "traded". A trade can only be recorded once
(UNIQUE portfolio_id+trade_key); a later step merely updates its exit. Entries that would open after the daily loss limit
was already breached that IST day are skipped and counted.
"""
from __future__ import annotations

from datetime import datetime

from . import backtest, candles, config, service, store
from .symbols import Instrument

_IST = candles.IST
WINDOW_BARS = 1500           # history fed to the strategy each step (warm-up + forward)


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, _IST).strftime("%Y-%m-%d")


def trade_key(t: dict) -> str:
    return f"{t['entry_time']}:{t['entry_id']}:{t['direction']}"


def create(owner: str, script: dict, inst: Instrument, tf: str, capital: float, commission_pct: float, slippage_ticks: float,
           daily_loss_limit: float, name: str | None = None) -> dict:
    if store.count_active_portfolios(owner) >= config.PAPER_MAX_PORTFOLIOS:
        raise service.ServiceError(409, "TOO_MANY_PORTFOLIOS", f"at most {config.PAPER_MAX_PORTFOLIOS} paper portfolios")
    if script["kind"] != "strategy":
        raise service.ServiceError(422, "NOT_A_STRATEGY", "paper trading needs a strategy script")
    if not (1_000 <= capital <= 1e9):
        raise service.ServiceError(422, "BAD_CAPITAL", "capital must be between 1,000 and 1,000,000,000")
    if not (0 <= commission_pct <= 5) or not (0 <= slippage_ticks <= 1000) or daily_loss_limit < 0:
        raise service.ServiceError(422, "BAD_PARAMS", "invalid commission / slippage / daily loss limit")
    import time
    pf = store.create_portfolio(owner, name or f"{script['name']} · {inst.id} {tf}", script["id"], script["version"], inst.id, tf,
                                capital, commission_pct, slippage_ticks, daily_loss_limit, int(time.time() * 1000))
    return pf


def start(owner: str, pid: str) -> dict:
    pf = store.get_portfolio(owner, pid)
    if not pf:
        raise service.ServiceError(404, "NOT_FOUND", "portfolio not found")
    if pf["status"] == "HALTED":
        raise service.ServiceError(409, "HALTED", f"portfolio halted: {pf['halted_reason']}")
    store.update_portfolio(owner, pid, status="RUNNING")
    store.audit(owner, "paper.start", "paper_portfolio", pid)
    return store.get_portfolio(owner, pid)


def stop(owner: str, pid: str) -> dict:
    pf = store.get_portfolio(owner, pid)
    if not pf:
        raise service.ServiceError(404, "NOT_FOUND", "portfolio not found")
    store.update_portfolio(owner, pid, status="STOPPED")
    store.audit(owner, "paper.stop", "paper_portfolio", pid)
    return store.get_portfolio(owner, pid)


def _cfg(pf: dict) -> dict:
    return {"initial_capital": pf["capital"], "commission_type": "percent", "commission_value": pf["commission_pct"]}


def step(owner: str, pid: str, inst: Instrument | None = None) -> dict:
    """Advance one portfolio with the latest closed bars. Safe to call repeatedly (idempotent)."""
    pf = store.get_portfolio(owner, pid)
    if not pf:
        raise service.ServiceError(404, "NOT_FOUND", "portfolio not found")
    if pf["status"] not in ("RUNNING",):
        return {"portfolio": pf, "advanced": False, "reason": f"portfolio is {pf['status']}"}
    script = store.get_script(owner, pf["script_id"])
    if not script:
        raise service.ServiceError(404, "SCRIPT_GONE", "the portfolio's script was deleted")
    from . import symbols
    inst = inst or symbols.resolve(pf["symbol"])
    rows, meta = service.load_bars(inst, pf["tf"], WINDOW_BARS, closed_only=True)
    props = {"initial_capital": pf["capital"], "commission_type": "percent", "commission_value": pf["commission_pct"],
             "slippage": pf["slippage_ticks"]}
    res = service._strategy_run(owner, script["source"], rows, inst, pf["tf"], script["inputs"], props)
    st = res["strategy"]
    cfg = _cfg(pf)
    candidates = sorted([t for t in st["closed_trades"] + st["open_trades"] if t["entry_time"] >= pf["start_ms"]], key=lambda t: t["entry_time"])
    day_realized: dict[str, float] = {}
    for t in candidates:
        if t.get("exit_time") is not None and t.get("profit") is not None:
            day_realized.setdefault(_day(t["exit_time"]), 0.0)
    skipped, new_trades, halted = [], 0, None
    realized_by_exit = sorted([t for t in candidates if t.get("exit_time") is not None], key=lambda t: t["exit_time"])
    for t in candidates:
        d = _day(t["entry_time"])
        pnl_before = sum(x["profit"] for x in realized_by_exit if _day(x["exit_time"]) == d and x["exit_time"] <= t["entry_time"] and x["profit"] is not None)
        if pf["daily_loss_limit"] and pnl_before <= -pf["daily_loss_limit"]:
            skipped.append({"key": trade_key(t), "reason": "DAILY_LOSS_LIMIT", "day": d})
            continue
        closed = t.get("exit_time") is not None
        if store.upsert_paper_trade(pid, trade_key(t), t["direction"], abs(t["size"]), t["entry_time"], t["entry_price"],
                                    t.get("exit_time"), t.get("exit_price"), t.get("commission") or 0.0, t.get("profit") if closed else None,
                                    "CLOSED" if closed else "OPEN"):
            new_trades += 1
    stored = store.list_paper_trades(pid, limit=10_000)
    realized = round(sum(x["pnl"] for x in stored if x["status"] == "CLOSED" and x["pnl"] is not None), 2)
    last_close = rows[-1]["close"]
    open_pnl = 0.0
    for x in stored:
        if x["status"] == "OPEN":
            sign = 1 if x["direction"] == "LONG" else -1
            open_pnl += sign * x["qty"] * (last_close - x["entry_price"]) - backtest.entry_commission(cfg, x["qty"], x["entry_price"])
    open_pnl = round(open_pnl, 2)
    ref_ms = max([rows[-1]["time"]] + [x["exit_ms"] for x in stored if x["exit_ms"]])          # the IST day being risk-checked: latest bar or latest exit
    today = _day(ref_ms)
    day_pnl = round(sum(x["pnl"] for x in stored if x["status"] == "CLOSED" and x["exit_ms"] and _day(x["exit_ms"]) == today and x["pnl"] is not None) + open_pnl, 2)
    if pf["daily_loss_limit"] and day_pnl <= -pf["daily_loss_limit"]:
        halted = f"daily loss limit {pf['daily_loss_limit']:.0f} reached on {today} (day P&L {day_pnl:.2f})"
    state = {"realized": realized, "open_pnl": open_pnl, "equity": round(pf["capital"] + realized + open_pnl, 2), "day": today, "day_pnl": day_pnl,
             "skipped": (pf["state"].get("skipped", []) + skipped)[-50:], "last_bar_ms": rows[-1]["time"], "trades": len(stored),
             "data_source": meta.get("source"), "market_open": meta.get("market_open"), "stale": meta.get("stale")}
    fields = {"state": state, "last_bar_ms": rows[-1]["time"], "cash": round(pf["capital"] + realized, 2)}
    if halted:
        fields.update(status="HALTED", halted_reason=halted)
        store.audit(owner, "paper.halt", "paper_portfolio", pid, {"reason": halted})
    store.update_portfolio(owner, pid, **fields)
    if new_trades or skipped:
        store.audit(owner, "paper.trades", "paper_portfolio", pid, {"new": new_trades, "skipped": len(skipped)})
    return {"portfolio": store.get_portfolio(owner, pid), "advanced": True, "new_trades": new_trades, "skipped": skipped, "halted": halted}
