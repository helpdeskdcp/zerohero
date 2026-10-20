"""Shadow recorder. Called from autoscalp/runner.py after the frozen decision: re-runs the SAME decide_from_context with only `filters` relaxed and records BUY_CE/BUY_PE decisions
beside the frozen decision. Never raises; never mutates the runner's data (works on deep copies). Own sqlite file. ACTIVE_ARM_SHADOW=0 disables."""
from __future__ import annotations

import copy
import json
import os
import sqlite3
import threading
import time

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "active_arm.db")
ACTIVE_FILTERS = {"block_regimes": [], "block_signal_types": [], "block_tod": []}
DEDUP_SEC = 600.0
_lock = threading.Lock(); _last: dict = {}


def enabled() -> bool:
    v = os.environ.get("ACTIVE_ARM_SHADOW", "1").strip().lower()
    if os.environ.get("PYTEST_CURRENT_TEST") and v != "force": return False
    return v not in ("0", "false", "off", "no")


def relaxed(strat_cfg: dict) -> dict:
    """The ONLY difference vs the frozen engine: filters overridden. Returns a copy; the input is untouched."""
    c = copy.deepcopy(strat_cfg); c["filters"] = copy.deepcopy(ACTIVE_FILTERS); return c


def _conn(path: str | None = None):
    p = path or DB; os.makedirs(os.path.dirname(p), exist_ok=True)
    c = sqlite3.connect(p, timeout=5)
    c.execute("CREATE TABLE IF NOT EXISTS arm_signals (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, symbol TEXT, decision TEXT, strike REAL, expiry TEXT, entry REAL, stop_loss REAL, "
              "target_1 REAL, target_2 REAL, max_hold_sec REAL, regime TEXT, signal_type TEXT, confidence TEXT, probability REAL, frozen_decision TEXT, frozen_blocked INTEGER, exchange TEXT, payload TEXT)")
    return c


def record(sym: str, sig: dict, frozen: dict, exchange: str | None, *, path: str | None = None, now: float | None = None) -> bool:
    d = str(sig.get("decision", ""))
    if d not in ("BUY_CE", "BUY_PE") or not all(sig.get(k) for k in ("entry", "stop_loss", "target_1")): return False
    t = now or time.time(); key = (sym.upper(), d)
    with _lock:
        if t - _last.get(key, 0.0) < DEDUP_SEC: return False
        _last[key] = t
        fd = str((frozen or {}).get("decision", ""))
        c = _conn(path)
        try:
            c.execute("INSERT INTO arm_signals (ts,symbol,decision,strike,expiry,entry,stop_loss,target_1,target_2,max_hold_sec,regime,signal_type,confidence,probability,frozen_decision,frozen_blocked,exchange,payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (t, sym.upper(), d, sig.get("strike"), str(sig.get("expiry") or ""), sig["entry"], sig["stop_loss"], sig["target_1"], sig.get("target_2"), sig.get("max_hold_sec") or 1500.0, sig.get("regime"),
                       sig.get("signal_type"), sig.get("confidence"), sig.get("probability"), fd, 0 if fd == d else 1, exchange, json.dumps({"reason": str(sig.get("reason", ""))[:400]})))
            c.commit()
            rid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            c.close()
    try:                                                      # evidence post of the arm signal (log-only side effect; never raises)
        from ..evidence_feed import publish as _ev
        _ev.publish_arm_signal({"id": rid, "ts": t, "symbol": sym.upper(), "decision": d, "strike": sig.get("strike"), "expiry": str(sig.get("expiry") or ""), "entry": sig["entry"], "stop_loss": sig["stop_loss"],
                                "target_1": sig["target_1"], "target_2": sig.get("target_2"), "regime": sig.get("regime"), "signal_type": sig.get("signal_type"), "confidence": sig.get("confidence"), "frozen_blocked": 0 if fd == d else 1})
    except Exception:
        pass
    return True


def observe(sym: str, frozen_sig: dict, strat_cfg: dict, bars, chain, *, atm, calib, avg_win, avg_loss, leg_bars_fn, tod, exchange=None, decide_fn=None, path: str | None = None, now: float | None = None) -> bool:
    """Never raises. Returns True when a row was written."""
    try:
        if not enabled(): return False
        if decide_fn is None:
            from ..engines.scalp_strategy import decide_from_context as decide_fn
        sig = decide_fn(copy.deepcopy(bars), copy.deepcopy(chain), atm=atm, calib=copy.deepcopy(calib), avg_win=avg_win, avg_loss=avg_loss, leg_bars_fn=leg_bars_fn, tod_bucket=tod, config=relaxed(strat_cfg))
        return record(sym, sig, frozen_sig, exchange, path=path, now=now)
    except Exception:
        return False


def load(path: str | None = None, since_ts: float = 0.0, until_ts: float = 1e18) -> list[dict]:
    p = path or DB
    if not os.path.exists(p): return []
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5); c.row_factory = sqlite3.Row
    try: return [dict(r) for r in c.execute("SELECT * FROM arm_signals WHERE ts>=? AND ts<=? ORDER BY ts", (since_ts, until_ts))]
    except sqlite3.OperationalError: return []
    finally: c.close()
