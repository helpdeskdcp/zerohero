"""Winners Scalper (the owner's Pine script, signal path ported to Python and verified bar-for-bar against the script itself in the Smart Chart's Pine interpreter) running LIVE on MCX NATURALGAS.
Every closed 15-minute bar (built from the primary Angel capture's 1-minute FUTURE candles, contract rolls spliced additively) is run through the same signal code; a new BUY/SELL becomes a PAPER arena signal for the
participant ZEROHERO_PINE_AI: BUY -> nearest captured ATM CALL, SELL -> ATM PUT, entry at the current ask, stop and 1R/2R/3R targets from ZeroHero's risk module (sl_detector.plan_for), 1 lot, valid 15 minutes.
PAPER / OBSERVATION ONLY: no broker or order import. NOT back-tested on NG (only ~4 months of 1-minute NG exist; the index-level test is in data/research/winners_scalper) - the arena result is the test.
Kill switches: WINNERS_PINE_LIVE=0 (runner off), WINNERS_PINE_SUBMIT=0 (record signals, submit nothing), WINNERS_PINE_TG=0 (no Telegram)."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import strategy as WS

_log = logging.getLogger("winners_scalper.live")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "winners_scalper_live.db")
HISTORY = os.path.join(ROOT, "data", "market_history.db")
SYMS = ("NATURALGAS",)
PARTICIPANT = "ZEROHERO_PINE_AI"
IST = timezone(timedelta(hours=5, minutes=30))
BAR_MIN = 15
SCHEMA = """CREATE TABLE IF NOT EXISTS state (symbol TEXT PRIMARY KEY, last_ts TEXT);
CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, bar_ts TEXT, side TEXT, strong INTEGER, close REAL, atr REAL, stop REAL, tp1 REAL, tp2 REAL, tp3 REAL,
  status TEXT, detail TEXT, arena_id TEXT, logged_ts REAL, UNIQUE(symbol, bar_ts));"""


def enabled() -> bool:
    if os.environ.get("WINNERS_PINE_LIVE", "1") in ("0", "false", "no"): return False
    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get("WINNERS_PINE_DB"): return False
    return True


def _conn(path=None):
    c = sqlite3.connect(path or os.environ.get("WINNERS_PINE_DB", DB), timeout=5); c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=5000"); c.executescript(SCHEMA); return c


def _norm_expiry(s: str) -> str:
    from ..arena import submit as SU
    try: return SU.norm_expiry(str(s))
    except Exception: return str(s)


def load_bars(sym: str, now_epoch: float, days: int = 30, path: str | None = None) -> pd.DataFrame:
    """Complete 15-minute bars from 1-minute FUTURE candles. A contract roll is spliced additively (older bars shifted by last-old-close minus first-new-close) so no fake gap reaches the ATR/stops."""
    since = (datetime.fromtimestamp(now_epoch, IST) - timedelta(days=days)).strftime("%Y-%m-%d")
    c = sqlite3.connect(f"file:{path or HISTORY}?mode=ro", uri=True, timeout=10)
    try:
        rows = c.execute("SELECT bar_start, expiry, o, h, l, c, v FROM market_candles WHERE symbol=? AND kind='FUTURE' AND tf='1m' AND session_date_ist>=? ORDER BY bar_start", (sym, since)).fetchall()
    finally: c.close()
    cols = ["timestamp", "expiry", "open", "high", "low", "close", "volume"]; empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    if not rows: return empty
    d = pd.DataFrame(rows, columns=cols); d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True).dt.tz_convert(IST); d["expiry"] = d["expiry"].map(_norm_expiry)
    d["exp_dt"] = pd.to_datetime(d["expiry"], errors="coerce"); d = d.dropna(subset=["exp_dt"]).drop_duplicates(["timestamp", "expiry"]).sort_values(["timestamp", "exp_dt"])
    raw = d.set_index(["timestamp", "expiry"])["close"].to_dict()
    d = d.drop_duplicates("timestamp", keep="first").reset_index(drop=True)                                                         # front month = the nearest expiry that has a bar at that minute
    chg = [i for i in range(1, len(d)) if d["expiry"].iat[i] != d["expiry"].iat[i - 1]]
    for i in reversed(chg):                                                                                                         # splice each roll, latest first: everything before it shifts by (new - old)
        old_c, new_c = float(d["close"].iat[i - 1]), float(raw.get((d["timestamp"].iat[i - 1], d["expiry"].iat[i]), d["close"].iat[i]))
        off = old_c - new_c
        for k in ("open", "high", "low", "close"): d.loc[:i - 1, k] = d.loc[:i - 1, k] - off
    d = d.set_index("timestamp")
    g = d.resample(f"{BAR_MIN}min").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}); n = d["close"].resample(f"{BAR_MIN}min").count()
    g = g[n >= 3].dropna(subset=["close"]).reset_index(); end = g["timestamp"] + pd.Timedelta(minutes=BAR_MIN)
    return g[end.map(pd.Timestamp.timestamp) <= now_epoch - 5].reset_index(drop=True)


def _contest():
    try: return json.load(open(os.path.join(ROOT, "data", "arena", "contest.json")))
    except Exception: return {}


def submit_to_arena(sym: str, side: str, strong: bool, bar_ts, fut_close: float, stop: float, now: float | None = None, path: str | None = None) -> dict:
    """-> {"status": SUBMITTED|SKIPPED, "detail": ..., "arena_id": ...}. Never raises."""
    try:
        from .. import market_calendar as mc, sl_detector
        from ..arena import contracts as CT, submit as SU
        now = now or time.time()
        if os.environ.get("WINNERS_PINE_SUBMIT", "1") in ("0", "false", "no"): return dict(status="SKIPPED", detail="WINNERS_PINE_SUBMIT=0")
        if not mc.is_trading("MCX"): return dict(status="SKIPPED", detail="MCX not trading")
        ct = _contest(); today = datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d")
        if ct.get("end") and today > ct["end"]: return dict(status="SKIPPED", detail="arena ended")
        practice = bool(ct.get("start") and today < ct["start"])
        ot = "CE" if side == "BUY" else "PE"; cap = CT.captured(sym); exps = sorted(cap["expiries"])
        if not exps: return dict(status="SKIPPED", detail="no captured option expiries")
        future = [e for e in exps if e > today] or [e for e in exps if e >= today] or exps; expiry = future[0]
        ch = CT.chain(sym, expiry, ot, 3); atm = ch.get("atm"); row = next((r for r in ch.get("rows", []) if r["strike"] == atm), None); q = (row or {}).get("quote") or {}
        if not atm or not q.get("ask") or not q.get("ts") or now - _epoch(q["ts"]) > 180: return dict(status="SKIPPED", detail=f"no fresh ATM quote ({expiry} {atm} {ot}): {ch.get('problems')}")
        entry = round(round(float(q["ask"]) / 0.05 + 1e-9) * 0.05, 2); plan = sl_detector.plan_for(sym, expiry, float(atm), ot, entry, now)
        if entry < 1.0 or not (0 < plan["sl"] < entry) or not all(t > entry for t in plan["targets"]): return dict(status="SKIPPED", detail=f"degenerate option plan (entry {entry}, sl {plan['sl']}, targets {plan['targets']}): premium too low to define a stop")
        body = dict(participant=PARTICIPANT, underlying=sym, expiry=expiry, strike=float(atm), option_type=ot, entry_mode="NEAR", entry=entry, sl=plan["sl"], targets=plan["targets"], valid_sec=900, lots=1,
                    note=f"Winners Scalper (Pine) {side}{' STRONG' if strong else ''} on {sym} fut 15m bar {bar_ts}: fut close {fut_close:.2f}, Pine stop {stop:.2f}. Stop/targets: ZeroHero risk module 1R/2R/3R.")
        chk = CT.check(sym, expiry, float(atm), ot, entry, "NEAR", sl=plan["sl"])
        if chk.get("problems"): return dict(status="SKIPPED", detail="unscorable: " + "; ".join(chk["problems"])[:300])
        rec = SU.submit(body, practice=practice); return dict(status="SUBMITTED", detail=f"{expiry} {atm:g} {ot} entry {entry} sl {plan['sl']} targets {plan['targets']}{' (practice)' if practice else ''}", arena_id=rec.get("id"))
    except Exception as e:
        return dict(status="SKIPPED", detail=f"error {e!r}"[:300])


def _epoch(ts) -> float:
    """Quote timestamps come back as ISO strings ('2026-10-23T18:04:59.939004Z') or epoch numbers."""
    if isinstance(ts, (int, float)): return float(ts)
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()


def _tg(text: str):
    if os.environ.get("WINNERS_PINE_TG", "1") in ("0", "false", "no"): return
    try:
        from ..connectors import telegram
        telegram._send(text, os.environ.get("TELEGRAM_CHAT_ID"))
    except Exception as e: _log.debug("telegram failed (cosmetic): %r", e)


def process(sym: str, bars: pd.DataFrame, c, submit=submit_to_arena, send=_tg, now: float | None = None) -> list[dict]:
    """Advance over every closed bar newer than the stored one (first run: just remember the last bar). Idempotent per (symbol, bar)."""
    out: list[dict] = []
    if len(bars) < 120: return out                                                                                                  # warm-up: ATR/ADX/SMA need history
    s = WS.signals(bars); r = c.execute("SELECT last_ts FROM state WHERE symbol=?", (sym,)).fetchone(); last = pd.Timestamp(r[0]) if r else None
    if last is None:
        c.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (sym, str(bars["timestamp"].iloc[-1]))); c.commit(); return out
    for i in range(len(s)):
        ts = s["timestamp"].iat[i]
        if ts <= last or not (s["buy"].iat[i] or s["sell"].iat[i]): continue
        side = "BUY" if s["buy"].iat[i] else "SELL"; stop = float(s["long_stop"].iat[i] if side == "BUY" else s["short_stop"].iat[i]); strong = bool(s["strong"].iat[i])
        age = (now or time.time()) - (ts + pd.Timedelta(minutes=BAR_MIN)).timestamp()
        if age > 300: res = dict(status="SKIPPED", detail=f"stale signal ({age / 60:.0f} min after bar close)")                                # never trade a signal found late (restart / data gap)
        else: res = submit(sym, side, strong, ts, float(s["close"].iat[i]), stop, now)
        try:
            c.execute("INSERT INTO signals (symbol, bar_ts, side, strong, close, atr, stop, tp1, tp2, tp3, status, detail, arena_id, logged_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (sym, str(ts), side, int(strong), float(s["close"].iat[i]), float(s["atr"].iat[i]), stop, *(None if np.isnan(s[k].iat[i]) else float(s[k].iat[i]) for k in ("tp1", "tp2", "tp3")), res["status"], res["detail"], res.get("arena_id"), time.time()))
        except sqlite3.IntegrityError: continue
        out.append(dict(symbol=sym, bar_ts=str(ts), side=side, **res))
        send(f"WINNERS SCALPER (Pine) {sym} 15m {side}{' STRONG' if strong else ''}\nfut close {float(s['close'].iat[i]):.2f}  Pine stop {stop:.2f}\nArena ({PARTICIPANT}): {res['status']} - {res['detail']}\nPAPER - no live order")
    c.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (sym, str(bars["timestamp"].iloc[-1]))); c.commit(); return out


def run_once(now_epoch: float | None = None, bars_provider=None, db_path: str | None = None, submit=submit_to_arena, send=_tg) -> dict:
    now = now_epoch or time.time(); res = {}
    with _conn(db_path) as c:
        for sym in SYMS:
            try: res[sym] = process(sym, (bars_provider or load_bars)(sym, now), c, submit, send, now)
            except Exception as e: res[sym] = {"error": repr(e)[:150]}; _log.warning("winners pine %s failed: %r", sym, e)
    return res


class Runner:
    def __init__(self, period: float = 30.0): self.period, self._t, self._stop = period, None, threading.Event()

    def _loop(self):
        while not self._stop.is_set():
            try:
                from .. import market_calendar as mc
                if mc.is_trading("MCX"): run_once()
            except Exception as e: _log.warning("winners pine tick failed: %r", e)
            self._stop.wait(self.period)

    def start(self):
        if enabled() and self._t is None: self._t = threading.Thread(target=self._loop, name="winners-pine-live", daemon=True); self._t.start()


def report(db_path: str | None = None, limit: int = 50) -> dict:
    p = db_path or os.environ.get("WINNERS_PINE_DB", DB)
    if not os.path.exists(p): return {"signals": []}
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    try: return {"signals": [dict(zip(("symbol", "bar_ts", "side", "strong", "close", "stop", "status", "detail", "arena_id"), r)) for r in c.execute("SELECT symbol, bar_ts, side, strong, close, stop, status, detail, arena_id FROM signals ORDER BY id DESC LIMIT ?", (limit,))]}
    finally: c.close()
