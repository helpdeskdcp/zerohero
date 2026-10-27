"""Backtest accounting on top of PineTS trade ledgers (pure functions, no I/O).

PineTS (the strategy broker emulator) produces the trades; this module derives the equity curve and statistics
independently from those trades and the candles, and reconciles its net profit against PineTS's own total, so a
disagreement is visible instead of hidden.

ASSUMPTIONS (returned verbatim with every result; they are part of the product, not fine print):
"""
from __future__ import annotations

ASSUMPTIONS = [
    "Orders are placed on a bar's close and filled at the NEXT bar's open (market) - a signal never trades on the bar that produced it.",
    "Only CLOSED bars are used; the still-forming bar is excluded, so results cannot change retroactively on the current candle.",
    "100% margin (no leverage), as TradingView and the in-chart engine: an order larger than the available equity is REJECTED, not filled. PineTS's own //@version=5 default is no margin model; Smart Chart overrides it unless the script declares margin_long/margin_short.",
    "Fill prices are rounded to the 0.05 tick (NSE tick size; PineTS rounds fills to syminfo.mintick).",
    "Slippage (script/props 'slippage', in ticks of 0.05) always moves the fill against the trade; commission is charged per the script's commission settings.",
    "Stop/limit brackets (strategy.exit) are evaluated against each bar's OHLC. When both could trigger inside one bar the intrabar order is unknowable from OHLC data; PineTS's OHLC-path convention applies.",
    "No intrabar data: 1-minute (or finer) paths inside a bar are not simulated.",
    "Simulated fills are not broker fills: no queue position, partial fills, rejections, circuit limits, margin calls or latency. Index symbols are not directly tradable (a real trade would be a future/option with different costs).",
    "Index volume is not published by Upstox (always 0); volume-based logic on indices is meaningless.",
    "Statistics on fewer than 30 trades are not reliable.",
]


def entry_commission(cfg: dict, size: float, price: float) -> float:
    t, v = cfg.get("commission_type", "percent"), float(cfg.get("commission_value") or 0)
    if t == "percent":
        return abs(size) * price * v / 100.0
    if t == "cash_per_order":
        return v
    if t == "cash_per_contract":
        return abs(size) * v
    return 0.0


def equity_curve(candles: list[dict], closed: list[dict], open_: list[dict], cfg: dict) -> list[float]:
    """Mark-to-market equity at each bar close. Closed trades realize their NET profit (PineTS's `profit` includes both
    commission legs) on the exit bar; open trades are marked at the bar close less their entry commission."""
    cap = float(cfg["initial_capital"])
    n = len(candles)
    realized_at = [0.0] * (n + 1)
    for t in closed:
        eb = t["exit_bar"]
        if eb is not None and 0 <= eb < n:
            realized_at[eb] += t["profit"] or 0.0
    live = []
    for t in closed:
        if t["exit_bar"] is not None:
            live.append((t["entry_bar"], t["exit_bar"] - 1, t))     # open at the close of bars entry..exit-1
    for t in open_:
        live.append((t["entry_bar"], n - 1, t))
    unreal = [0.0] * n
    for a, b, t in live:
        if a is None:
            continue
        ec = entry_commission(cfg, t["size"], t["entry_price"])
        for i in range(max(a, 0), min(b, n - 1) + 1):
            unreal[i] += t["size"] * (candles[i]["close"] - t["entry_price"]) - ec
    eq, run = [], 0.0
    for i in range(n):
        run += realized_at[i]
        eq.append(round(cap + run + unreal[i], 2))
    return eq


def drawdown(eq: list[float]) -> tuple[float, float]:
    peak, max_abs, max_pct = float("-inf"), 0.0, 0.0
    for v in eq:
        peak = max(peak, v)
        dd = peak - v
        if dd > max_abs:
            max_abs = dd
        if peak > 0 and dd / peak > max_pct:
            max_pct = dd / peak
    return round(max_abs, 2), round(max_pct * 100, 2)


def compute_stats(candles: list[dict], closed: list[dict], open_: list[dict], cfg: dict, pine_summary: dict | None) -> tuple[dict, list[float]]:
    cap = float(cfg["initial_capital"])
    eq = equity_curve(candles, closed, open_, cfg)
    profits = [t["profit"] for t in closed if t["profit"] is not None]
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]
    gp, gl = sum(wins), -sum(losses)
    n = len(profits)
    dd_abs, dd_pct = drawdown(eq) if eq else (0.0, 0.0)
    streak = best = 0
    for p in profits:
        streak = streak + 1 if p < 0 else 0
        best = max(best, streak)
    in_mkt = set()
    for t in list(closed) + list(open_):
        a = t.get("entry_bar")
        b = t.get("exit_bar") if t.get("exit_bar") is not None else len(candles)
        if a is not None:
            in_mkt.update(range(a, min(b, len(candles))))
    final_eq = eq[-1] if eq else cap
    closed_profit = sum(profits)
    open_commission = sum((t.get("commission") or 0.0) for t in open_)           # PineTS charges entry commission on open trades at once
    last_close = candles[-1]["close"] if candles else 0.0
    open_pnl = sum(t["size"] * (last_close - t["entry_price"]) for t in open_)    # unrealized, gross of that commission
    net_profit = closed_profit - open_commission                                  # comparable with PineTS strategy.netprofit
    stats = {
        "initial_capital": cap, "final_equity": final_eq,
        "net_profit": round(net_profit, 2), "closed_profit": round(closed_profit, 2), "gross_profit": round(gp, 2), "gross_loss": round(gl, 2),
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
        "trades": n, "wins": len(wins), "losses": len(losses), "win_rate_pct": round(100 * len(wins) / n, 2) if n else None,
        "avg_trade": round(sum(profits) / n, 2) if n else None,
        "avg_win": round(gp / len(wins), 2) if wins else None, "avg_loss": round(-gl / len(losses), 2) if losses else None,
        "expectancy": round(sum(profits) / n, 2) if n else None,
        "max_drawdown": dd_abs, "max_drawdown_pct": dd_pct,
        "return_pct": round(100 * (final_eq - cap) / cap, 2) if cap else None,
        "buy_hold_pct": round(100 * (candles[-1]["close"] / candles[0]["close"] - 1), 2) if len(candles) > 1 else None,
        "exposure_pct": round(100 * len(in_mkt) / len(candles), 1) if candles else None,
        "longest_losing_streak": best, "open_trades": len(open_),
        "open_pnl": round(open_pnl, 2), "open_commission": round(open_commission, 2),
        "warnings": [],
    }
    if n < 30:
        stats["warnings"].append(f"only {n} closed trades: statistics are not statistically reliable")
    if pine_summary and pine_summary.get("netprofit") is not None:
        diff = round(stats["net_profit"] - pine_summary["netprofit"], 4)
        stats["reconciliation"] = {"pinets_net_profit": round(pine_summary["netprofit"], 4), "derived_net_profit": stats["net_profit"],
                                   "difference": diff, "ok": abs(diff) < 0.01}
        if abs(diff) >= 0.01:
            stats["warnings"].append(f"net profit differs from the PineTS total by {diff}; treat results with caution")
    return stats, eq


def _sig(t: dict) -> tuple:
    return (t["entry_bar"], t["exit_bar"], t["size"], round(t["entry_price"], 6),
            None if t["exit_price"] is None else round(t["exit_price"], 6))


def causality_diff(full: dict, trunc: dict, cut_bar: int) -> dict:
    """Prefix-invariance test: re-running on the first `cut_bar` bars must reproduce, exactly, every trade the full run had
    already closed by then (and the same entries). Any difference means the script saw the future (repaint / look-ahead)."""
    full_closed = {_sig(t) for t in full["closed_trades"] if t["exit_bar"] is not None and t["exit_bar"] <= cut_bar - 1}
    trunc_closed = {_sig(t) for t in trunc["closed_trades"] if t["exit_bar"] is not None and t["exit_bar"] <= cut_bar - 1}
    full_entries = {(t["entry_bar"], t["size"], round(t["entry_price"], 6)) for t in full["closed_trades"] + full["open_trades"] if t["entry_bar"] <= cut_bar - 1}
    trunc_entries = {(t["entry_bar"], t["size"], round(t["entry_price"], 6)) for t in trunc["closed_trades"] + trunc["open_trades"] if t["entry_bar"] <= cut_bar - 1}
    mism = sorted(full_closed ^ trunc_closed, key=str)[:10] + sorted(full_entries ^ trunc_entries, key=str)[:10]
    return {"ok": not mism, "checked_bars": cut_bar, "trades_compared": len(full_closed), "mismatches": mism}


def pick_cuts(full: dict, n_bars: int, max_cuts: int = 6, warmup: int = 40) -> list[int]:
    """Truncation points right after sampled trade entries/exits: the sharpest place to expose a script that needed future bars."""
    ts = full["closed_trades"] + full["open_trades"]
    pts = sorted({t["entry_bar"] + 1 for t in ts} | {t["exit_bar"] + 1 for t in ts if t["exit_bar"] is not None})
    pts = [p for p in pts if warmup <= p < n_bars]
    if len(pts) <= max_cuts:
        return pts
    step = len(pts) / max_cuts
    return [pts[int(i * step)] for i in range(max_cuts)]


def merge_causality(results: list[dict]) -> dict:
    bad = [r for r in results if not r["ok"]]
    return {"ok": not bad, "cuts_tested": len(results), "failing_cuts": [r["checked_bars"] for r in bad],
            "mismatches": [m for r in bad for m in r["mismatches"]][:10],
            "note": "sampled prefix-invariance test (history cut right after trade entries/exits). A pass does not prove the absence of look-ahead; a fail proves it."}
