"""Identical settlement for BOTH sides on captured option quotes (market_history.db quote_snapshots, Angel Quote-Full).  Rules (pre-registered, see ARENA_TERMS):
  * execution delay: first snapshot at/after signal time + LATENCY_SEC (same for both sides).
  * BUY fills at the ASK (observed); SELL (SL / target / time exit) at the BID. Where a snapshot has no usable bid/ask (all SENSEX and MCX option rows today), the fill is LTP -/+ the
    instrument's half-spread from spread_model (measured where data exists, else a stated fallback) - applied to BOTH sides.
  * ABOVE x: waits for ltp >= x, fills at max(ask, x). LIMIT x: buy limit, fills at the ask once ask <= x (valid to session end). NEAR/MARKET: next snapshot ask; MISSED if it is >5% above the stated entry high.
  * SL-at-fill (effective only for signals submitted after the deployment time in data/arena/rule_effective.json, never retroactive): if the stop is at/above the sell price of the fill snapshot, exit there at once (reason SL_AT_FILL).
  * management events (amend SL / targets, trailing stop, emergency exit, cancel) take effect 20 s after their server timestamp, never retroactively; the AI's own recorded management is replayed as the same event types.
  * exit on the first snapshot whose sell price <= SL (gap: that price) or >= target (T1 policy by default; 'LAST' = last target); forced exit at session end.
  * resolution is the snapshot interval (about 1-3 minutes per strike): brief spikes between snapshots are NOT seen, for either side.
  * costs: brokerage + STT/exchange/regulatory-fee/stamp/GST via the cost models, slippage already inside the fills (not added twice). One lot per signal."""
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


RULES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "arena", "rule_effective.json")


def sl_at_fill_from() -> float | None:
    """Epoch from which the SL-at-fill rule applies (written at deployment; NOT retroactive). Absent file -> rule inactive."""
    try:
        import json
        v = json.load(open(RULES_FILE)).get("sl_at_fill_from_ts"); return float(v) if v is not None else None
    except Exception:
        return None


def _apply(ev: dict, st: dict) -> None:
    a = ev["action"]
    if a == "AMEND_SL": st["sl"] = float(ev["sl"])
    elif a == "AMEND_TARGETS": st["targets"] = sorted({float(x) for x in ev["targets"]})
    elif a == "TRAIL_SL": st["trail_points"] = ev.get("trail_points"); st["trail_pct"] = ev.get("trail_pct")
    elif a == "CANCEL" or a == "EXIT_NOW": st[a] = True


def settle(sig: dict, snaps: list[dict], *, half_spread: float, policy: str = "T1", latency: int = LATENCY_SEC) -> dict:
    """Event-driven. sig: underlying, strike, option_type, entry_mode (ABOVE|NEAR|MARKET|LIMIT), entry (low), entry_hi (opt), sl, targets[], t (epoch) and optional events[]
    ({t, action: AMEND_SL|AMEND_TARGETS|TRAIL_SL|EXIT_NOW|CANCEL, ...}). An event is effective on the first snapshot at/after event.t + latency (same delay for every action, both sides);
    events never act retroactively and are ignored once the trade is closed. LIMIT: a buy limit at `entry`, fills at the ask as soon as ask <= entry (valid to session end).
    Returns status SETTLED | MISSED | NOT_TRIGGERED | CANCELLED | NO_DATA:reason."""
    und = sig["underlying"]; base = {"id": sig.get("id"), "participant": sig.get("participant"), "underlying": und, "strike": sig["strike"], "type": sig["option_type"], "t": sig["t"], "entry_mode": sig["entry_mode"]}
    try: lot = int(get_instrument_profile(und).lot_size.value)
    except Exception: lot = None
    if not lot: return {**base, "status": "NO_DATA:no_lot_size"}
    exch = "MCX" if und in MCX else "NSE"; end_min = SESSION_END_MIN[exch]; t0 = sig["t"] + latency
    live = [s for s in snaps if s["t"] >= t0]
    if not live: return {**base, "status": "NO_DATA:no_snapshot_after_signal"}
    evs = sorted(({**e, "t_eff": e["t"] + latency} for e in (sig.get("events") or []) if e.get("t", 0) >= sig["t"]), key=lambda e: e["t_eff"]); ei = 0
    st = {"sl": sig["sl"], "targets": list(sig["targets"]), "trail_points": None, "trail_pct": None}
    applied = 0

    def drain(upto: float):
        nonlocal ei, applied
        while ei < len(evs) and evs[ei]["t_eff"] <= upto: _apply(evs[ei], st); ei += 1; applied += 1

    fill = None; real_fill = False; j = None
    for i, s in enumerate(live):
        if _mod(s["t"]) >= end_min - 5: break
        drain(s["t"])
        if st.get("CANCEL") or st.get("EXIT_NOW"): return {**base, "status": "CANCELLED", "events_applied": applied}
        mode = sig["entry_mode"]; p, real = buy_px(s, half_spread)
        if mode == "ABOVE":
            if s["ltp"] >= sig["entry"]: fill, real_fill, j = max(p, sig["entry"]), real, i; break
        elif mode == "LIMIT":
            if p <= sig["entry"]: fill, real_fill, j = p, real, i; break
        else:                                                    # NEAR / MARKET: first snapshot only
            if p > sig.get("entry_hi", sig["entry"]) * (1 + RUNAWAY): return {**base, "status": "MISSED", "ask_at_entry": p}
            fill, real_fill, j = p, real, i; break
    if fill is None: return {**base, "status": "NOT_TRIGGERED", "events_applied": applied}
    hwm = sell_px(live[j], half_spread)[0]; px = why = None; k_exit = j; real_exit = False
    _from = sl_at_fill_from()
    if _from is not None and sig["t"] >= _from and st["sl"] is not None:                    # SL-at-fill rule: a stop already at/above the market at the moment of the fill is hit immediately
        drain(live[j]["t"]); sp0, real0 = sell_px(live[j], half_spread)
        if st["sl"] >= sp0: px, why, real_exit, k_exit = sp0, "SL_AT_FILL", real0, j
    for k in range(j + 1, len(live) if px is None else j + 1):
        s = live[k]; sp, real = sell_px(s, half_spread); k_exit = k; drain(s["t"]); hwm = max(hwm, sp)
        if _mod(s["t"]) >= end_min: px, real_exit, why = sp, real, "TIME"; break
        if st.get("EXIT_NOW"): px, real_exit, why = sp, real, "EMERGENCY"; break
        trail = None
        if st["trail_points"]: trail = hwm - float(st["trail_points"])
        elif st["trail_pct"]: trail = hwm * (1 - float(st["trail_pct"]) / 100.0)
        eff_sl = st["sl"] if trail is None else max(st["sl"] if st["sl"] is not None else -1e18, trail)
        if eff_sl is not None and sp <= eff_sl: px, real_exit, why = sp, real, ("TRAIL" if (trail is not None and trail >= (st["sl"] or -1e18)) else "SL"); break
        tg = (st["targets"][0] if policy == "T1" else st["targets"][-1]) if st["targets"] else None
        if tg is not None and sp >= tg: px, real_exit, why = sp, real, "TARGET"; break
    if px is None: return {**base, "status": "NO_DATA:no_exit_snapshot", "fill": fill}
    c = cost_points(und, fill, px, lot)
    if c is None: return {**base, "status": "NO_DATA:no_cost_model"}
    gross = (px - fill) * lot; cost = c * lot
    return {**base, "status": "SETTLED", "fill": fill, "exit": px, "exit_reason": why, "fill_real_quote": real_fill, "exit_real_quote": real_exit, "lot": lot, "gross_rs": gross, "cost_rs": cost,
            "net_rs": gross - cost, "hold_sec": live[k_exit]["t"] - live[j]["t"], "fill_t": live[j]["t"], "events_applied": applied}


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
