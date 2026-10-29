"""Live runner of the Safe AI (paper). `Engine.tick()` is the whole decision loop with every dependency injected (tests use fakes); `Runner` is the thread that wires the real ones.

Safety stack, in the order it is applied (each refusal is written to data/safe_ai/decisions.jsonl with its reason, once per 5 min per symbol/reason):
  kill switches (SAFE_AI_LIVE=0 stops the runner, SAFE_AI_SUBMIT=0 = decide + log only) -> arena mode (never after the contest) -> exchange open -> time window (09:35-14:30, expiry day of the traded option 09:35-13:30)
  -> fresh index price (<= 20 s) -> day lock (one loss ends the day for that index; the day's total net <= -DAY_LOSS_RS ends it for all) -> at most MAX_TRADES_DAY trades a day in total, MAX_PER_SYMBOL per index, ONE position open at a time
  -> the U-turn rule (signals.py) -> an ATM option of the nearest expiry with a quote not older than 75 s, ask >= Rs 30, spread <= 3 % -> 1 lot, stop 25 % of the premium, first target +20 %.
It never raises into the application, never places a broker order (the arena has no order path), and claims no edge."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ..arena import jsonio
from ..arena.markets import EXCH
from . import signals as SG

_log = logging.getLogger("safe_ai")
IST = timezone(timedelta(hours=5, minutes=30))
HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIR = os.path.join(HERE, "data", "safe_ai")
PARTICIPANT = "ZEROHERO_SAFE_AI"
SYMS = ("NIFTY", "SENSEX", "BANKNIFTY")
SL_FRAC, T1_FRAC = 0.25, 0.20
MAX_TRADES_DAY, MAX_PER_SYMBOL, DAY_LOSS_RS = 3, 2, 6000.0
MAX_SPREAD, MIN_PREMIUM, MAX_QUOTE_AGE_S, MAX_SPOT_AGE_S, COOLDOWN_MIN = 0.03, 30.0, 75.0, 20.0, 30
TICK_PERIOD_S = 10.0


def enabled() -> bool: return os.environ.get("SAFE_AI_LIVE", "1").strip().lower() not in ("0", "false", "off", "no")
CONTROL = os.path.join(DIR, "control.json")


def submit_enabled() -> bool:
    """Signals reach the ledger only if SAFE_AI_SUBMIT is not 0 AND data/safe_ai/control.json does not say {"submit": false}. The control file is read at every decision, so it can be flipped without a restart."""
    if os.environ.get("SAFE_AI_SUBMIT", "1").strip().lower() in ("0", "false", "off", "no"): return False
    c = jsonio.read_json_cached(CONTROL, {}) or {}
    return bool(c.get("submit", True)) if isinstance(c, dict) else True


def _tick(x: float) -> float: return round(round(x / 0.05) * 0.05, 2)


class Engine:
    """deps: spot(sym)->(price, epoch)|None  seed(sym, day)->[(t, px)]  chain(sym)->{"day","expiries":{iso:{"strikes":[...]}}}  quote(sym, iso, strike, ot)->{"ltp","bid","ask","t"}|None
    submit(body, practice)->dict (the ledger row)  watch(sym, iso, strike, ot)  outcomes(day)->{"results": [...]}   mode()->"ARENA_LIVE"|"PRACTICE"|"ENDED"  is_open(exchange)->bool"""

    def __init__(self, *, spot, seed, chain, quote, submit, watch, outcomes, mode, is_open, now=time.time, params: SG.Params | None = None, state_path: str | None = None, log_path: str | None = None):
        self.spot, self.seed, self.chain, self.quote, self.submit, self.watch, self.outcomes, self.mode, self.is_open, self.now = spot, seed, chain, quote, submit, watch, outcomes, mode, is_open, now
        self.p = params or SG.Params(); self.state_path = state_path or os.path.join(DIR, "state.json"); self.log_path = log_path or os.path.join(DIR, "decisions.jsonl")
        self.ticks: dict = defaultdict(list); self._seeded: dict = {}; self._said: dict = {}; self._last_outcome_check = 0.0; self._out_cache: dict = {}
        self.state = jsonio.read_json(self.state_path, {}) or {}

    # ---------------------------------------------------------------- bookkeeping
    def _day(self) -> str: return datetime.fromtimestamp(self.now(), IST).strftime("%Y-%m-%d")

    def _st(self) -> dict:
        d = self._day()
        if self.state.get("day") != d: self.state = {"day": d, "n": 0, "per": {}, "busy_until": 0.0, "stopped": {}, "day_net": 0.0, "locked": False}
        return self.state

    def _save(self) -> None:
        try: jsonio.write_json(self.state_path, self.state)
        except Exception as e: _log.warning("safe_ai state not saved: %r", e)

    def _say(self, kind: str, sym: str | None = None, *, always: bool = False, **kw) -> None:
        key = (kind, sym); now = self.now()
        if not always and now - self._said.get(key, 0) < 300: return
        self._said[key] = now
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "a") as f: f.write(json.dumps({"ts": now, "ist": datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "sym": sym, **kw}, default=str) + "\n")
        except Exception: pass

    def _refresh_outcomes(self) -> None:
        """Which of today's Safe AI signals have closed, and how: a loss locks that index for the day, the day's net locks everything at DAY_LOSS_RS; an open one keeps the others waiting."""
        now = self.now()
        if now - self._last_outcome_check < 20: return
        self._last_outcome_check = now; st = self._st()
        try: res = self.outcomes(self._day())
        except Exception as e: self._say("outcomes_error", None, error=repr(e)); return
        net = 0.0; pid = st.get("pending_fill")
        for r in res.get("results", []):
            s = str(r.get("status", ""))
            if s == "SETTLED":
                net += r["net_rs"]
                if r["net_rs"] < 0: st["stopped"][r["underlying"]] = True
            if pid and r.get("id") == pid:                                         # the position in flight: released when it is final, the cool-down runs from its exit
                if s == "SETTLED": st["busy_until"] = r["fill_t"] + (r.get("hold_sec") or 0) + COOLDOWN_MIN * 60; st["pending_fill"] = None
                elif s in ("CANCELLED", "MISSED", "DATA_GAP", "INVALID_LEVELS", "NOT_TRIGGERED"): st["busy_until"] = now + COOLDOWN_MIN * 60; st["pending_fill"] = None
        st["day_net"] = round(net, 1)
        if net <= -DAY_LOSS_RS and not st["locked"]: st["locked"] = True; self._say("day_locked", None, always=True, net=net)
        self._save()

    # ---------------------------------------------------------------- one pass
    def tick(self) -> list:
        """One decision pass over the indices. -> the ledger rows submitted (usually none)."""
        out = []; now = self.now()
        if not enabled(): return out
        md = self.mode()
        if md == "ENDED": return out
        st = self._st()
        for sym in SYMS:
            try:
                r = self._one(sym, now, md, st)
                if r: out.append(r)
            except Exception as e:
                self._say("error", sym, error=f"{type(e).__name__}: {e}")
        return out

    def _one(self, sym: str, now: float, md: str, st: dict):
        if not self.is_open(EXCH[sym]): return None
        day = self._day()
        if self._seeded.get(sym) != day:
            self.ticks[sym] = [(t, px) for t, px in (self.seed(sym, day) or []) if t <= now]; self._seeded[sym] = day
        sp = self.spot(sym)
        if sp and now - sp[1] <= MAX_SPOT_AGE_S and (not self.ticks[sym] or sp[1] > self.ticks[sym][-1][0]): self.ticks[sym].append((sp[1], float(sp[0])))
        ticks = self.ticks[sym]
        if not ticks or now - ticks[-1][0] > MAX_SPOT_AGE_S: return self._skip(sym, "stale_index_price")
        cap = self.chain(sym) or {}; exps = sorted(e for e in (cap.get("expiries") or {}) if e >= day)
        if not exps: return self._skip(sym, "no_option_expiry")
        exp = exps[0]; expiry_day = exp == day
        if not SG.in_window(now, self.p, expiry_day): return None
        self._refresh_outcomes()
        if st["locked"]: return self._skip(sym, "day_loss_lock")
        if st["stopped"].get(sym): return self._skip(sym, "stopped_after_a_loss_today")
        if st["n"] >= MAX_TRADES_DAY: return self._skip(sym, "daily_trade_cap")
        if st["per"].get(sym, 0) >= MAX_PER_SYMBOL: return self._skip(sym, "per_index_cap")
        if now < st.get("busy_until", 0): return None
        sig = SG.detect(ticks, now, self.p)
        if not sig: return None
        strikes = (cap["expiries"][exp].get("strikes") or [])
        if not strikes: return self._skip(sym, "no_strikes")
        spot = ticks[-1][1]; k = min(strikes, key=lambda s: abs(s - spot)); q = self.quote(sym, exp, float(k), sig["side"])
        if not q or not q.get("ask") or not q.get("bid"): return self._skip(sym, "no_bid_ask", strike=k)
        if now - q["t"] > MAX_QUOTE_AGE_S: return self._skip(sym, "option_quote_too_old", age=round(now - q["t"]))
        ask, bid = float(q["ask"]), float(q["bid"])
        if ask < MIN_PREMIUM: return self._skip(sym, "premium_too_low", ask=ask)
        if (ask - bid) / ask > MAX_SPREAD: return self._skip(sym, "spread_too_wide", spread_pct=round((ask - bid) / ask * 100, 2))
        entry = _tick(ask); sl = _tick(entry * (1 - SL_FRAC)); t1 = _tick(entry * (1 + T1_FRAC))
        note = f"Safe AI U-turn: {sig['reason']}. ATM {k:g} {sig['side']} exp {exp}{' (expiry day)' if expiry_day else ''}; stop 25% of premium, T1 +20%, 1 lot. Paper, no edge claimed."[:300]
        body = {"participant": PARTICIPANT, "underlying": sym, "expiry": exp, "strike": float(k), "option_type": sig["side"], "entry_mode": "NEAR", "entry": entry, "sl": sl, "targets": [t1], "lots": 1, "note": note}
        if not submit_enabled():
            self._say("would_submit", sym, always=True, body=body, features=sig["features"]); st["busy_until"] = now + COOLDOWN_MIN * 60; self._save(); return None
        rec = self.submit(body, md == "PRACTICE")
        st["n"] += 1; st["per"][sym] = st["per"].get(sym, 0) + 1; st["busy_until"] = 9e12; st["pending_fill"] = rec.get("id"); self._save()          # busy until this position is final
        try: self.watch(sym, exp, float(k), sig["side"])
        except Exception: pass
        self._say("submitted", sym, always=True, id=rec.get("id"), body=body, features=sig["features"], quote={"bid": bid, "ask": ask, "age_s": round(now - q["t"], 1)})
        return rec

    def _skip(self, sym: str, why: str, **kw):
        self._say("skip:" + why, sym, **kw); return None


# ---------------------------------------------------------------- real dependencies
def _real_engine() -> Engine:
    from .. import market_calendar as mc
    from ..arena import api as A, contracts as CT, run as R, submit as SU
    from ..histcap import priority

    def spot(sym):
        try:
            from ..connectors.angelone import _market_sdk
            from ..mathematical_confluence import context as ctx
            v = ctx._feed_spot(sym, _market_sdk(require_auth=True))
            return (float(v), time.time()) if v else None
        except Exception: return None

    def seed(sym, day):
        import sqlite3
        d0 = datetime.fromisoformat(day).replace(tzinfo=IST).replace(hour=9, minute=15).timestamp(); out = []
        try:
            with sqlite3.connect(f"file:{CT.DB}?mode=ro", uri=True, timeout=30) as c:
                for ts, p in c.execute("SELECT received_ts, ltp FROM quote_snapshots WHERE session_date_ist=? AND symbol=? AND kind='INDEX' AND ltp IS NOT NULL ORDER BY received_ts", (day, sym)):
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                    if t >= d0 and (not out or t > out[-1][0]): out.append((t, float(p)))
        except Exception as e: _log.warning("safe_ai seed(%s) failed: %r", sym, e)
        return out

    cache: dict = {}

    def outcomes(day):
        sigs = [s for s in R.human_signals(start=day, end=day, include_practice=True) if s["participant"] == PARTICIPANT]
        return R.run(sigs, market_db=CT.DB, cache=cache).get(PARTICIPANT, {"results": []}) if sigs else {"results": []}

    def submit(body, practice): return SU.submit(body, practice=practice)

    return Engine(spot=spot, seed=seed, chain=lambda s: CT.captured_fresh(s), quote=lambda s, e, k, o: CT.latest_quote(s, e, k, o), submit=submit, watch=lambda s, e, k, o: priority.request_watch(s, e, k, o),
                  outcomes=outcomes, mode=lambda: A.mode(A.cfg()), is_open=lambda ex: bool(mc.is_trading(ex)))


class Runner:
    def __init__(self, period: float = TICK_PERIOD_S): self.period, self._t, self._stop, self._eng = period, None, threading.Event(), None

    def _loop(self):
        while not self._stop.is_set():
            try:
                if self._eng is None: self._eng = _real_engine()
                self._eng.tick()
            except Exception as e: _log.warning("safe_ai tick failed: %r", e)
            self._stop.wait(self.period)

    def start(self):
        if enabled() and self._t is None:
            self._t = threading.Thread(target=self._loop, name="safe-ai", daemon=True); self._t.start()
