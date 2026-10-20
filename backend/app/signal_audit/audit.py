"""Audit option signals against REAL option 5m bars (Upstox expired-contract store: normalized_option_bars). Pessimistic and causal:
  * the bar CONTAINING the message is never used (message time is inside it); everything starts at the NEXT 5m bar.
  * ABOVE x : fills at max(bar open, x) on the first later bar whose high >= x.  NEAR/RANGE x : fills at the next bar open if open <= entry_hi*(1+RUNAWAY) else MISSED.
  * same bar touching SL and a target -> stop first.  Gap through SL -> fill at the open.  Flat at the last bar close (~15:15) if nothing hits.
  * costs: statutory + brokerage + slippage per leg via app.strategy_lab.costs.option_buy_round_trip_points (lot per underlying).
Per signal it also reports timing: was the target already reached BEFORE the message (POSTED_AFTER_T1), and lead minutes from the message to the first target touch.
Output statuses: PRICED | NOT_TRIGGERED | MISSED | NO_DATA(reason).  Nothing is imputed for NO_DATA."""
from __future__ import annotations

import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from ..strategy_lab.costs import option_buy_round_trip_points
from .parse import Signal, Update, link_updates

IST_SEC = 19800
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "historical", "upstox", "upstox_research.db")
LOTS = {"NIFTY": 65}                    # only underlyings with a verified lot size AND data are auditable
RUNAWAY = 0.05
SESSION_END_MIN = 15 * 60 + 15          # last bar START considered = 15:10 (close 15:15)


def _ro(path):
    return sqlite3.connect(f"file:{os.path.abspath(path or DB)}?mode=ro", uri=True, timeout=30)


def _epoch(ts: str) -> int:
    return int(datetime.fromisoformat(ts).timestamp())


class Store:
    def __init__(self, path: str | None = None):
        self.c = _ro(path); self._exp = None

    def expiries(self, und: str) -> list[str]:
        if self._exp is None:
            self._exp = {}
        if und not in self._exp:
            self._exp[und] = [r[0] for r in self.c.execute("SELECT DISTINCT expiry FROM normalized_option_bars WHERE underlying=? AND interval='5minute' ORDER BY 1", (und,))]
        return self._exp[und]

    def bars(self, und, expiry, strike, ot, day) -> list[tuple]:
        rows = self.c.execute("SELECT timestamp,open,high,low,close FROM normalized_option_bars WHERE underlying=? AND expiry=? AND strike=? AND option_type=? AND interval='5minute' "
                              "AND substr(timestamp,1,10)=? ORDER BY timestamp", (und, expiry, strike, ot, day)).fetchall()
        return [(_epoch(r[0]), r[1], r[2], r[3], r[4]) for r in rows if None not in r[1:]]


def resolve_expiry(st: Store, sig: Signal) -> tuple[str | None, str | None]:
    ex = st.expiries(sig.underlying)
    if not ex: return None, "NO_DATA:underlying_not_in_store"
    if sig.expiry:
        return (sig.expiry, None) if sig.expiry in ex else (None, "NO_DATA:stated_expiry_not_in_store")
    nxt = [e for e in ex if e >= sig.date_ist]
    if not nxt or (datetime.fromisoformat(nxt[0]) - datetime.fromisoformat(sig.date_ist)).days > 7: return None, "NO_DATA:no_expiry_within_7d"
    return nxt[0], None


def _mod(t: int) -> int: return ((t + IST_SEC) // 60) % 1440


def simulate(sig: Signal, bars: list[tuple], policy: str = "T1") -> dict:
    """policy: 'T1' = exit everything at the first target; 'LAST' = at the last target. SL always full exit."""
    lot = LOTS[sig.underlying]
    j0 = next((i for i, b in enumerate(bars) if b[0] >= (sig.t // 300 + 1) * 300), None)
    pre = [b for b in bars if b[0] + 300 <= sig.t]                              # bars fully completed before the message
    t1 = sig.targets[0] if sig.targets else None
    out = {"status": None, "pre_msg_max_high": max((b[2] for b in pre), default=None), "posted_after_t1": bool(t1 is not None and pre and max(b[2] for b in pre) >= t1)}
    if j0 is None: out["status"] = "NO_DATA:no_bars_after_message"; return out
    fill = j = None
    if sig.entry_mode == "ABOVE":
        for i in range(j0, len(bars)):
            if _mod(bars[i][0]) > SESSION_END_MIN - 5: break
            if bars[i][2] >= sig.entry_lo: fill, j = max(bars[i][1], sig.entry_lo), i; break
        if fill is None: out["status"] = "NOT_TRIGGERED"; return out
    else:
        if bars[j0][1] <= sig.entry_hi * (1 + RUNAWAY): fill, j = bars[j0][1], j0
        else: out["status"] = "MISSED"; out["open_at_next_bar"] = bars[j0][1]; return out
    tgt = (sig.targets[0] if policy == "T1" else sig.targets[-1]) if sig.targets else None
    px, why, k_exit = None, "EOD", j
    mfe, mae = 0.0, 0.0
    first_t1_bar = None
    for k in range(j, len(bars)):
        t, o, h, l, c = bars[k]
        if _mod(t) > SESSION_END_MIN - 5: break
        k_exit = k
        if t1 is not None and first_t1_bar is None and h >= t1: first_t1_bar = k
        if sig.sl is not None and (l <= sig.sl or (k > j and o <= sig.sl)):
            px, why = (o if (k > j and o <= sig.sl) else sig.sl), "SL"; break
        mfe, mae = max(mfe, h - fill), min(mae, l - fill)
        if tgt is not None and h >= tgt:
            px, why = max(tgt, o if k > j else tgt), "TARGET"; break
    if px is None: px = bars[k_exit][4]
    gross = px - fill
    cost = option_buy_round_trip_points(fill, max(px, 0.05), lot)
    out.update(status="PRICED", policy=policy, fill=fill, fill_t=bars[j][0], exit=px, exit_t=bars[k_exit][0], exit_reason=why, gross_pts=gross, cost_pts=cost, net_pts=gross - cost,
               net_pct=(gross - cost) / fill * 100 if fill else None, mfe=mfe, mae=mae, lot=lot,
               lead_min=((bars[first_t1_bar][0] - sig.t) / 60) if first_t1_bar is not None else None,
               hit_t1_before_sl=bool(first_t1_bar is not None and (why != "SL" or k_exit >= first_t1_bar)))
    return out


def audit_signal(st: Store, sig: Signal, policy: str = "T1") -> dict:
    base = {"msg_id": sig.msg_id, "date": sig.date_ist, "t": sig.t, "underlying": sig.underlying, "strike": sig.strike, "type": sig.opt_type, "entry": sig.entry_lo, "entry_mode": sig.entry_mode,
            "sl": sig.sl, "targets": sig.targets, "edited": sig.edited}
    if sig.underlying not in LOTS: return {**base, "status": "NO_DATA:underlying_not_supported(lot/data)"}
    if sig.side != "BUY": return {**base, "status": "NO_DATA:short_option_not_modelled"}
    exp, err = resolve_expiry(st, sig)
    if err: return {**base, "status": err}
    bars = st.bars(sig.underlying, exp, sig.strike, sig.opt_type, sig.date_ist)
    if not bars: return {**base, "status": "NO_DATA:strike_or_day_not_in_store", "expiry": exp}
    return {**base, "expiry": exp, **simulate(sig, bars, policy)}


def summarize(rows: list[dict], links: dict[int, list[Update]], parse_stats: dict, export_meta: dict) -> dict:
    priced = [r for r in rows if r["status"] == "PRICED"]
    by_status = {}
    for r in rows:
        k = r["status"].split(":")[0] if r["status"].startswith("NO_DATA") else r["status"]
        by_status[k] = by_status.get(k, 0) + 1
    no_data_reasons = {}
    for r in rows:
        if r["status"].startswith("NO_DATA"): no_data_reasons[r["status"]] = no_data_reasons.get(r["status"], 0) + 1
    s = {"parse": parse_stats, "export": export_meta, "signals": len(rows), "by_status": by_status, "no_data_reasons": no_data_reasons, "priced": len(priced)}
    claims = {"signals_with_any_update": sum(1 for r in rows if links.get(r["msg_id"])), "claimed_sl_hit": sum(1 for r in rows if any(u.kind == "SL_HIT" for u in links.get(r["msg_id"], []))),
              "claimed_target_hit": sum(1 for r in rows if any(u.kind == "T_HIT" for u in links.get(r["msg_id"], [])))}
    claims["no_outcome_posted"] = len(rows) - claims["signals_with_any_update"]
    s["channel_claims"] = claims
    if len(priced) >= 3:
        n = [r["net_pts"] for r in priced]; g = [r["gross_pts"] for r in priced]; N = len(n)
        m = sum(n) / N; sd = math.sqrt(sum((x - m) ** 2 for x in n) / (N - 1)); se = sd / math.sqrt(N)
        days = {}
        for r in priced: days.setdefault(r["date"], []).append(r["net_pts"])
        dm = [sum(v) / len(v) for v in days.values()]
        pc = None
        if len(dm) >= 3:
            mm = sum(dm) / len(dm); sdd = math.sqrt(sum((x - mm) ** 2 for x in dm) / (len(dm) - 1)); sec = sdd / math.sqrt(len(dm)); tc = mm / sec if sec else 0.0
            pc = {"t": tc, "p_one_sided_normal": 0.5 * math.erfc(tc / math.sqrt(2)), "days": len(dm)}
        wins = [x for x in n if x > 0]; losses = [x for x in n if x <= 0]
        eq = 0.0; peak = 0.0; dd = 0.0
        for x in n: eq += x; peak = max(peak, eq); dd = max(dd, peak - eq)
        s["performance"] = {"n": N, "win_rate_net": len(wins) / N, "mean_gross_pts": sum(g) / N, "mean_net_pts": m, "se": se, "t": m / se if se else 0.0,
                            "day_clustered": pc, "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None, "max_drawdown_pts_sum": dd,
                            "loss_count_share": len(losses) / N, "loss_amount_share": (-sum(losses) / (sum(wins) - sum(losses))) if (wins or losses) else None,
                            "sl_exits": sum(1 for r in priced if r["exit_reason"] == "SL"), "target_exits": sum(1 for r in priced if r["exit_reason"] == "TARGET"), "eod_exits": sum(1 for r in priced if r["exit_reason"] == "EOD")}
        s["timing"] = {"posted_after_t1": sum(1 for r in priced if r["posted_after_t1"]), "lead_min_median": sorted([r["lead_min"] for r in priced if r["lead_min"] is not None])[len([1 for r in priced if r["lead_min"] is not None]) // 2] if any(r["lead_min"] is not None for r in priced) else None,
                       "lead_5_to_15": sum(1 for r in priced if r["lead_min"] is not None and 5 <= r["lead_min"] <= 15)}
        s["mde_80pct_pts"] = 2.49 * se
    else:
        s["performance"] = None; s["note"] = "fewer than 3 priced signals: no statistics are reported (UNVERIFIED)"
    return s
