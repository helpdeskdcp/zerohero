"""
The one-shot Live Monitor snapshot (positions/scalps enriched with live P&L +
distance-to-target/stop). Split out of app/main.py.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from fastapi import APIRouter

from .. import combos
from .. import db
from .. import runtime
from ..connectors.angel_ws import LTP_MAX_AGE_SEC, is_ltp_fresh

router = APIRouter()


@router.get("/api/monitor")
def api_monitor():
    """One-shot snapshot for the Live Monitor page: runner health, live feed
    marks, open positions/scalps with live P&L + distance-to-target/stop, and
    the most recent signals. Deltas thereafter arrive over the WebSocket."""
    st = runtime.scalp_runner.status()
    feed_marks = (st.get("feed") or {}).get("marks") or {}

    def enrich(rows):
        out = []
        for r in rows:
            m = feed_marks.get(str(r.get("symboltoken") or ""))
            entry = r.get("entry") or 0
            qty = r.get("quantity") or 0
            direction = r.get("direction")
            direction_valid = direction in ("BUY", "SELL")
            sign = 1 if direction == "BUY" else -1
            age = m.get("age_sec") if isinstance(m, dict) else None
            fresh = is_ltp_fresh(age)
            raw_mark = m.get("ltp") if isinstance(m, dict) else None
            try:
                mark_is_valid = math.isfinite(float(raw_mark)) and float(raw_mark) > 0
            except (TypeError, ValueError):
                mark_is_valid = False
            # A cached or malformed quote must not be re-labelled as a live
            # REST mark.  There is no timestamp on persisted P&L, so it is
            # deliberately unavailable for live-monitor calculations.
            mark = float(raw_mark) if fresh and mark_is_valid else None
            mark_src = "ws" if mark is not None else None
            freshness = "FRESH" if mark is not None else ("STALE" if m else "UNAVAILABLE")
            live_pnl = round(sign * (mark - entry) * qty, 2) if (direction_valid and mark is not None and entry) else None
            t1, sl = r.get("target_1"), r.get("stop_loss")
            hit = None
            if direction_valid and mark is not None:
                if t1 and ((sign > 0 and mark >= t1) or (sign < 0 and mark <= t1)):
                    hit = "TARGET"
                elif sl and ((sign > 0 and mark <= sl) or (sign < 0 and mark >= sl)):
                    hit = "STOP"
            if r.get("status") != "OPEN":
                monitor_status = r.get("status") or "UNKNOWN"
            elif not direction_valid:
                monitor_status = "INVALID_DATA"
            elif freshness != "FRESH":
                monitor_status = "STALE_DATA"
            elif hit:
                monitor_status = f"{hit}_HIT"
            else:
                monitor_status = "OPEN"
            if not direction_valid:
                target_distance = stop_distance = None
            elif direction == "BUY":
                target_distance = t1 - mark if (t1 and mark is not None) else None
                stop_distance = mark - sl if (sl and mark is not None) else None
            else:
                target_distance = mark - t1 if (t1 and mark is not None) else None
                stop_distance = sl - mark if (sl and mark is not None) else None
            out.append({
                **r,
                "mark": mark,
                "mark_source": mark_src,
                "mark_age_sec": age,
                "stale_age_sec": age if freshness == "STALE" else None,
                "freshness": freshness,
                "live_pnl": live_pnl,
                "hit": hit,
                "monitor_status": monitor_status,
                "dist_to_target": round(target_distance, 2) if target_distance is not None else None,
                "dist_to_stop": round(stop_distance, 2) if stop_distance is not None else None,
            })
        return out

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "runner": {k: st.get(k) for k in (
            "armed", "running", "is_leader", "runner_owner", "auto_arm", "fast_mode",
            "manage_latency_ms", "broker_sync", "broker_sync_status",
            "last_tick_ts", "last_error", "session_open", "session_note",
            "cooldown_sec_remaining", "open_scalps", "max_concurrent",
            "traded_today", "daily_cap", "poll_sec")},
        "feed": st.get("feed"),
        "ltp_max_age_sec": LTP_MAX_AGE_SEC,
        "positions": enrich(db.list_trades(strategy="MANUAL", limit=100)),
        "scalps": enrich(db.list_trades(strategy="SCALP", limit=100)),
        "combos": combos.snapshot(),
        "reversals": [v for v in (runtime.scalp_runner.reversals or {}).values() if v.get("reversal")],
        "turning_points": [v for v in (runtime.scalp_runner.turning_points or {}).values()
                           if v.get("direction") != "NO_TURN"],
        "execution": {**(st.get("execution") or {}),
                      "kill_switch": runtime._ks_state(),
                      "orders": db.list_broker_orders(limit=25)},
        "recent_signals": db.list_signals(limit=20),
    }
