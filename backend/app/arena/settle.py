"""Identical settlement for BOTH sides on captured option quotes (market_history.db quote_snapshots, Angel Quote-Full).  Rules (pre-registered, see ARENA_TERMS):
  * execution delay: first snapshot at/after signal time + LATENCY_SEC (same for both sides).
  * BUY fills at the ASK (observed); SELL (SL / target / time exit) at the BID. Where a snapshot has no usable bid/ask (all SENSEX and MCX option rows today), the fill is LTP -/+ the
    instrument's half-spread from spread_model (measured where data exists, else a stated fallback) - applied to BOTH sides.
  * ABOVE x: waits for ltp >= x, fills at max(ask, x). LIMIT x: buy limit, fills at the ask once ask <= x (valid to session end). NEAR/MARKET: next snapshot ask; MISSED if it is >5% above the stated entry high.
  * SL-at-fill (effective only for signals submitted after the deployment time in data/arena/rule_effective.json, never retroactive): if the stop is at/above the sell price of the fill snapshot, exit there at once (reason SL_AT_FILL).
  * management events (amend SL / targets, trailing stop, emergency exit, cancel) take effect 20 s after their server timestamp, never retroactively; the AI's own recorded management is replayed as the same event types.
  * (from gap_chase_rules_from_ts) DATA_GAP: a quote gap > 60 s right before the fill/miss decision voids the signal; ABOVE x filling > 5% above x is MISSED; a first target already at/below the fill is INVALID_LEVELS.
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
EXIT_NOW_LATENCY_SEC = 3                      # EXIT_NOW only, from rule_effective.json `exit_now_latency_from_ts` (owner's decision 2026-10-26: a 20 s wait on an emergency exit turned a cut into a loss); every other action keeps LATENCY_SEC
RUNAWAY = 0.05
MAX_FILL_GAP_SEC = 60            # DATA_GAP rule: no quote for longer than this around the execution instant -> the fill price is unknowable, the signal is void (not a loss, not a win)
FUTURE_STAMP_SEC = 30          # exch_ts more than this ahead of received_ts = mis-stamped frame (23-Oct-2026 NG 265 PE 17:18:08 false SL)
IST_SEC = 19800
SESSION_END_MIN = {"NSE": 15 * 60 + 15, "MCX": 23 * 60 + 15}
MCX = {"NATURALGAS": "NATURALGAS_OPTION", "CRUDEOIL": "CRUDEOIL_OPTION", "GOLDM": "GOLDM_OPTION"}
MON = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()


def snap_expiry(iso: str) -> str:
    d = datetime.fromisoformat(iso); return f"{d.day:02d}{MON[d.month - 1]}{d.year}"


def _ep(ts: str) -> float: return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def load_snaps(underlying: str, expiry_iso: str, strike: float, ot: str, day: str, path: str | None = None, since: float | None = None, instrument: str = "OPTION") -> list[dict]:
    """Captured quotes of one contract for one session day. `since` (epoch) keeps only rows received at/after that instant: a settlement never needs the rows before its signal."""
    c = sqlite3.connect(f"file:{os.path.abspath(path or DB)}?mode=ro", uri=True, timeout=30)
    try:
        if instrument == "EQUITY":                                                                           # cash equity: one row series per NSE symbol (no expiry / strike)
            q = "SELECT received_ts,ltp,bid,ask,exch_ts FROM quote_snapshots WHERE symbol=? AND kind='EQUITY' AND session_date_ist=? AND ltp IS NOT NULL"; a = [underlying, day]
        else:
            q = "SELECT received_ts,ltp,bid,ask,exch_ts FROM quote_snapshots WHERE symbol=? AND kind='OPTION' AND expiry=? AND strike=? AND option_type=? AND session_date_ist=? AND ltp IS NOT NULL"
            a = [underlying, snap_expiry(expiry_iso), strike, ot, day]
        if since is not None:
            sx = datetime.fromtimestamp(since, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"); q += " AND (received_ts>=? OR exch_ts>=?)"; a += [sx, sx]
        rows = c.execute(q + " ORDER BY received_ts", a).fetchall()
    finally:
        c.close()
    out = []
    for r in rows:
        t = _ep(r[0])
        try: te = _ep(r[4]) if r[4] else None
        except ValueError: te = None
        if te is not None and te - t > FUTURE_STAMP_SEC: t = te                                              # exchange stamp ahead of the receive time: the quote cannot have been seen yet -> placed at its exchange time
        out.append({"t": t, "ltp": r[1], "bid": r[2], "ask": r[3]})
    out.sort(key=lambda x: x["t"])
    return [x for x in out if since is None or x["t"] >= since]


def buy_px(s: dict, h: float) -> tuple[float, bool]:
    a = s.get("ask"); return (float(a), True) if a and a > 0 and s.get("bid") and a > s["bid"] else (float(s["ltp"]) * (1 + h), False)


def sell_px(s: dict, h: float) -> tuple[float, bool]:
    b = s.get("bid"); return (float(b), True) if b and b > 0 and s.get("ask") and s["ask"] > b else (float(s["ltp"]) * (1 - h), False)


def _mod(t: float) -> int: return int((t + IST_SEC) // 60) % 1440


PROVISIONAL_COSTS = {"GOLDM": "PROVISIONAL gold-option charges (public rate schedule: brokerage Rs 20/order, CTT 0.05% of the sell premium, exchange 0.0418%, regulatory fee, stamp 0.003% of buy, GST 18%); not yet validated against a real contract note"}


def _gold_cost_points(entry: float, exit_: float, lot: int) -> float:
    buy, sell = entry * lot, max(exit_, 0.05) * lot
    brokerage = 2 * 20.0; exch = (buy + sell) * 0.000418; regfee = (buy + sell) * 0.000001; ctt = sell * 0.0005; stamp = buy * 0.00003; gst = 0.18 * (brokerage + exch + regfee)
    return (brokerage + exch + regfee + ctt + stamp + gst) / lot


def cost_points(und: str, entry: float, exit_: float, lot: int) -> float | None:
    if und == "GOLDM": return _gold_cost_points(entry, exit_, lot)
    if und in MCX:
        e = estimate_cost("MCX", MCX[und], slippage_points=0.0); return e.total_cost_points
    return estimate_index_option_cost(entry, max(exit_, 0.05), lot, lots=1, slippage_points=0.0)["total_cost_points"]


def cost_total_rs(und: str, entry: float, exit_: float, unit: int, lots: int = 1) -> float | None:
    """Round-trip cost in RUPEES for `lots` lots (formula lives in institutional_edge.costs so trading code can use it without importing this package)."""
    from ..institutional_edge.costs import option_round_trip_cost_rs
    return option_round_trip_cost_rs(und, entry, exit_, unit, lots)


RULES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "arena", "rule_effective.json")


def rule_from(name: str) -> float | None:
    """Epoch from which a named rule applies (rule_effective.json; NOT retroactive). Absent -> inactive."""
    try:
        import json
        v = json.load(open(RULES_FILE)).get(name); return float(v) if v is not None else None
    except Exception:
        return None


def latency_at(t: float, default: float = LATENCY_SEC) -> float:
    """Execution delay for a signal / management event stamped at `t`: LATENCY_SEC (20 s) unless rule_effective.json says otherwise. `latency_schedule` = [[from_ts, sec], ...] (owner's decisions: 3 s from 2026-10-26 13:13,
    1 s from 2026-10-29): the LAST step whose from_ts <= t applies, the same for every participant and both sides, NEVER retroactive (an earlier signal or event keeps the delay it was stamped under). Files from before the
    schedule only have `latency_from_ts` + `latency_sec` (one step). The value can only shorten the delay, never lengthen it."""
    try:
        import json
        d = json.load(open(RULES_FILE)); steps = d.get("latency_schedule")
        if not steps and d.get("latency_from_ts") is not None and d.get("latency_sec") is not None: steps = [[d["latency_from_ts"], d["latency_sec"]]]
        cur = None
        for f, v in sorted(([float(a), float(b)] for a, b in (steps or [])), key=lambda x: x[0]):
            if t >= f: cur = v
        if cur is not None: return min(float(default), cur)
    except Exception:
        pass
    return float(default)


def current_latency(now: float | None = None) -> float:
    import time as _t
    return latency_at(now or _t.time(), LATENCY_SEC)


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
    elif a == "AMEND_LOTS":
        if not st.get("filled"): st["lots"] = max(1, int(ev["lots"]))                                              # once the trade is active the quantity is frozen: the event is ignored
    elif a == "AMEND_ENTRY":
        if not st.get("filled") and ev.get("entry") is not None:                                           # a malformed event (no price) is ignored, never a crash                                                                                   # only while the order is still waiting (ABOVE / LIMIT); ignored once active
            st["entry"] = float(ev["entry"]); st["entry_hi"] = float(ev["entry_hi"]) if ev.get("entry_hi") else None
    elif a == "CANCEL" or a == "EXIT_NOW": st[a] = True


def equity_cost_rs(side: str, fill: float, exit_: float, qty: int) -> float:
    """Round-trip Rs cost of an equity trade by its REAL prices: a BUY buys at `fill` and sells at `exit_`, a SELL (short) sells at `fill` and buys back at `exit_`."""
    from ..institutional_edge.costs import equity_intraday_round_trip_cost_rs as _c
    return _c(fill, exit_, qty) if side != "SELL" else _c(exit_, fill, qty)


def _settle_short(sig: dict, snaps: list[dict], *, half_spread: float, policy: str, latency: int) -> dict:
    """A SELL (short) is the long engine run on the mirrored price y = K - x (K = 2 x entry): selling at the bid is buying at y_ask = K - bid, a stop ABOVE the entry is a stop below it in y, a target BELOW the entry is
    above it in y, a trailing stop in points mirrors exactly. The result is mapped back to real prices, so P&L = (sell price - buy-back price) x qty. The 5 % no-chase limit is measured on y (about 5 % of the entry)."""
    K = 2.0 * float(sig["entry"]); m = lambda x: None if x is None else K - float(x)
    ms = [{**x, "ltp": m(x["ltp"]), "bid": m(x.get("ask")), "ask": m(x.get("bid"))} for x in snaps]
    me = []
    for e in (sig.get("events") or []):
        e = dict(e)
        if e.get("action") == "AMEND_SL" and e.get("sl") is not None: e["sl"] = m(e["sl"])
        elif e.get("action") == "AMEND_TARGETS": e["targets"] = [m(x) for x in e["targets"]]
        elif e.get("action") == "AMEND_ENTRY" and e.get("entry") is not None: e["entry"] = m(e["entry"]); e["entry_hi"] = None
        me.append(e)
    msig = {**sig, "_mirror_K": K, "entry": m(sig["entry"]), "entry_hi": m(sig["entry"]), "sl": m(sig.get("sl")), "targets": sorted(m(x) for x in sig.get("targets") or []), "events": me}
    r = settle(msig, ms, half_spread=half_spread, policy=policy, latency=latency)
    for k in ("fill", "exit", "ask_at_entry", "target", "entry_now", "sl_now"):
        if r.get(k) is not None: r[k] = K - r[k]
    if r.get("targets_now") is not None: r["targets_now"] = sorted((K - x for x in r["targets_now"]), reverse=True)
    if r.get("last"): r["last"] = {**r["last"], "bid": m(r["last"].get("ask")), "ask": m(r["last"].get("bid")), "ltp": m(r["last"].get("ltp"))}
    return r


def settle(sig: dict, snaps: list[dict], *, half_spread: float, policy: str = "T1", latency: int = LATENCY_SEC) -> dict:
    """Event-driven. sig: underlying, strike, option_type, entry_mode (ABOVE|NEAR|MARKET|LIMIT), entry (low), entry_hi (opt), sl, targets[], t (epoch) and optional events[]
    ({t, action: AMEND_SL|AMEND_TARGETS|TRAIL_SL|EXIT_NOW|CANCEL, ...}). An event is effective on the first snapshot at/after event.t + latency (same delay for every action, both sides);
    events never act retroactively and are ignored once the trade is closed. LIMIT: a buy limit at `entry`, fills at the ask as soon as ask <= entry (valid to session end).
    Returns status SETTLED | MISSED | DATA_GAP | INVALID_LEVELS | NOT_TRIGGERED | CANCELLED | NO_DATA:reason."""
    und = sig["underlying"]; base = {"id": sig.get("id"), "participant": sig.get("participant"), "underlying": und, "strike": sig["strike"], "type": sig["option_type"], "t": sig["t"], "entry_mode": sig["entry_mode"]}
    eq = sig.get("instrument") == "EQUITY"; Kmir = sig.get("_mirror_K")
    if eq and sig.get("side") == "SELL" and Kmir is None: return _settle_short(sig, snaps, half_spread=half_spread, policy=policy, latency=latency)
    if eq: lot = 1                                                                                      # a share is the unit: quantity (shares) rides in `lots`
    else:
        try: lot = int(get_instrument_profile(und).lot_size.value)
        except Exception: lot = None
    if not lot: return {**base, "status": "NO_DATA:no_lot_size"}
    if sig.get("void_status"): return {**base, "status": sig["void_status"], "void_reason": sig.get("void_reason")}        # ledger correction (HUMAN_STATUS_CORRECTION): signal voided after the fact
    _rf = rule_from("gap_chase_rules_from_ts"); new_rules = _rf is not None and sig["t"] >= _rf                          # DATA_GAP + ABOVE-chase + stale-levels rules: only for signals posted after deployment
    exch = "MCX" if und in MCX else "NSE"; end_min = SESSION_END_MIN[exch]; t0 = sig["t"] + latency_at(sig["t"], latency)
    live = [s for s in snaps if s["t"] >= t0]
    prev_t = max((x["t"] for x in snaps if x["t"] < t0), default=sig["t"])                      # last quote known at the execution instant (the signal time if there is none)
    if not live: return {**base, "status": "NO_DATA:no_snapshot_after_signal"}
    _xf = rule_from("exit_now_latency_from_ts")                                                                                 # EXIT_NOW posted after deployment: short execution delay (same for every participant, both sides); earlier events keep 20 s
    evs = sorted(({**e, "t_eff": e["t"] + (min(latency_at(e["t"], latency), EXIT_NOW_LATENCY_SEC) if (e.get("action") == "EXIT_NOW" and _xf is not None and e["t"] >= _xf) else latency_at(e["t"], latency))}
                  for e in (sig.get("events") or []) if e.get("t", 0) >= sig["t"]), key=lambda e: e["t_eff"]); ei = 0
    st = {"sl": sig["sl"], "targets": list(sig["targets"]), "trail_points": None, "trail_pct": None, "lots": max(1, int(sig.get("lots") or 1)), "filled": False, "entry": sig["entry"], "entry_hi": sig.get("entry_hi")}
    applied = 0

    def drain(upto: float):
        nonlocal ei, applied
        while ei < len(evs) and evs[ei]["t_eff"] <= upto: _apply(evs[ei], st); ei += 1; applied += 1

    fill = None; real_fill = False; j = None
    for i, s in enumerate(live):
        if _mod(s["t"]) >= end_min - 5: break
        drain(s["t"])
        if st.get("CANCEL") or st.get("EXIT_NOW"): return {**base, "status": "CANCELLED", "events_applied": applied}
        vs = sig.get("valid_sec")
        if vs and s["t"] - sig["t"] > vs: break                      # a range/limit entry that is not filled inside its validity window EXPIRES (never filled hours later)
        mode = sig["entry_mode"]; p, real = buy_px(s, half_spread)
        gap = s["t"] - (live[i - 1]["t"] if i > 0 else prev_t)                                   # silence in the capture just before this quote
        if mode == "ABOVE":
            if s["ltp"] >= st["entry"]:
                top = st["entry_hi"]                                                              # an entry ZONE (above 570 - 575): no chasing above its upper end, wait until the ask is back inside
                if top and top > st["entry"] and p > top: continue
                if new_rules and gap > MAX_FILL_GAP_SEC: return {**base, "status": "DATA_GAP", "gap_sec": round(gap, 1)}
                if new_rules and not (top and top > st["entry"]) and p > st["entry"] * (1 + RUNAWAY): return {**base, "status": "MISSED", "ask_at_entry": p}      # ABOVE x already far past x when it fills: same no-chase limit as NEAR
                fill, real_fill, j = max(p, st["entry"]), real, i; break
        elif mode == "LIMIT":
            if p <= st["entry"]:
                if new_rules and gap > MAX_FILL_GAP_SEC: return {**base, "status": "DATA_GAP", "gap_sec": round(gap, 1)}
                fill, real_fill, j = p, real, i; break
        else:                                                    # NEAR / MARKET: first snapshot only
            if new_rules and gap > MAX_FILL_GAP_SEC: return {**base, "status": "DATA_GAP", "gap_sec": round(gap, 1)}
            if p > sig.get("entry_hi", sig["entry"]) * (1 + RUNAWAY): return {**base, "status": "MISSED", "ask_at_entry": p}
            fill, real_fill, j = p, real, i; break
    if fill is not None: st["filled"] = True                                                              # from here the quantity cannot change
    if fill is None:
        vu = (sig["t"] + sig["valid_sec"]) if sig.get("valid_sec") else None                                  # a limit / above entry may still be waiting until this instant (None = until the session end)
        return {**base, "status": "NOT_TRIGGERED", "events_applied": applied, "valid_until": vu, "entry_now": st["entry"], "sl_now": st["sl"], "targets_now": list(st["targets"]), "lots": st["lots"]}
    if new_rules and st["targets"]:                                                                       # levels set from a stale entry: the target is already behind the fill price -> a "TARGET" exit would be a loss
        drain(live[j]["t"]); tg0 = (st["targets"][0] if policy == "T1" else st["targets"][-1]) if st["targets"] else None
        if tg0 is not None and tg0 <= fill: return {**base, "status": "INVALID_LEVELS", "fill": fill, "target": tg0, "events_applied": applied}
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
        if tg is not None and sp >= tg and (sp > fill or not new_rules): px, real_exit, why = sp, real, "TARGET"; break
    if px is None:                                                                                      # still open: what a live screen needs (fill, lot, the stop / targets in force, the last quote)
        ls = live[-1]; return {**base, "status": "NO_DATA:no_exit_snapshot", "fill": fill, "fill_t": live[j]["t"], "lot": lot, "lots": st["lots"], "sl_now": st["sl"], "targets_now": list(st["targets"]), "last": {"t": ls["t"], "bid": ls.get("bid"), "ask": ls.get("ask"), "ltp": ls.get("ltp")}}
    c = 0.0 if eq else cost_points(und, fill, px, lot)
    if c is None: return {**base, "status": "NO_DATA:no_cost_model"}
    lots = st["lots"]
    if eq:                                                                                                  # real prices: undo the mirror for a short before the charges are computed
        rf, rx, sd = (Kmir - fill, Kmir - px, "SELL") if Kmir is not None else (fill, px, "BUY")
        ct1 = equity_cost_rs(sd, rf, rx, 1); ctn = equity_cost_rs(sd, rf, rx, lots)
    else: ct1 = cost_total_rs(und, fill, px, lot, 1); ctn = cost_total_rs(und, fill, px, lot, lots)
    if ct1 is None or ctn is None: return {**base, "status": "NO_DATA:no_cost_model", "fill": fill}
    gross = (px - fill) * lot * lots; cost = ctn; gross1 = (px - fill) * lot; cost1 = ct1
    return {**base, "status": "SETTLED", "fill": fill, "exit": px, "exit_reason": why, "fill_real_quote": real_fill, "exit_real_quote": real_exit, "lot": lot, "lots": lots, "gross_rs": gross, "cost_rs": cost, "net_rs_1lot": gross1 - cost1,
            "net_rs": gross - cost, "hold_sec": live[k_exit]["t"] - live[j]["t"], "fill_t": live[j]["t"], "events_applied": applied, **({"cost_note": PROVISIONAL_COSTS[und]} if und in PROVISIONAL_COSTS else {})}


def summarize(results: list[dict], capital: float = 50000.0) -> dict:
    ok = [r for r in results if r["status"] == "SETTLED"]; by = {}
    for r in results: k = r["status"].split(":")[0]; by[k] = by.get(k, 0) + 1
    out = {"signals": len(results), "by_status": by, "settled": len(ok), "capital": capital}
    if not ok: return out
    net = [r["net_rs"] for r in ok]; w = [x for x in net if x > 0]; l = [-x for x in net if x <= 0]; eq = peak = dd = 0.0
    for x in sorted(ok, key=lambda r: r["fill_t"]): eq += x["net_rs"]; peak = max(peak, eq); dd = max(dd, peak - eq)
    out.update(net_rs=sum(net), net_rs_1lot=sum(r.get("net_rs_1lot", r["net_rs"]) for r in ok), lots_traded=sum(r.get("lots", 1) for r in ok), return_pct=sum(net) / capital * 100.0, win_rate=len(w) / len(ok), gross_rs=sum(r["gross_rs"] for r in ok), cost_rs=sum(r["cost_rs"] for r in ok),
               profit_factor=(sum(w) / sum(l)) if l and sum(l) > 0 else None, max_drawdown_rs=dd, max_drawdown_pct=dd / capital * 100.0, avg_net_rs=sum(net) / len(ok),
               real_quote_fill_share=sum(1 for r in ok if r["fill_real_quote"] and r["exit_real_quote"]) / len(ok))
    return out
