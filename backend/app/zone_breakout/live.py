"""Zone Breakout PAPER runner for all six indices. Reads the primary Angel One path's 1-minute candle capture (market_candles INDEX, written by the Angel adapter's capture; no second Angel login),
builds complete 5-minute bars, advances each index's persisted state machine one bar at a time (identical code to the back-test: strategy.step, next-open entry, flat at 15:15) and
announces entries / exits as IDADDY cards on the PRIVATE alert chat (the signals channel is evidence-only by the owner's instruction). OBSERVATION ONLY: no order path.
Spec: data/research/zone_breakout/SPEC_2026-10-25.md. Kill switches: ZONE_BREAKOUT_LIVE=0 (runner), ZONE_BREAKOUT_TG=0 (messages)."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time

import numpy as np
import pandas as pd

from . import data as DT, strategy as ZB

_log = logging.getLogger("zone_breakout.live")
ROOT = DT.ROOT
DB = os.path.join(ROOT, "data", "zone_breakout_live.db")
SCHEMA = """CREATE TABLE IF NOT EXISTS state (symbol TEXT PRIMARY KEY, last_ts TEXT, js TEXT);
CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, side TEXT, entry_ts TEXT, entry REAL, sl REAL, tp REAL, exit_ts TEXT, exit REAL, reason TEXT, R REAL, risk_bp REAL, status TEXT, logged_ts REAL, UNIQUE (symbol, entry_ts));
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, ts TEXT, kind TEXT, price REAL, sl REAL, tp REAL, logged_ts REAL);"""
VERDICT_LINE = "Back-test 2015-2026 (6 indices, 5m/15m): NO-GO — gross edge small and shrinking, negative after costs. Paper / analysis only."


def enabled() -> bool:
    if os.environ.get("ZONE_BREAKOUT_LIVE", "1") in ("0", "false", "no"): return False
    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get("ZONE_BREAKOUT_DB"): return False
    return True


def _db() -> str: return os.environ.get("ZONE_BREAKOUT_DB", DB)


def _conn(path=None):
    c = sqlite3.connect(path or _db(), timeout=5); c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=5000"); c.executescript(SCHEMA)
    try: c.execute("ALTER TABLE trades ADD COLUMN features TEXT")                                       # trap-filter features at entry (forward dataset, data/research/trap_filter)
    except sqlite3.OperationalError: pass
    return c


def _entry_features(c, sym: str, di: pd.DataFrame, i: int, ts, side: str, px: float, sl: float) -> str | None:
    """Causal trap features of the trade being opened on bar i (signal bar = i-1), stored as JSON for the forward trap-filter test. Never raises."""
    try:
        from . import trap_features as TF
        sess = pd.Timestamp(ts).tz_convert("Asia/Kolkata").date()
        prior = pd.DataFrame([dict(side=r[0], entry=r[1], entry_ts=pd.Timestamp(r[2]), exit_ts=pd.Timestamp(r[3]), reason=r[4], session=sess, risk_bp=1.0, signal_i=max(i - 1, 0))
                              for r in c.execute("SELECT side, entry, entry_ts, exit_ts, reason FROM trades WHERE symbol=? AND status='CLOSED' AND exit_ts IS NOT NULL", (sym,)).fetchall()
                              if pd.Timestamp(r[2]).tz_convert("Asia/Kolkata").date() == sess])
        cur = pd.DataFrame([dict(side=side, entry=px, entry_ts=pd.Timestamp(ts), exit_ts=pd.NaT, reason="", session=sess, risk_bp=abs(px - sl) / px * 1e4, signal_i=max(i - 1, 0))])
        t = pd.concat([prior, cur], ignore_index=True); f = TF.build(di.iloc[:i + 1].reset_index(drop=True), t, only=len(t) - 1)
        return json.dumps({k: (None if pd.isna(f.iloc[0][k]) else float(f.iloc[0][k])) for k in TF.FEATURES})
    except Exception as e:
        _log.debug("trap features failed (cosmetic): %r", e); return None


def recent_5m(sym: str, now_epoch: float, days: int = 9, db: str | None = None) -> pd.DataFrame:
    """Complete 5-minute session bars from the Angel capture (1-minute market_candles)."""
    since = pd.Timestamp(now_epoch - days * 86400, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%S")
    d = DT._resample_1m(DT._capture_1m(sym, since_utc=since, db=db), 5) if True else None
    if d is None or d.empty: return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    d["timestamp"] = d["timestamp"].dt.tz_convert("Asia/Kolkata"); d = DT._session_only(d.sort_values("timestamp"))
    return d[(d["timestamp"] + pd.Timedelta(minutes=5)).map(pd.Timestamp.timestamp) <= now_epoch].reset_index(drop=True)


def _load_state(c, sym: str, d: pd.DataFrame):
    r = c.execute("SELECT last_ts, js FROM state WHERE symbol=?", (sym,)).fetchone(); st = ZB.State()
    if not r: return None, st
    j = json.loads(r[1]); st.pending_dir = j["pending_dir"]; st.breakout_level = j["breakout_level"]; st.position = j["position"]; st.queued = j["queued"]
    if st.position: st.position["entry_ts"] = pd.Timestamp(st.position["entry_ts"])
    if j["breakout_ts"] is not None:
        idx = d.index[d["timestamp"] == pd.Timestamp(j["breakout_ts"])]
        if len(idx): st.breakout_bar = int(idx[0])
        else: st.pending_dir, st.breakout_level, st.breakout_bar = 0, None, None            # the breakout bar is older than the loaded window: setup expired
    return pd.Timestamp(r[0]), st


def _save_state(c, sym: str, last_ts, st: ZB.State, d: pd.DataFrame):
    pos = None
    if st.position: pos = {**{k: v for k, v in st.position.items() if k != "entry_ts"}, "entry_ts": str(st.position["entry_ts"])}
    js = {"pending_dir": st.pending_dir, "breakout_level": st.breakout_level, "breakout_ts": (str(d["timestamp"].iloc[st.breakout_bar]) if st.breakout_bar is not None and st.breakout_bar < len(d) else None), "position": pos, "queued": st.queued}
    c.execute("INSERT OR REPLACE INTO state VALUES (?,?,?)", (sym, str(last_ts), json.dumps(js, default=float)))


def card(kind: str, sym: str, t: dict, tf: int = 5) -> str:
    BAR = "━" * 22
    if kind == "ENTRY":
        risk_pts = abs(t["entry"] - t["sl"]); rr = abs(t["tp"] - t["entry"]) / risk_pts if risk_pts else 0
        return "\n".join([BAR, "     IDADDY AI SIGNAL", BAR, "", f"Instrument: {sym}  ({tf}m)", f"Direction: {'BUY / long' if t['side'] == 'BUY' else 'SELL / short'}", "", "Setup:", "Zone Breakout (strict consolidation) + engulfing/harami", "",
                          f"Entry:\n{t['entry']:.2f}", f"Stop Loss:\n{t['sl']:.2f}  ({risk_pts:.1f} pts, {risk_pts / t['entry'] * 1e4:.1f} bp)", f"Target:\n{t['tp']:.2f}", f"Risk/Reward: 1:{rr:.1f}", "",
                          "Status:\nPAPER  (no live order)", VERDICT_LINE, BAR])
    icon = {"tp": "✅ TARGET", "sl": "❌ STOP", "eod": "⏱ EOD EXIT"}[t["reason"]]
    return f"{icon} — <b>ZONE BREAKOUT {sym}</b> {t['side']}\nentry {t['entry']:.2f}  exit {t['exit']:.2f}  R {t['R']:+.2f}\nPAPER — monitor / no live order"


def process(sym: str, d: pd.DataFrame, c, cfg: ZB.Config | None = None, send=None, log_first_run: bool = True) -> list[dict]:
    """Advance sym's persisted state over every bar of `d` newer than the stored one (first run: just remember the last bar, no historical paper trades). Returns the announcements made."""
    cfg = cfg or ZB.Config(); out: list[dict] = []
    if len(d) < max(cfg.zone_len + cfg.cons_bars + 3, cfg.ema_len + 2): return out
    di = ZB.indicators(d, cfg); last_ts, st = _load_state(c, sym, di)
    if last_ts is None:
        _save_state(c, sym, di["timestamp"].iloc[-1], st, di); return out
    new_idx = [i for i in range(len(di)) if di["timestamp"].iloc[i] > last_ts]
    if not new_idx: return out
    sd = pd.DatetimeIndex(di["timestamp"]).tz_convert("Asia/Kolkata").date; tf = 5
    for i in new_idx:
        ev: list = []; closed = ZB.step(di, i, cfg, st, "next_open", True, tf, sd, ev)
        for k, ts, px, sl, tp in ev:
            c.execute("INSERT INTO events (symbol, ts, kind, price, sl, tp, logged_ts) VALUES (?,?,?,?,?,?,?)", (sym, str(ts), k, px, sl, tp, time.time()))
            if k.startswith("PAPER_ENTRY"):                                                          # from the event itself: the position may already be closed again on the same bar
                side = k.rsplit("_", 1)[-1]; p = {"side": side, "entry": float(px), "sl": float(sl), "tp": float(tp)}
                c.execute("INSERT OR IGNORE INTO trades (symbol, side, entry_ts, entry, sl, tp, status, risk_bp, logged_ts) VALUES (?,?,?,?,?,?,?,?,?)", (sym, side, str(ts), p["entry"], p["sl"], p["tp"], "OPEN", abs(p["entry"] - p["sl"]) / p["entry"] * 1e4, time.time()))
                fj = _entry_features(c, sym, di, i, ts, side, p["entry"], p["sl"])
                if fj: c.execute("UPDATE trades SET features=? WHERE symbol=? AND entry_ts=?", (fj, sym, str(ts)))
                out.append({"kind": "ENTRY", "symbol": sym, **p}); send and send(card("ENTRY", sym, p, tf))
        for t in closed:
            c.execute("UPDATE trades SET exit_ts=?, exit=?, reason=?, R=?, status='CLOSED' WHERE symbol=? AND entry_ts=?", (str(t["exit_ts"]), t["exit"], t["reason"], t["R"], sym, str(t["entry_ts"])))
            out.append({"kind": "EXIT", "symbol": sym, "reason": t["reason"], "R": t["R"]}); send and send(card("EXIT", sym, t, tf))
    _save_state(c, sym, di["timestamp"].iloc[-1], st, di); return out


def run_once(now_epoch: float | None = None, bars_provider=None, db_path: str | None = None, send=None, symbols=None) -> dict:
    now = now_epoch or time.time(); res = {}
    if send is None and os.environ.get("ZONE_BREAKOUT_TG", "1") not in ("0", "false", "no"):
        def send(text):
            try:
                from ..connectors import telegram
                telegram._send(text, os.environ.get("TELEGRAM_CHAT_ID"))
            except Exception as e: _log.debug("zone breakout telegram failed (cosmetic): %r", e)
    with _conn(db_path) as c:
        for sym in (symbols or DT.INDICES):
            try: res[sym] = process(sym, (bars_provider or recent_5m)(sym, now), c, send=send)
            except Exception as e: res[sym] = {"error": repr(e)[:120]}; _log.warning("zone breakout %s failed: %r", sym, e)
    return res


class Runner:
    def __init__(self, period: float = 30.0): self.period, self._t, self._stop = period, None, threading.Event()

    def _loop(self):
        while not self._stop.is_set():
            try:
                from .. import market_calendar as mc
                if mc.is_trading("NSE"): run_once()
            except Exception as e: _log.warning("zone breakout tick failed: %r", e)
            self._stop.wait(self.period)

    def start(self):
        if enabled() and self._t is None: self._t = threading.Thread(target=self._loop, name="zone-breakout-live", daemon=True); self._t.start()


def report(db_path: str | None = None, limit: int = 50) -> dict:
    p = db_path or _db()
    if not os.path.exists(p): return {"trades": [], "events": [], "summary": {}}
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    try:
        tr = pd.read_sql_query("SELECT symbol, side, entry_ts, entry, sl, tp, exit_ts, exit, reason, R, risk_bp, status FROM trades ORDER BY id DESC LIMIT ?", c, params=(limit,))
        ev = pd.read_sql_query("SELECT symbol, ts, kind, price FROM events ORDER BY id DESC LIMIT ?", c, params=(limit,)); st = pd.read_sql_query("SELECT symbol, last_ts FROM state", c)
    finally:
        c.close()
    cl = tr[tr["status"] == "CLOSED"]
    return {"trades": tr.to_dict("records"), "events": ev.to_dict("records"), "last_bar": dict(zip(st["symbol"], st["last_ts"])), "summary": {"closed": int(len(cl)), "mean_R_gross": (round(float(cl["R"].mean()), 3) if len(cl) else None), "open": int((tr["status"] == "OPEN").sum())}, "verdict": VERDICT_LINE}
