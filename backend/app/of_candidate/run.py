"""OF-1 research simulation. Signals = app.pa_of_lab.confirm.of_signals (absorption / exhaustion on inferred-aggressor 5m footprint bars, data-quality gated). Trade = the FUTURE, entry at the
open of the bar after the signal, signal stop and 2R target (stop-first on the 5m bar), time exit 12 bars, flat at session end, one position at a time per symbol/day. Cost = COST_BPS round trip
(6.3 bps; a placeholder for MCX). READ-ONLY on the tick DB. Costs/defaults are the pre-registered ones; do not tune here."""
from __future__ import annotations

import math
import os

from ..pa_of_lab import confirm, ticks as tk

SYMBOLS = ("NIFTY", "BANKNIFTY", "NATURALGAS", "CRUDEOIL")
TF, COST_BPS, MAX_HOLD_BARS = 300, 6.3, 12
MIN_N, MIN_DAYS, ALPHA = 60, 20, 0.05
DB = tk.DB


def tick_days(symbol: str, path: str | None = None) -> list[str]:
    c = tk.connect_ro(path)
    try:
        rows = c.execute("SELECT DISTINCT date(ltt+19800,'unixepoch') FROM classified_ticks WHERE symbol=? AND kind='FUTURE' AND ltt>1000000000 ORDER BY 1", (symbol,)).fetchall()
    finally:
        c.close()
    return [r[0] for r in rows]


def day_bars(symbol: str, day: str, path: str | None = None) -> tuple[list[dict], dict]:
    rows, _ = tk.load_rows(symbol, "FUTURE", day, path)
    acc, rep = tk.prepare(rows, symbol)
    if not acc: return [], rep
    ts, _src = tk.infer_tick_size([t["ltp"] for t in acc])
    return tk.build_bars(acc, symbol, "FUTURE", TF, tick_size=ts, gaps=rep["gaps"], final_closed=True), rep


def simulate(bars: list[dict], sig: dict, cost_bps: float = COST_BPS) -> dict | None:
    i = sig["idx"]
    if i + 1 >= len(bars) or bars[i + 1]["t"] - bars[i]["t"] != bars[i]["tf_sec"]: return {"status": "NO_NEXT_BAR", "sig": sig}
    side, stop, tgt = sig["side"], sig["stop"], sig["t2"]; entry = bars[i + 1]["o"]; px = why = None; last = i + 1
    for k in range(i + 1, min(len(bars), i + 1 + MAX_HOLD_BARS)):
        if k > i + 1 and bars[k]["t"] - bars[k - 1]["t"] != bars[k]["tf_sec"]: px, why, last = bars[k - 1]["c"], "GAP_EXIT", k - 1; break
        b = bars[k]; last = k
        hit_stop = b["l"] <= stop if side > 0 else b["h"] >= stop
        hit_tgt = b["h"] >= tgt if side > 0 else b["l"] <= tgt
        if hit_stop: px, why = (min(b["o"], stop) if side > 0 else max(b["o"], stop)) if k == i + 1 else stop, "STOP"; break
        if hit_tgt: px, why = tgt, "TARGET"; break
    if px is None: px, why = bars[last]["c"], ("TIME" if last - (i + 1) + 1 >= MAX_HOLD_BARS else "SESSION_END")
    gross = side * (px - entry) / entry * 1e4
    return {"status": "TRADE", "symbol": bars[i]["symbol"], "day": None, "kind": sig["kind"], "side": side, "t_signal": bars[i]["t"], "entry": entry, "exit": px, "exit_reason": why, "gross_bps": gross,
            "net_bps": gross - cost_bps, "hold_bars": last - i, "exit_idx": last}


def run_day(symbol: str, day: str, path: str | None = None, cost_bps: float = COST_BPS) -> dict:
    bars, rep = day_bars(symbol, day, path)
    ok = sum(1 for b in bars if b["dq"]["status"] == "OK")
    sigs = confirm.of_signals(bars) if bars else []
    trades, busy_until = [], -1
    for s in sorted(sigs, key=lambda x: x["idx"]):
        if s["idx"] <= busy_until: continue
        r = simulate(bars, s, cost_bps)
        if r["status"] == "TRADE": r["day"] = day; trades.append(r); busy_until = r["exit_idx"]
    return {"symbol": symbol, "day": day, "bars": len(bars), "ok_bars": ok, "signals": len(sigs), "trades": trades, "prepare": {k: rep[k] for k in ("rows_in", "accepted", "duplicate_dropped", "stale_dropped", "late_dropped") if k in rep}, "gaps": len(rep.get("gaps", []))}


def run_all(path: str | None = None, symbols=SYMBOLS, cost_bps: float = COST_BPS) -> dict:
    days_out, trades = [], []
    for s in symbols:
        for d in tick_days(s, path):
            r = run_day(s, d, path, cost_bps); days_out.append({k: v for k, v in r.items() if k != "trades"} | {"n_trades": len(r["trades"])}); trades += r["trades"]
    return {"days": days_out, "trades": trades, "summary": evaluate(trades, days_out)}


def _wilson(k: int, n: int, z: float = 1.96):
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)); return ((c - h) / d, (c + h) / d)


def evaluate(trades: list[dict], days: list[dict] | None = None) -> dict:
    n = len(trades); out = {"n_trades": n, "verdict": "ACCUMULATING", "symbol_days_with_ticks": len(days or []), "ok_bar_share": None}
    if days:
        tb = sum(d["bars"] for d in days); out["ok_bar_share"] = (sum(d["ok_bars"] for d in days) / tb) if tb else None; out["signals"] = sum(d["signals"] for d in days)
    if n == 0: return out
    net = [t["net_bps"] for t in trades]; k = sum(1 for x in net if x > 0); w = [x for x in net if x > 0]; l = [-x for x in net if x <= 0]
    by = {}
    for t in trades: by.setdefault(t["day"], []).append(t["net_bps"])
    dm = [sum(v) / len(v) for v in by.values()]; nd = len(dm)
    be = (sum(l) / len(l)) / (sum(w) / len(w) + sum(l) / len(l)) if w and l else (0.0 if w else 1.0)
    lo, hi = _wilson(k, n); p = None
    if nd >= 3:
        mm = sum(dm) / nd; sd = math.sqrt(sum((x - mm) ** 2 for x in dm) / (nd - 1))
        if sd > 0: p = 0.5 * math.erfc((mm / (sd / math.sqrt(nd))) / math.sqrt(2))
    out.update(days=nd, win_rate=k / n, wilson95=[lo, hi], breakeven_win_rate=be, mean_gross_bps=sum(t["gross_bps"] for t in trades) / n, mean_net_bps=sum(net) / n, median_net_bps=sorted(net)[n // 2],
               p_day_clustered_one_sided=p, exits={r: sum(1 for t in trades if t["exit_reason"] == r) for r in {t["exit_reason"] for t in trades}}, mean_hold_bars=sum(t["hold_bars"] for t in trades) / n)
    if n >= MIN_N and nd >= MIN_DAYS:
        out["verdict"] = "PASS_FOR_NEXT_STAGE" if (out["mean_net_bps"] > 0 and p is not None and p <= ALPHA and lo > be) else "FAIL"
    return out
