"""Identical settlement for BOTH sides on captured option quotes (market_history.db quote_snapshots, Angel Quote-Full).  Rules (pre-registered, see ARENA_TERMS):
  * execution delay: first snapshot at/after signal time + LATENCY_SEC (same for both sides).
  * BUY fills at the ASK (observed); SELL (SL / target / time exit) at the BID. Where a snapshot has no usable bid/ask (all SENSEX and MCX option rows today), the fill is LTP -/+ the
    instrument's half-spread from spread_model (measured where data exists, else a stated fallback) - applied to BOTH sides.
  * ABOVE x: waits for ltp >= x, fills at max(ask, x). NEAR/MARKET: next snapshot ask; MISSED if it is >5% above the stated entry high.
  * exit on the first snapshot whose sell price <= SL (gap: that price) or >= target (T1 policy by default; 'LAST' = last target); forced exit at session end.
  * resolution is the snapshot interval (about 1-3 minutes per strike): brief spikes between snapshots are NOT seen, for either side.
  * costs: brokerage + STT/exchange/SEBI/stamp/GST via the cost models, slippage already inside the fills (not added twice). One lot per signal."""
from __future__ import annotations

import math
import os
import sqlite3
from datetime import datetime, timezone

from ..institutional_edge.costs import estimate_cost, estimate_index_option_cost
from ..instrument_profiles import get_instrument_profile

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "market_history.db")
LATENCY_SEC = 20
RUNAWAY = 0.05
IST_SEC = 19800
SESSION_END_MIN = {"NSE": 15 * 60 + 15, "MCX": 23 * 60 + 15}
MCX = {"NATURALGAS": "NATURALGAS_OPTION", "CRUDEOIL": "CRUDEOIL_OPTION"}
MON = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()


def snap_expiry(iso: str) -> str:
    d = datetime.fromisoformat(iso); return f"{d.day:02d}{MON[d.month - 1]}{d.year}"


def _ep(ts: str) -> float: return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def load_snaps(underlying: str, expiry_iso: str, strike: float, ot: str, day: str, path: str | None = None) -> list[dict]:
    c = sqlite3.connect(f"file:{os.path.abspath(path or DB)}?mode=ro", uri=True, timeout=30)
    try:
        rows = c.execute("SELECT received_ts,ltp,bid,ask FROM quote_snapshots WHERE symbol=? AND kind='OPTION' AND expiry=? AND strike=? AND option_type=? AND session_date_ist=? AND ltp IS NOT NULL ORDER BY received_ts",
                         (underlying, snap_expiry(expiry_iso), strike, ot, day)).fetchall()
    finally:
        c.close()
    return [{"t": _ep(r[0]), "ltp": r[1], "bid": r[2], "ask": r[3]} for r in rows]


def buy_px(s: dict, h: float) -> tuple[float, bool]:
    a = s.get("ask"); return (float(a), True) if a and a > 0 and s.get("bid") and a > s["bid"] else (float(s["ltp"]) * (1 + h), False)


def sell_px(s: dict, h: float) -> tuple[float, bool]:
    b = s.get("bid"); return (float(b), True) if b and b > 0 and s.get("ask") and s["ask"] > b else (float(s["ltp"]) * (1 - h), False)


def _mod(t: float) -> int: return int((t + IST_SEC) // 60) % 1440


def cost_points(und: str, entry: float, exit_: float, lot: int) -> float | None:
    if und in MCX:
        e = estimate_cost("MCX", MCX[und], slippage_points=0.0); return e.total_cost_points
    return estimate_index_option_cost(entry, max(exit_, 0.05), lot, lots=1, slippage_points=0.0)["total_cost_points"]


def settle(sig: dict, snaps: list[dict], *, half_spread: float, policy: str = "T1", latency: int = LATENCY_SEC) -> dict:
    """sig: underlying, strike, option_type, entry_mode, entry (low), entry_hi (opt), sl, targets[], t (epoch). Returns a result dict (status SETTLED|MISSED|NOT_TRIGGERED|NO_DATA)."""
    und = sig["underlying"]; base = {"id": sig.get("id"), "participant": sig.get("participant"), "underlying": und, "strike": sig["strike"], "type": sig["option_type"], "t": sig["t"], "entry_mode": sig["entry_mode"]}
    try: lot = int(get_instrument_profile(und).lot_size.value)
    except Exception: lot = None
    if not lot: return {**base, "status": "NO_DATA:no_lot_size"}
    exch = "MCX" if und in MCX else "NSE"; end_min = SESSION_END_MIN[exch]; t0 = sig["t"] + latency
    live = [s for s in snaps if s["t"] >= t0]
    if not live: return {**base, "status": "NO_DATA:no_snapshot_after_signal"}
    fill = None; used_real_fill = False; j = None
    if sig["entry_mode"] == "ABOVE":
        for i, s in enumerate(live):
            if _mod(s["t"]) >= end_min - 5: break
            if s["ltp"] >= sig["entry"]:
                p, real = buy_px(s, half_spread); fill, used_real_fill, j = max(p, sig["entry"]), real, i; break
        if fill is None: return {**base, "status": "NOT_TRIGGERED"}
    else:
        p, real = buy_px(live[0], half_spread)
        if p > sig.get("entry_hi", sig["entry"]) * (1 + RUNAWAY): return {**base, "status": "MISSED", "ask_at_entry": p}
        fill, used_real_fill, j = p, real, 0
    tgt = (sig["targets"][0] if policy == "T1" else sig["targets"][-1]) if sig["targets"] else None
    px = why = None; k_exit = j; real_exit = False
    for k in range(j + 1, len(live)):
        s = live[k]
        if _mod(s["t"]) >= end_min: px, real_exit = sell_px(s, half_spread); why = "TIME"; k_exit = k; break
        sp, real = sell_px(s, half_spread); k_exit = k
        if sig["sl"] is not None and sp <= sig["sl"]: px, why, real_exit = sp, "SL", real; break
        if tgt is not None and sp >= tgt: px, why, real_exit = sp, "TARGET", real; break
    if px is None: return {**base, "status": "NO_DATA:no_exit_snapshot", "fill": fill}
    c = cost_points(und, fill, px, lot)
    if c is None: return {**base, "status": "NO_DATA:no_cost_model"}
    gross = (px - fill) * lot; cost = c * lot
    return {**base, "status": "SETTLED", "fill": fill, "exit": px, "exit_reason": why, "fill_real_quote": used_real_fill, "exit_real_quote": real_exit, "lot": lot, "gross_rs": gross, "cost_rs": cost,
            "net_rs": gross - cost, "hold_sec": live[k_exit]["t"] - live[j]["t"], "fill_t": live[j]["t"]}


def summarize(results: list[dict], capital: float = 50000.0) -> dict:
    ok = [r for r in results if r["status"] == "SETTLED"]; by = {}
    for r in results: k = r["status"].split(":")[0]; by[k] = by.get(k, 0) + 1
    out = {"signals": len(results), "by_status": by, "settled": len(ok), "capital": capital}
    if not ok: return out
    net = [r["net_rs"] for r in ok]; w = [x for x in net if x > 0]; l = [-x for x in net if x <= 0]; eq = peak = dd = 0.0
    for x in sorted(ok, key=lambda r: r["fill_t"]): eq += x["net_rs"]; peak = max(peak, eq); dd = max(dd, peak - eq)
    out.update(net_rs=sum(net), return_pct=sum(net) / capital * 100.0, win_rate=len(w) / len(ok), gross_rs=sum(r["gross_rs"] for r in ok), cost_rs=sum(r["cost_rs"] for r in ok),
               profit_factor=(sum(w) / sum(l)) if l and sum(l) > 0 else None, max_drawdown_rs=dd, max_drawdown_pct=dd / capital * 100.0, avg_net_rs=sum(net) / len(ok),
               real_quote_fill_share=sum(1 for r in ok if r["fill_real_quote"] and r["exit_real_quote"]) / len(ok))
    return out
