"""Replay of the Safe AI rule over the CAPTURED sessions (market_history.db): real index ticks, real option bid/ask, settled by the arena's own engine (ask in / bid out, execution delay, session-end exit, full charges).
Caveat that makes the replay PESSIMISTIC: captured option quotes are ~1 per minute (the live signal gets 1-second capture), so fills and stop/target detection are up to a minute late. Python -m app.arena.safe_ai.replay [thrust]"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .. import settle as S, spread_model as SM
from . import signals as SG

IST = timezone(timedelta(hours=5, minutes=30))
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "data", "market_history.db")
SYMS = ("NIFTY", "SENSEX", "BANKNIFTY")
SL_FRAC, T1_FRAC = 0.25, 0.20          # stop 25 % of the premium, first target +20 % (reward:risk 0.8, like the institution trader's median 0.81)
MAX_TRADES_DAY, COOLDOWN_MIN, MAX_SPREAD, MIN_PREMIUM = 2, 30, 0.03, 30.0
MON = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()


def _ro(): return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=120)


def _ep(ts: str) -> float: return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def _iso_exp(raw: str) -> str: return datetime.strptime(raw.title(), "%d%b%Y").date().isoformat()


def days(c, since="2026-09-01") -> list[str]:
    return [r[0] for r in c.execute("SELECT DISTINCT session_date_ist FROM quote_snapshots WHERE symbol='NIFTY' AND kind='INDEX' AND session_date_ist>=? ORDER BY 1", (since,))]


def index_ticks(c, sym: str, day: str) -> list:
    """INDEX ticks of one session. Two indexed range reads (a plain session-day scan reads ~100 000 rows from disk per symbol and day): rows without an exchange stamp through (symbol, kind, exch_ts IS NULL, id range), rows
    with one through (symbol, kind, exch_ts range). Merged by capture time."""
    d0 = datetime.fromisoformat(day).replace(tzinfo=IST).replace(hour=9, minute=15).timestamp()
    lo = datetime.fromtimestamp(d0 - 1800, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"); hi = datetime.fromtimestamp(d0 + 6.5 * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    ids = c.execute("SELECT MIN(id), MAX(id) FROM quote_snapshots WHERE session_date_ist=? AND symbol=?", (day, sym)).fetchone()
    rows = c.execute("SELECT received_ts, ltp FROM quote_snapshots WHERE symbol=? AND kind='INDEX' AND exch_ts>=? AND exch_ts<=? AND ltp IS NOT NULL", (sym, lo, hi)).fetchall()
    if ids[0] is not None: rows += c.execute("SELECT received_ts, ltp FROM quote_snapshots WHERE symbol=? AND kind='INDEX' AND exch_ts IS NULL AND id BETWEEN ? AND ? AND ltp IS NOT NULL", (sym, ids[0], ids[1])).fetchall()
    out = []
    for ts, p in sorted((_ep(r[0]), r[1]) for r in rows):
        if d0 <= ts <= d0 + 6.2 * 3600 and (not out or ts > out[-1][0] + 0.5): out.append((ts, float(p)))
    return out


_chain: dict = {}


def chain_info(c, sym: str, day: str):
    """-> {iso_expiry: {"CE": [strikes], "PE": [strikes]}} captured that day. Read from two thin id slices of the (session_date, symbol) index (~5 minutes of capture each) instead of the whole day."""
    k = (sym, day)
    if k not in _chain:
        lo, hi = c.execute("SELECT MIN(id), MAX(id) FROM quote_snapshots WHERE session_date_ist=? AND symbol=?", (day, sym)).fetchone()
        m: dict = defaultdict(lambda: {"CE": set(), "PE": set()})
        if lo is not None:
            for a in (lo + (hi - lo) // 3, lo + (hi - lo) * 2 // 3):
                for e, ot, k2 in c.execute("SELECT DISTINCT expiry, option_type, strike FROM quote_snapshots WHERE session_date_ist=? AND symbol=? AND id BETWEEN ? AND ? AND kind='OPTION'", (day, sym, a, a + 40000)):
                    try: m[_iso_exp(e)][ot].add(float(k2))
                    except Exception: pass
        _chain[k] = {e: {o: sorted(v) for o, v in d.items()} for e, d in m.items()}
    return _chain[k]


def run(thrust: float = 0.005, since: str = "2026-09-01", until: str = "2026-12-31", verbose: bool = False) -> dict:
    p = SG.Params(thrust=thrust); c = _ro(); trades = []; skipped = defaultdict(int)
    for day in [d for d in days(c, since) if d <= until]:
        for sym in SYMS:
            ticks = index_ticks(c, sym, day)
            gaps = sorted(y[0] - x[0] for x, y in zip(ticks, ticks[1:]))
            if len(ticks) < 150 or gaps[len(gaps) // 2] > 90: skipped["sparse_index_day"] += 1; continue          # capture stalls / lock errors left too few index ticks to judge a turn
            ch = chain_info(c, sym, day); exps = sorted(e for e in ch if e >= day)
            if not exps: skipped["no_option_chain"] += 1; continue
            exp = exps[0]; expiry_day = (exp == day); n_day = 0; busy_until = 0.0; stopped = False
            for i in range(20, len(ticks)):
                t, px = ticks[i]
                if stopped or n_day >= MAX_TRADES_DAY or t < busy_until or not SG.in_window(t, p, expiry_day): continue
                sig = SG.detect(ticks[:i + 1], t, p)
                if not sig: continue
                strikes = ch[exp][sig["side"]]
                if not strikes: skipped["no_strikes"] += 1; continue
                k = min(strikes, key=lambda s: abs(s - px))
                snaps = S.load_snaps(sym, exp, k, sig["side"], day, DB, since=t - 120)
                nxt = [s for s in snaps if s["t"] >= t + 1]; pre = [s for s in snaps if s["t"] <= t]
                if not nxt: skipped["no_quote_after_signal"] += 1; continue
                q = pre[-1] if pre and t - pre[-1]["t"] < 180 else nxt[0]
                ask, bid = q.get("ask"), q.get("bid")
                if not ask or not bid or ask <= bid * 0.999 and False: skipped["no_bid_ask"] += 1; continue
                if ask < MIN_PREMIUM: skipped["premium_too_low"] += 1; continue
                if (ask - bid) / ask > MAX_SPREAD: skipped["spread_too_wide"] += 1; continue
                entry = round(round(ask / 0.05) * 0.05, 2); sl = round(round(entry * (1 - SL_FRAC) / 0.05) * 0.05, 2); t1 = round(round(entry * (1 + T1_FRAC) / 0.05) * 0.05, 2)
                hs = SM.half_spread(sym, [day], DB)
                s = {"id": f"R{len(trades)}", "participant": "SAFE", "underlying": sym, "expiry": exp, "strike": k, "option_type": sig["side"], "entry_mode": "NEAR", "entry": entry, "entry_hi": entry, "sl": sl, "targets": [t1], "t": t, "events": [], "lots": 1}
                r = S.settle(s, snaps, half_spread=hs["half_spread"], policy="T1", latency=1)
                n_day += 1
                rec = {"day": day, "sym": sym, "side": sig["side"], "t": datetime.fromtimestamp(t, IST).strftime("%H:%M:%S"), "expiry_day": expiry_day, "strike": k, "spot": px, "entry": entry, "status": r["status"],
                       "fill": r.get("fill"), "exit": r.get("exit"), "why": r.get("exit_reason"), "net": r.get("net_rs"), "net1": r.get("net_rs_1lot"), "hold_sec": r.get("hold_sec"), "reason": sig["reason"]}
                trades.append(rec)
                if verbose: print(rec["day"], sym, rec["t"], rec["side"], entry, rec["status"], rec["why"], rec["net"])
                if r["status"] == "SETTLED":
                    busy_until = r["fill_t"] + (r.get("hold_sec") or 0) + COOLDOWN_MIN * 60
                    if r["net_rs"] < 0: stopped = True                      # one loss ends the day for this index
                else: busy_until = t + COOLDOWN_MIN * 60
    c.close(); return {"params": p.as_dict(), "trades": trades, "skipped": dict(skipped)}


def summarize(trades: list, label: str = "") -> dict:
    ok = [t for t in trades if t["status"] == "SETTLED"]
    if not ok: return {"label": label, "trades": len(trades), "settled": 0}
    net = [t["net"] for t in ok]; w = [x for x in net if x > 0]; l = [x for x in net if x <= 0]
    # day-block bootstrap of the mean net per trade
    import random; rnd = random.Random(7); byday = defaultdict(list)
    for t in ok: byday[t["day"]].append(t["net"])
    dk = list(byday); means = []
    for _ in range(2000):
        pick = [x for d in (rnd.choice(dk) for _ in dk) for x in byday[d]]; means.append(sum(pick) / len(pick))
    means.sort()
    return {"label": label, "trades": len(trades), "settled": len(ok), "days": len(byday), "win_rate": round(len(w) / len(ok), 3), "net_rs": round(sum(net)), "net_per_trade": round(st.mean(net), 1),
            "ci95_per_trade": [round(means[50]), round(means[1949])], "avg_win": round(st.mean(w)) if w else 0, "avg_loss": round(st.mean(l)) if l else 0, "profit_factor": round(sum(w) / -sum(l), 2) if l and sum(l) < 0 else None,
            "worst": round(min(net)), "exits": {k: sum(1 for t in ok if t["why"] == k) for k in {t["why"] for t in ok}}}


if __name__ == "__main__":
    th = float(sys.argv[1]) if len(sys.argv) > 1 else 0.005
    out = run(th, verbose=True); tr = out["trades"]
    dev = [t for t in tr if t["day"] <= "2026-10-07"]; oos = [t for t in tr if t["day"] > "2026-10-07"]
    os.makedirs("data/research/safe_ai", exist_ok=True); json.dump(out, open(f"data/research/safe_ai/replay_thrust{int(th * 10000):04d}.json", "w"), indent=1, default=str)
    for lab, sub in (("ALL", tr), ("DEV 02-Sep..07-Oct", dev), ("HOLD-OUT 08-Oct..28-Oct", oos)): print(json.dumps(summarize(sub, lab)))
    print("skipped:", out["skipped"])
