"""Winners Scalper (the owner's Pine script, signal path ported to Python and verified bar-for-bar against the script itself in the Smart Chart's Pine interpreter) running LIVE on MCX NATURALGAS.
Every closed 15-minute bar is run through the same signal code; a new BUY/SELL is RECORDED in the `signals` table (status NEW) for the paper-contest signal consumer (app/<contest package>/pine_submitter.py), which turns it into a
long ATM option paper signal for the participant ZEROHERO_PINE_AI. This module itself has no contest, broker or order import (an information-barrier test enforces it): it only creates signals.
PAPER / OBSERVATION ONLY. NOT back-tested on NG (only ~4 months of 1-minute NG exist); the paper-contest result is the test.
Kill switches: WINNERS_PINE_LIVE=0 (runner off), WINNERS_PINE_SUBMIT=0 (consumer records only), WINNERS_PINE_TG=0 (no Telegram).

LIVE PRICE SOURCE (audit 2026-10-26): the capture's 1-minute candles arrive 5-25 minutes late and ~20 % of them are stored before their minute closed (partial OHLC), so they serve as HISTORY only.
New 15-minute bars are built here from the shared Angel WS feed (feed.get_ltp of the NG front-month future token, sampled every ~2 s) and finalised the moment their bucket ends, so a signal is recorded within seconds of the bar close.
A bar that did not see the first ~2 minutes of its bucket (restart / feed gap) is stored as PARTIAL and never used for a signal; the capture candle stands in for it in the history."""
from __future__ import annotations

import json
import logging
import os
import re
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
IST = timezone(timedelta(hours=5, minutes=30))
BAR_MIN = 15
BUCKET = BAR_MIN * 60                       # 900 s; the IST offset (19,800 s) is a multiple of 900, so epoch // 900 is already quarter-hour aligned in IST
MAX_SIGNAL_AGE_S = 300                      # a signal found later than this after its bar closed is recorded as STALE and never submitted
MAX_QUOTE_AGE_S = 180
MIN_TICKS = 20
LATE_START_S = 120                          # a bucket whose first tick came later than this is PARTIAL
SCHEMA = """CREATE TABLE IF NOT EXISTS state (symbol TEXT PRIMARY KEY, last_ts TEXT);
CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, bar_ts TEXT, side TEXT, strong INTEGER, close REAL, atr REAL, stop REAL, tp1 REAL, tp2 REAL, tp3 REAL,
  status TEXT, detail TEXT, ledger_id TEXT, logged_ts REAL, UNIQUE(symbol, bar_ts));
CREATE TABLE IF NOT EXISTS tick_bars (symbol TEXT, bucket INTEGER, o REAL, h REAL, l REAL, c REAL, n INTEGER, first_ts REAL, last_ts REAL, token TEXT, expiry TEXT, partial INTEGER, PRIMARY KEY (symbol, bucket));
CREATE TABLE IF NOT EXISTS heartbeat (symbol TEXT PRIMARY KEY, ts REAL, ltp REAL, token TEXT, expiry TEXT, ticks_today INTEGER, gaps_today INTEGER, bars_today INTEGER, note TEXT);"""
LEDGER_COLS = ("ledger_id TEXT", "fut_expiry TEXT", "opt_expiry TEXT", "strike REAL", "opt_type TEXT", "entry REAL", "opt_sl REAL", "opt_targets TEXT", "quote_ts TEXT", "quote_age_s REAL", "risk_per_unit REAL",
               "valid_until REAL", "bar_source TEXT")


def enabled() -> bool:
    if os.environ.get("WINNERS_PINE_LIVE", "1") in ("0", "false", "no"): return False
    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get("WINNERS_PINE_DB"): return False
    return True


def _conn(path=None):
    c = sqlite3.connect(path or os.environ.get("WINNERS_PINE_DB", DB), timeout=5); c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=5000"); c.executescript(SCHEMA)
    have = {r[1] for r in c.execute("PRAGMA table_info(signals)")}
    for col in LEDGER_COLS:
        if col.split()[0] not in have: c.execute(f"ALTER TABLE signals ADD COLUMN {col}")
    return c


def _norm_expiry(s: str) -> str:
    """'27OCT2026' | '27-Oct-2026' | '2026-10-27' -> ISO date (unchanged when it cannot be read)."""
    t = str(s).strip().upper()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t): return t
    m = re.fullmatch(r"(\d{1,2})[- ]?([A-Z]{3})[- ]?(\d{4})", t)
    try: return datetime.strptime(f"{m.group(1)}{m.group(2)}{m.group(3)}", "%d%b%Y").date().isoformat() if m else str(s)
    except ValueError: return str(s)


# ---------------------------------------------------------------- history from the capture (final, but late and sometimes partial)
def load_bars(sym: str, now_epoch: float, days: int = 30, path: str | None = None) -> pd.DataFrame:
    """Complete 15-minute bars from 1-minute FUTURE candles. A contract roll is spliced additively onto the front month so no fake gap reaches the ATR/stops. attrs['last_expiry'] = newest contract seen."""
    since = (datetime.fromtimestamp(now_epoch, IST) - timedelta(days=days)).strftime("%Y-%m-%d")
    c = sqlite3.connect(f"file:{path or HISTORY}?mode=ro", uri=True, timeout=10)
    try:
        rows = c.execute("SELECT bar_start, expiry, o, h, l, c, v FROM market_candles WHERE symbol=? AND kind='FUTURE' AND tf='1m' AND session_date_ist>=? ORDER BY bar_start", (sym, since)).fetchall()
    finally: c.close()
    cols = ["timestamp", "expiry", "open", "high", "low", "close", "volume"]; empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"]); empty.attrs["last_expiry"] = ""
    if not rows: return empty
    d = pd.DataFrame(rows, columns=cols); d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True).dt.tz_convert(IST); d["expiry"] = d["expiry"].map(_norm_expiry)
    d["exp_dt"] = pd.to_datetime(d["expiry"], errors="coerce"); d = d.dropna(subset=["exp_dt"]).drop_duplicates(["timestamp", "expiry"]).sort_values(["timestamp", "exp_dt"])
    raw = d.set_index(["timestamp", "expiry"])["close"].to_dict()
    d = d.drop_duplicates("timestamp", keep="first").reset_index(drop=True)                                                         # front month = nearest expiry that has a bar at that minute
    last_exp = d["expiry"].iloc[-1] if len(d) else ""
    chg = [i for i in range(1, len(d)) if d["expiry"].iat[i] != d["expiry"].iat[i - 1]]
    for i in reversed(chg):                                                                                                         # splice each roll, latest first: everything before it shifts by (new - old)
        old_c, new_c = float(d["close"].iat[i - 1]), float(raw.get((d["timestamp"].iat[i - 1], d["expiry"].iat[i]), d["close"].iat[i]))
        off = old_c - new_c
        for k in ("open", "high", "low", "close"): d.loc[:i - 1, k] = d.loc[:i - 1, k] - off
    d = d.set_index("timestamp")
    g = d.resample(f"{BAR_MIN}min").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}); n = d["close"].resample(f"{BAR_MIN}min").count()
    g = g[n >= 3].dropna(subset=["close"]).reset_index(); end = g["timestamp"] + pd.Timedelta(minutes=BAR_MIN)
    out = g[end.map(pd.Timestamp.timestamp) <= now_epoch - 5].reset_index(drop=True); out["src"] = "CAPTURE"; out.attrs["last_expiry"] = str(last_exp); return out


# ---------------------------------------------------------------- live bars from WS ticks
class TickBuilder:
    """Builds 15-minute bars from sampled LTPs. add() feeds a price; due() finalises any bucket that has ended (also without a new tick). Finalised bars are persisted."""

    def __init__(self, c): self.c, self.cur = c, {}

    def add(self, sym: str, now: float, ltp: float, token: str, expiry: str):
        b = self.cur.get(sym); bucket = int(now // BUCKET)
        if b is not None and bucket != b["bucket"]: self._finish(sym)
        b = self.cur.get(sym)
        if b is None: self.cur[sym] = dict(bucket=bucket, o=ltp, h=ltp, l=ltp, c=ltp, n=1, first=now, last=now, token=token, expiry=expiry); return
        b["h"], b["l"], b["c"], b["n"], b["last"] = max(b["h"], ltp), min(b["l"], ltp), ltp, b["n"] + 1, now

    def due(self, now: float) -> list[dict]:
        out = []
        for sym in list(self.cur):
            if now >= (self.cur[sym]["bucket"] + 1) * BUCKET: out.append(self._finish(sym))
        return [x for x in out if x]

    def _finish(self, sym: str) -> dict | None:
        b = self.cur.pop(sym, None)
        if not b: return None
        partial = int(b["n"] < MIN_TICKS or b["first"] - b["bucket"] * BUCKET > LATE_START_S)
        self.c.execute("INSERT OR REPLACE INTO tick_bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (sym, b["bucket"], b["o"], b["h"], b["l"], b["c"], b["n"], b["first"], b["last"], b["token"], b["expiry"], partial)); self.c.commit()
        return dict(symbol=sym, partial=partial, **b)


def merged_bars(sym: str, now: float, c, history_path: str | None = None) -> pd.DataFrame:
    """Capture history + good (non-partial) tick bars. Tick bars win over the capture candle of the same bucket. A change of contract between tick bars (or between the capture's newest contract and the
    first tick bar) is spliced additively so the rolled price gap never reaches the indicators."""
    cap = load_bars(sym, now, path=history_path); last_exp = cap.attrs.get("last_expiry", "")
    rows = c.execute("SELECT bucket, o, h, l, c, expiry FROM tick_bars WHERE symbol=? AND partial=0 AND (bucket+1)*? <= ? ORDER BY bucket", (sym, BUCKET, now + 1)).fetchall()
    if not rows: return cap
    tb = pd.DataFrame(rows, columns=["bucket", "open", "high", "low", "close", "expiry"]); tb["timestamp"] = pd.to_datetime(tb["bucket"] * BUCKET, unit="s", utc=True).dt.tz_convert(IST); tb["volume"] = 0.0; tb["src"] = "TICK"
    tb["expiry"] = tb["expiry"].map(lambda e: _norm_expiry(e) if e else "")
    base = cap[~cap["timestamp"].isin(set(tb["timestamp"]))].copy(); base["expiry"] = last_exp
    allb = pd.concat([base[["timestamp", "open", "high", "low", "close", "volume", "src", "expiry"]], tb[["timestamp", "open", "high", "low", "close", "volume", "src", "expiry"]]]).sort_values("timestamp").reset_index(drop=True)
    ex = allb["expiry"].tolist()
    for i in range(len(allb) - 1, 0, -1):
        if ex[i] and ex[i - 1] and ex[i] != ex[i - 1]:
            off = float(allb["close"].iat[i - 1]) - float(allb["open"].iat[i])
            for k in ("open", "high", "low", "close"): allb.loc[:i - 1, k] = allb.loc[:i - 1, k] - off
    allb = allb.drop(columns="expiry"); return allb


# ---------------------------------------------------------------- signal processing
def process(sym: str, bars: pd.DataFrame, c, now: float | None = None) -> list[dict]:
    """Advance over every closed bar newer than the stored one (first run: just remember the last bar). Each new BUY/SELL is stored ONCE (UNIQUE per symbol and bar) with status NEW for the consumer, or SKIPPED with the exact
    reason when it was found too late / from delayed capture candles. Nothing is submitted here."""
    out: list[dict] = []
    if len(bars) < 120: return out                                                                                                  # warm-up: ATR/ADX/SMA need history
    s = WS.signals(bars); r = c.execute("SELECT last_ts FROM state WHERE symbol=?", (sym,)).fetchone(); last = pd.Timestamp(r[0]) if r else None
    if last is None:
        c.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (sym, str(bars["timestamp"].iloc[-1]))); c.commit(); return out
    for i in range(len(s)):
        ts = s["timestamp"].iat[i]
        if ts <= last or not (s["buy"].iat[i] or s["sell"].iat[i]): continue
        side = "BUY" if s["buy"].iat[i] else "SELL"; stop = float(s["long_stop"].iat[i] if side == "BUY" else s["short_stop"].iat[i]); strong = bool(s["strong"].iat[i]); fclose = float(s["close"].iat[i])
        tps = [None if np.isnan(s[k].iat[i]) else float(s[k].iat[i]) for k in ("tp1", "tp2", "tp3")]; src = str(s["src"].iat[i]) if "src" in s.columns else None
        age = (now or time.time()) - (ts + pd.Timedelta(minutes=BAR_MIN)).timestamp()
        if age > MAX_SIGNAL_AGE_S: status, detail = "SKIPPED", f"STALE_SIGNAL: found {age / 60:.0f} min after the bar closed (max {MAX_SIGNAL_AGE_S // 60} min)"
        elif src == "CAPTURE" and age > 90: status, detail = "SKIPPED", f"CAPTURE_BAR: built from delayed capture candles ({age:.0f}s after close), not from live ticks"
        else: status, detail = "NEW", "recorded; waiting for the consumer"
        try:
            c.execute("INSERT INTO signals (symbol, bar_ts, side, strong, close, atr, stop, tp1, tp2, tp3, status, detail, logged_ts, bar_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (sym, str(ts), side, int(strong), fclose, float(s["atr"].iat[i]), stop, *tps, status, detail, time.time(), src)); c.commit()
        except sqlite3.IntegrityError: continue                                                                                      # this bar was already handled (restart / duplicate)
        out.append(dict(symbol=sym, bar_ts=str(ts), side=side, status=status, detail=detail))
    c.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (sym, str(bars["timestamp"].iloc[-1]))); c.commit(); return out


def run_once(now_epoch: float | None = None, bars_provider=None, db_path: str | None = None) -> dict:
    now = now_epoch or time.time(); res = {}
    with _conn(db_path) as c:
        for sym in SYMS:
            try: res[sym] = process(sym, bars_provider(sym, now) if bars_provider else merged_bars(sym, now, c), c, now)
            except Exception as e: res[sym] = {"error": repr(e)[:150]}; _log.warning("winners pine %s failed: %r", sym, e)
    return res


class Runner:
    """2-second loop: sample the NG future's LTP from the shared WS feed while MCX trades, finalise 15-minute buckets, and run the signal code the moment a good bar closes."""

    def __init__(self, period: float = 2.0): self.period, self._t, self._stop = period, None, threading.Event()

    def _loop(self):
        c = _conn(); tb = TickBuilder(c); subscribed = {}; ticks = gaps = bars = 0; day = ""; last_hb = 0.0
        while not self._stop.is_set():
            now = time.time()
            try:
                from .. import market_calendar as mc, feed_registry
                today = datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d")
                if today != day: day, ticks, gaps, bars = today, 0, 0, 0
                for sym in SYMS:
                    ltp, token, expiry = None, "", ""
                    done = tb.due(now)                                                                                               # finalise ended buckets BEFORE adding the next tick (add() would swallow the roll silently)
                    if mc.is_trading("MCX"):
                        from ..autoscalp.runner import _underlying_ref
                        ref = _underlying_ref(sym) or {}; token, expiry = str(ref.get("token") or ""), str(ref.get("expiry") or ""); feed = feed_registry.get_feed()
                        if feed is not None and token:
                            if subscribed.get(sym) != token:
                                from ..connectors.angel_ws import EXCHANGE_TYPE
                                feed.subscribe([{"token": token, "exchange_type": EXCHANGE_TYPE.get("MCX", 5)}], owner="winners_pine"); subscribed[sym] = token
                            ltp = feed.get_ltp(token, 10.0)
                        if ltp is not None: tb.add(sym, now, float(ltp), token, expiry); ticks += 1
                        else: gaps += 1
                    for bar in done:
                        bars += 1
                        if not bar["partial"]:
                            r = run_once(now, db_path=None)                                                                          # the bar just closed: evaluate immediately
                            if r.get(sym): _log.info("winners pine %s: %s", sym, r[sym])
                    if now - last_hb > 30:
                        c.execute("INSERT OR REPLACE INTO heartbeat VALUES (?,?,?,?,?,?,?,?,?)", (sym, now, ltp, token, expiry, ticks, gaps, bars, "mcx_open" if mc.is_trading("MCX") else "mcx_closed")); c.commit(); last_hb = now
            except Exception as e: _log.warning("winners pine tick failed: %r", e)
            self._stop.wait(self.period)

    def start(self):
        if enabled() and self._t is None: self._t = threading.Thread(target=self._loop, name="winners-pine-live", daemon=True); self._t.start()


def report(db_path: str | None = None, limit: int = 50) -> dict:
    p = db_path or os.environ.get("WINNERS_PINE_DB", DB)
    if not os.path.exists(p): return {"signals": [], "heartbeat": []}
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    try:
        sig = [dict(zip(("symbol", "bar_ts", "side", "strong", "close", "stop", "status", "detail", "ledger_id", "bar_source", "opt_expiry", "strike", "opt_type", "entry", "opt_sl", "opt_targets", "quote_age_s"), r))
               for r in c.execute("SELECT symbol, bar_ts, side, strong, close, stop, status, detail, ledger_id, bar_source, opt_expiry, strike, opt_type, entry, opt_sl, opt_targets, quote_age_s FROM signals ORDER BY id DESC LIMIT ?", (limit,))]
        hb = [dict(zip(("symbol", "ts", "ltp", "token", "expiry", "ticks_today", "gaps_today", "bars_today", "note"), r)) for r in c.execute("SELECT * FROM heartbeat")]
        return {"signals": sig, "heartbeat": hb}
    finally: c.close()
