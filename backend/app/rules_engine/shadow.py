"""SHADOW observer: records what the unified policy would have decided next to the decision the runner actually took. NEVER alters control flow, NEVER raises (the runner
wraps the call too). Disabled with RULES_ENGINE_SHADOW=0. Own sqlite file (data/rules_engine_audit.db). Throttled: one row per (symbol, side, outcome) change or per 60 s."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from . import engine as E

IST = timezone(timedelta(hours=5, minutes=30))
CAPITAL = float(os.environ.get("RULES_ENGINE_CAPITAL", "200000"))
THROTTLE_SEC = 60.0
_lock = threading.Lock(); _eng = None; _failed = False; _last: dict = {}


def enabled() -> bool:
    v = os.environ.get("RULES_ENGINE_SHADOW", "1").strip().lower()
    if os.environ.get("PYTEST_CURRENT_TEST") and v != "force": return False         # tests never write the real audit file unless they opt in with RULES_ENGINE_SHADOW=force
    return v not in ("0", "false", "off", "no")


def _engine():
    global _eng, _failed
    if _eng is None and not _failed:
        try: _eng = E.Engine.load()
        except Exception: _failed = True
    return _eng


def _conn(path=None):
    p = path or E.AUDIT_DB; os.makedirs(os.path.dirname(p), exist_ok=True)
    c = sqlite3.connect(p, timeout=5)
    c.execute("CREATE TABLE IF NOT EXISTS shadow_decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, symbol TEXT, side TEXT, existing_allow INTEGER, existing_reason TEXT, "
              "engine_allow INTEGER, engine_blocked TEXT, not_evaluable TEXT, policy_version TEXT, ctx TEXT)")
    return c


def build_ctx(*, symbol, sig, opens, open_keys, feed_connected, feed_age, last_price, exchange, safeguards, now=None) -> dict:
    now = now or datetime.now(IST)
    side = str(sig.get("decision", "")).split("_")[-1]
    try: import app.market_calendar as mc; hol = bool(mc.is_holiday(exchange=exchange))
    except Exception: hol = now.weekday() >= 5
    try: from ..execution import killswitch; ks = bool(killswitch.is_active())
    except Exception: ks = None
    ev = sig.get("ev_r") if sig.get("ev_r") is not None else (sig.get("ev") or {}).get("ev_r") if isinstance(sig.get("ev"), dict) else None
    daily = None
    try: daily = float(safeguards._daily_realised()) / CAPITAL * 100.0
    except Exception: pass
    trades = None
    try: trades = int(safeguards._trades_today())
    except Exception: pass
    return {"exchange": exchange, "minute_of_day": now.hour * 60 + now.minute, "is_holiday": hol or now.weekday() >= 5, "feed_connected": bool(feed_connected), "feed_age_sec": feed_age,
            "trades_today": trades, "consecutive_losses": getattr(safeguards, "consecutive_losses", None), "daily_pnl_pct": daily, "open_positions": len(opens),
            "duplicate_open_position": (symbol.upper(), side) in open_keys, "minutes_since_last_loss": None, "option_spread_pct": sig.get("option_spread_pct"),
            "premium": sig.get("entry"), "spot": last_price, "reward_risk": sig.get("rr"), "ev_r": ev, "kill_switch_active": ks, "live_orders_requested": False,
            "evidence_status": "UNVALIDATED"}


def observe(ctx: dict, *, symbol: str, side: str, existing_allow: bool, existing_reason: str | None, path: str | None = None, now: float | None = None) -> dict | None:
    if not enabled(): return None
    eng = _engine()
    if eng is None: return None
    d = eng.evaluate({k: v for k, v in ctx.items()}, on_missing="skip")
    key = (symbol, side, bool(existing_allow), d.allow, tuple(sorted(b["rule"] for b in d.blocked_by)))
    t = now or time.time()
    with _lock:
        if t - _last.get(key, 0.0) < THROTTLE_SEC: return d.to_dict()
        _last[key] = t
        c = _conn(path)
        try:
            c.execute("INSERT INTO shadow_decisions (ts,symbol,side,existing_allow,existing_reason,engine_allow,engine_blocked,not_evaluable,policy_version,ctx) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (t, symbol, side, int(bool(existing_allow)), existing_reason, int(d.allow), json.dumps(d.blocked_by), json.dumps(d.not_evaluable), d.policy_version, json.dumps(ctx, default=str)))
            c.commit()
        finally:
            c.close()
    return d.to_dict()


def observe_entry(runner, sym, sig, opens, open_keys, feed_st, feed_age, agg, smeta, allow, why):
    """Called by autoscalp/runner.py right after safeguards.check_entry. Swallows every exception."""
    try:
        if not enabled(): return
        ctx = build_ctx(symbol=sym, sig=sig, opens=opens, open_keys=open_keys, feed_connected=feed_st.get("connected", True), feed_age=feed_age, last_price=getattr(agg, "last_price", None),
                        exchange=smeta.get("exchange") if isinstance(smeta, dict) else None, safeguards=runner.safeguards)
        observe(ctx, symbol=sym.upper(), side=str(sig.get("decision", "")).split("_")[-1], existing_allow=allow, existing_reason=why)
    except Exception:
        return


def summary(path: str | None = None, since_ts: float = 0.0) -> dict:
    p = path or E.AUDIT_DB
    if not os.path.exists(p): return {"rows": 0, "note": "no shadow decisions recorded yet"}
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    try:
        rows = c.execute("SELECT existing_allow,engine_allow,engine_blocked,symbol,ts FROM shadow_decisions WHERE ts>=?", (since_ts,)).fetchall()
    except sqlite3.OperationalError:
        return {"rows": 0, "note": "no shadow decisions recorded yet"}
    finally:
        c.close()
    agree = sum(1 for r in rows if r[0] == r[1]); by = {}
    for r in rows:
        for b in json.loads(r[2] or "[]"): by[b["rule"]] = by.get(b["rule"], 0) + 1
    return {"rows": len(rows), "agree": agree, "disagree": len(rows) - agree, "existing_allow_engine_block": sum(1 for r in rows if r[0] == 1 and r[1] == 0),
            "existing_block_engine_allow": sum(1 for r in rows if r[0] == 0 and r[1] == 1), "engine_blocks_by_rule": dict(sorted(by.items(), key=lambda x: -x[1])),
            "first_ts": min((r[4] for r in rows), default=None), "last_ts": max((r[4] for r in rows), default=None)}
