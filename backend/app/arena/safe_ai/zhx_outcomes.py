"""ZHX forward OUTCOME tracker (read-only research evidence; submits nothing, touches no ledger).
For every signal the Safe-AI monitor logged (data/safe_ai/zhx_decisions.jsonl - TAKE, WATCH and SKIP alike, because ZHX itself and Safe AI's filter are both under test) it replays the captured 5-minute bars AFTER the signal bar
with EXACTLY the exit rules of zhx_v1.pine: entry at the signal bar close; stop first when stop and a target share a candle; targets T1/T2/T3 as logged; legs 40/30/30 (rest closes at T3 / stop / the last close of the entry session);
no break-even, no time stop. A signal is resolved only when its trade has ended (T3, stop, or the entry session is over); until then it stays pending.
Result rows (append-only, one per signal): realised R (index level), exits, MFE / MAE in R, minutes to T1, session bucket, the monitor decision. INDEX-LEVEL ONLY: option premium profitability stays UNVALIDATED.
data/safe_ai/zhx_outcomes.jsonl; summary() aggregates by decision and by entry window. Pure core (replay) is unit-tested; resolve_all() takes injected bars."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DECISIONS = os.path.join(BASE, "data", "safe_ai", "zhx_decisions.jsonl")
OUTCOMES = os.path.join(BASE, "data", "safe_ai", "zhx_outcomes.jsonl")
MCX = {"NATURALGAS", "CRUDEOIL", "GOLDM", "SILVER", "COPPER"}
LEG1, LEG2 = 0.40, 0.30
SESSION_END_MIN = {"NSE": 15 * 60 + 30, "MCX": 23 * 60 + 55}


def replay(sig: dict, bars: list[dict], now: float, tf_min: int = 5, sym: str = "") -> dict | None:
    """bars: ascending list of {t (epoch seconds of bar START), o, h, l, c}. Returns the outcome dict, or None while the trade is still open."""
    side = int(sig["side"]); entry = float(sig["entry"]); stop0 = float(sig["sl"]); tg = [float(sig["t1"]), float(sig["t2"]), float(sig["t3"])]
    risk = abs(entry - stop0)
    if risk <= 0: return {"error": "zero risk"}
    t0 = float(sig["bar_time"]); day0 = datetime.fromtimestamp(t0, IST).date(); h = [False, False, False]; mfe = mae = 0.0; t1_min = None; last_close = entry; reason = None; exit_t = None
    for b in bars:
        if b["t"] <= t0: continue
        d = datetime.fromtimestamp(b["t"], IST).date()
        if d != day0:                                                                  # next session began: square-off at the last close of the entry session
            reason = "EOD"; break
        hi, lo = b["h"], b["l"]
        fav = ((hi - entry) if side > 0 else (entry - lo)) / risk; adv = ((entry - lo) if side > 0 else (hi - entry)) / risk
        mfe = max(mfe, fav); mae = max(mae, adv)
        if (side > 0 and lo <= stop0) or (side < 0 and hi >= stop0): reason = "SL"; exit_t = b["t"]; break              # stop first on a shared candle (same as the Pine script)
        for i in range(3):
            if not h[i] and ((side > 0 and hi >= tg[i]) or (side < 0 and lo <= tg[i])):
                h[i] = True
                if i == 0 and t1_min is None: t1_min = round((b["t"] + tf_min * 60 - t0) / 60.0 - tf_min, 1)
        last_close = b["c"]
        if h[2]: reason = "T3"; exit_t = b["t"]; break
    if reason is None:
        end = SESSION_END_MIN["MCX" if sym in MCX else "NSE"]; nowm = datetime.fromtimestamp(now, IST); same_day = nowm.date() == day0
        if (not same_day and nowm.date() > day0) or (same_day and nowm.hour * 60 + nowm.minute >= end + 10): reason = "EOD"                  # the entry session is over
        else: return None
    r = [abs(x - entry) / risk for x in tg]; banked = (LEG1 * r[0] if h[0] else 0.0) + (LEG2 * r[1] if h[1] else 0.0); rest = 1.0 - (LEG1 if h[0] else 0.0) - (LEG2 if h[1] else 0.0)
    if reason == "T3": realised = banked + rest * r[2]
    elif reason == "SL": realised = banked + rest * ((stop0 - entry) * side / risk)
    else: realised = banked + rest * ((last_close - entry) * side / risk)
    mins = datetime.fromtimestamp(t0, IST).hour * 60 + datetime.fromtimestamp(t0, IST).minute
    return {"exit": reason, "realised_R": round(realised, 3), "t1": h[0], "t2": h[1], "t3": h[2], "mfe_R": round(mfe, 2), "mae_R": round(mae, 2), "minutes_to_t1": t1_min, "risk_pts": round(risk, 4),
            "window": "09:15-10:30" if mins <= 630 else "10:30-13:00" if mins <= 780 else "13:00+", "index_level_only": True}


def _read(path: str) -> list[dict]:
    out = []
    if os.path.exists(path):
        for l in open(path, encoding="utf-8"):
            try: out.append(json.loads(l))
            except Exception: pass
    return out


def resolve_all(now: float, bars_fn=None, decisions_path: str | None = None, outcomes_path: str | None = None) -> dict:
    """Resolve every logged signal that has ended and has no outcome row yet. bars_fn(sym, now) -> list of bar dicts (5m, ascending)."""
    dp, op = decisions_path or DECISIONS, outcomes_path or OUTCOMES
    if bars_fn is None:
        from .. import terminal as TM

        def bars_fn(sym, n):
            d = TM._bars_from_capture(sym, 5, n, days=12)
            return [{"t": ts.timestamp(), "o": float(r.open), "h": float(r.high), "l": float(r.low), "c": float(r.close)} for ts, r in zip(d["timestamp"], d.itertuples())] if len(d) else []
    done = {(r["symbol"], r["bar_time"]) for r in _read(op)}; added, pending, errors = 0, 0, 0; cache: dict = {}
    for row in _read(dp):
        sig = row.get("signal") or {}; sym = row.get("symbol")
        if not sig or (sym, sig.get("bar_time")) in done or None in (sig.get("entry"), sig.get("sl"), sig.get("t1"), sig.get("t2"), sig.get("t3")): continue
        try:
            if sym not in cache: cache[sym] = bars_fn(sym, now)
            res = replay(sig, cache[sym], now, sym=sym)
        except Exception:
            errors += 1; continue
        if res is None: pending += 1; continue
        if res.get("error"): errors += 1; continue
        out = {"symbol": sym, "bar_time": sig["bar_time"], "bar_ist": sig.get("bar_ist"), "side": sig["side"], "score": sig.get("score"), "decision": row.get("decision"), **res, "resolved_ist": datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d %H:%M:%S")}
        os.makedirs(os.path.dirname(op), exist_ok=True)
        with open(op, "a", encoding="utf-8") as f: f.write(json.dumps(out) + "\n")
        done.add((sym, sig["bar_time"])); added += 1
    return {"resolved_now": added, "pending": pending, "errors": errors}


def summary(outcomes_path: str | None = None) -> dict:
    rows = _read(outcomes_path or OUTCOMES); out = {"n": len(rows), "note": "index-level R (before costs); option-premium profitability UNVALIDATED; too few trades to conclude anything below ~100"}

    def agg(rs):
        if not rs: return {"n": 0}
        v = [x["realised_R"] for x in rs]; return {"n": len(rs), "mean_R": round(sum(v) / len(v), 3), "win_rate": round(sum(1 for x in v if x > 0) / len(v), 3), "t1_rate": round(sum(1 for x in rs if x["t1"]) / len(rs), 3),
                                                  "mean_mfe_R": round(sum(x["mfe_R"] for x in rs) / len(rs), 2), "mean_mae_R": round(sum(x["mae_R"] for x in rs) / len(rs), 2)}
    out["all"] = agg(rows)
    out["by_decision"] = {d: agg([r for r in rows if r.get("decision") == d]) for d in sorted({r.get("decision") for r in rows if r.get("decision")})}
    out["by_window"] = {w: agg([r for r in rows if r.get("window") == w]) for w in ("09:15-10:30", "10:30-13:00", "13:00+")}
    out["by_symbol"] = {s: agg([r for r in rows if r.get("symbol") == s]) for s in sorted({r.get("symbol") for r in rows})}
    return out
