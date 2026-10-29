"""Backtest of the Safe AI rule on ~12 months of REAL Upstox 1-minute data: NIFTY index candles + expired NIFTY weekly option candles (data/historical/upstox/upstox_research.db).
Assumptions (all written down, all pessimistic where they matter):
  * the index series is one tick per minute (the bar close); a signal raised at minute m is executed at the OPEN of the next option bar (>= 1 minute of delay; live it is ~1-2 s)
  * no bid/ask in the data: every fill pays `half_spread` of the price on the way in and on the way out (sensitivity 0.25 / 0.5 / 1.0 %)
  * stop 25 % below / first target +20 % above the fill; they are checked against each minute's LOW and HIGH; if one bar touches both the STOP is assumed first; a bar that opens beyond a level fills at its open (gap)
  * exit at 15:15 if neither is hit; full round-trip charges from the arena's cost model; 1 lot (current NIFTY lot size)
  * same safety stack as live: window 09:35-14:30 (expiry day 13:30), <= 2 trades a day, one loss ends the day, 30-minute cool-down after an exit, ATM within one strike step of the spot, ask >= Rs 30
python -m app.arena.safe_ai.replay_hist [thrust] [half_spread] [uturn|sweep]"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .. import settle as S
from ...instrument_profiles import get_instrument_profile
from . import signals as SG

IST = timezone(timedelta(hours=5, minutes=30))
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "historical", "upstox", "upstox_research.db")
SL_FRAC, T1_FRAC, MIN_PREMIUM, MAX_TRADES_DAY, COOLDOWN_MIN = 0.25, 0.20, 30.0, 2, 30
MAX_DTE = 2                      # the Safe AI trades only on the expiry day and the two days before it (memory: ATM +/-4 strikes of the expiry-1 spot are pulled; gamma is highest there; Mohit: 18 of 29 index posts)
SPLIT = "2026-04-01"            # dev (Sep-2025 .. Mar-2026) / hold-out (Apr-2026 .. Oct-2026)


def _ts(x: str) -> float: return datetime.fromisoformat(x).timestamp()


def load(db=DB, und="NIFTY"):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
    idx = defaultdict(list); hl: dict = {}
    for t, o, h, l, cl in c.execute("SELECT timestamp, open, high, low, close FROM normalized_bars WHERE symbol=? AND timeframe='1m' ORDER BY timestamp", (und,)):
        idx[t[:10]].append((_ts(t) + 60.0, float(cl)))                       # a bar's close is known at its end
        a = hl.setdefault(t[:10], [float(h), float(l)]); a[0] = max(a[0], float(h)); a[1] = min(a[1], float(l))
    opt = defaultdict(dict)                                                  # (expiry, strike, type) -> {bar_start_epoch: (o,h,l,c)}
    for e, k, ot, t, o, h, l, cl, oi in c.execute("SELECT expiry, strike, option_type, timestamp, open, high, low, close, open_interest FROM normalized_option_bars WHERE underlying=? AND interval='1minute' AND open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL AND substr(timestamp,1,10) <= expiry AND substr(timestamp,1,10) >= date(expiry,'-2 day') ORDER BY timestamp", (und,)):
        opt[(e, float(k), ot)][_ts(t)] = (float(o), float(h), float(l), float(cl), None if oi is None else float(oi))
    c.close(); load.hl = hl; return idx, opt


def oi_confirms(opt, exp, k, side, t, minutes=15, step=50.0):
    """H-OI (pre-registered): over the last `minutes`, open interest of PUTs near the money grew faster than that of CALLS (put writing / call unwinding = support) for a CALL idea, and the mirror for a PUT idea.
    score = (dOI_put - dOI_call) / (OI_put + OI_call), summed over the ATM strike and its two neighbours; needs data on both sides."""
    dp = dc = tot = 0.0; ok = 0
    for kk in (k - step, k, k + step):
        for ot in ("CE", "PE"):
            bars = opt.get((exp, kk, ot))
            if not bars: continue
            now_b = [b for b in bars if t - 120 <= b <= t]; old_b = [b for b in bars if t - minutes * 60 - 120 <= b <= t - minutes * 60]
            if not now_b or not old_b: continue
            a, o = bars[max(now_b)][4], bars[max(old_b)][4]
            if a is None or o is None: continue
            ok += 1; tot += a
            if ot == "CE": dc += a - o
            else: dp += a - o
    if ok < 4 or tot <= 0: return False
    score = (dp - dc) / tot
    return score > 0 if side == "CE" else score < 0


def simulate(idx, opt, thrust=0.005, half_spread=0.005, since="2025-09-01", until="2026-12-31", family="uturn", sl_frac=None, t1_frac=None, oi_filter=False, und="NIFTY", atm_tol=50.0):
    sl_frac = SL_FRAC if sl_frac is None else sl_frac; t1_frac = T1_FRAC if t1_frac is None else t1_frac
    p = SG.Params(thrust=thrust) if family == "uturn" else SG.SweepParams(); hl = getattr(load, "hl", {}); days_sorted = sorted(idx); lot = int(get_instrument_profile(und).lot_size.value); trades = []; skipped = defaultdict(int)
    expiries = sorted({k[0] for k in opt}); strikes_by_exp = defaultdict(set)
    for (e, k, ot) in opt: strikes_by_exp[e].add(k)
    for day in sorted(d for d in idx if since <= d <= until):
        ticks = idx[day]
        if len(ticks) < 200: skipped["short_day"] += 1; continue
        exps = [e for e in expiries if e >= day]
        if not exps: skipped["no_option_data_for_day"] += 1; continue
        exp = exps[0]
        if (datetime.fromisoformat(exp).date() - datetime.fromisoformat(day).date()).days > MAX_DTE: skipped["dte_above_limit"] += 1; continue          # only days 0..2 before the expiry are loaded and traded
        expiry_day = exp == day; n_day = 0; busy = 0.0; stopped = False
        for i in range(20, len(ticks)):
            t, px = ticks[i]
            if stopped or n_day >= MAX_TRADES_DAY or t < busy or not SG.in_window(t, p, expiry_day): continue
            if family == "uturn": sig = SG.detect(ticks[:i + 1], t, p)
            else:
                j = days_sorted.index(day); prev = hl.get(days_sorted[j - 1]) if j > 0 else None
                sig = SG.detect_sweep(ticks[:i + 1], t, prev[0], prev[1], p) if prev else None
            if not sig: continue
            ks = strikes_by_exp.get(exp) or set()
            if not ks: skipped["no_strikes"] += 1; continue
            k = min(ks, key=lambda s: abs(s - px))
            if abs(k - px) > atm_tol: skipped["atm_strike_not_in_data"] += 1; continue
            if oi_filter and not oi_confirms(opt, exp, k, sig["side"], t, step=atm_tol): skipped["oi_not_confirming"] += 1; continue
            bars = opt.get((exp, k, sig["side"])) or {}
            nxt = sorted(b for b in bars if b >= t)
            if not nxt or nxt[0] - t > 180: skipped["no_option_bar_after_signal"] += 1; continue
            o0 = bars[nxt[0]][0]; fill = o0 * (1 + half_spread)
            if fill < MIN_PREMIUM: skipped["premium_too_low"] += 1; continue
            sl, tg = fill * (1 - sl_frac), fill * (1 + t1_frac); n_day += 1
            end_t = datetime.fromtimestamp(t, IST).replace(hour=15, minute=15, second=0).timestamp(); exit_px = why = exit_t = None
            for b in nxt[1:]:
                if b >= end_t: break
                o, h, l, cl = bars[b][:4]
                if o <= sl: exit_px, why = o, "SL(gap)"
                elif l <= sl: exit_px, why = sl, "SL"
                elif o >= tg: exit_px, why = o, "TARGET(gap)"
                elif h >= tg: exit_px, why = tg, "TARGET"
                if exit_px is not None: exit_t = b; break
            if exit_px is None:
                last = [b for b in nxt if b < end_t]
                if not last: skipped["no_exit_bar"] += 1; n_day -= 1; continue
                exit_px, why, exit_t = bars[last[-1]][3], "TIME", last[-1]
            sell = exit_px * (1 - half_spread); gross = (sell - fill) * lot; cost = S.cost_total_rs(und, fill, sell, lot, 1) or 0.0
            trades.append({"day": day, "t": datetime.fromtimestamp(t, IST).strftime("%H:%M"), "side": sig["side"], "expiry": exp, "dte": (datetime.fromisoformat(exp).date() - datetime.fromisoformat(day).date()).days, "strike": k, "fill": round(fill, 2),
                           "exit": round(sell, 2), "why": why, "gross": round(gross, 1), "cost": round(cost, 1), "net": round(gross - cost, 1), "thrust": sig["features"].get("thrust_pct", sig["features"].get("overshoot_pct"))})
            busy = exit_t + 60 + COOLDOWN_MIN * 60
            if gross - cost < 0: stopped = True
    return trades, dict(skipped)


def summarize(trades, label=""):
    if not trades: return {"label": label, "trades": 0}
    net = [t["net"] for t in trades]; w = [x for x in net if x > 0]; l = [x for x in net if x <= 0]
    byday = defaultdict(list)
    for t in trades: byday[t["day"]].append(t["net"])
    rnd = random.Random(11); dk = list(byday); means = []
    for _ in range(3000): pick = [x for d in (rnd.choice(dk) for _ in dk) for x in byday[d]]; means.append(sum(pick) / len(pick))
    means.sort(); eq = peak = dd = 0.0
    for t in sorted(trades, key=lambda x: (x["day"], x["t"])): eq += t["net"]; peak = max(peak, eq); dd = max(dd, peak - eq)
    return {"label": label, "trades": len(trades), "days_traded": len(byday), "win_rate": round(len(w) / len(net), 3), "net_rs": round(sum(net)), "net_per_trade": round(st.mean(net), 1), "ci95_per_trade": [round(means[75]), round(means[2924])],
            "avg_win": round(st.mean(w)) if w else 0, "avg_loss": round(st.mean(l)) if l else 0, "profit_factor": round(sum(w) / -sum(l), 2) if l and sum(l) < 0 else None, "worst": round(min(net)), "max_drawdown": round(dd),
            "exits": {k: sum(1 for t in trades if t["why"] == k) for k in sorted({t["why"] for t in trades})}}


if __name__ == "__main__":
    fam = sys.argv[3] if len(sys.argv) > 3 else "uturn"
    th = float(sys.argv[1]) if len(sys.argv) > 1 else 0.005; hs = float(sys.argv[2]) if len(sys.argv) > 2 else 0.005
    idx, opt = load(); tr, sk = simulate(idx, opt, th, hs, family=fam)
    os.makedirs("data/research/safe_ai", exist_ok=True); json.dump({"family": fam, "thrust": th, "half_spread": hs, "trades": tr, "skipped": sk}, open(f"data/research/safe_ai/hist_{fam}_t{int(th * 10000):04d}_hs{int(hs * 10000):04d}.json", "w"), indent=1)
    for lab, sub in (("ALL", tr), ("DEV <" + SPLIT, [t for t in tr if t["day"] < SPLIT]), ("HOLD-OUT >=" + SPLIT, [t for t in tr if t["day"] >= SPLIT])): print(json.dumps(summarize(sub, lab)))
    print("skipped:", sk, "| option expiries in data:", len({k[0] for k in opt}), "| index days:", len(idx))
