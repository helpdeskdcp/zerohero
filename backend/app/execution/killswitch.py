"""
Global emergency kill switch.

State lives in app_settings so it is shared across uvicorn workers and survives
a restart. When active:

  * OrderManager.prearm / submit refuse — no new entries, no automatic re-entry.
  * Existing TradeMonitors keep running (a live position must still be watched).
  * The emergency-exit behaviour is EXPLICIT, never implied:
      policy "MONITOR"  (default) — alert only, place no orders.
      policy "FLATTEN"           — OrderManager MAY submit market exits for
                                   confirmed positions (LIVE + auto_exit only).

Risk Engine also reads this via limits.kill_switch, so a stale in-flight
pipeline is rejected at the sizing stage too.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .. import db

_log = logging.getLogger(__name__)

_KEY = "execution_kill_switch"     # {"active": bool, "reason": str, "policy": str, "ts": str}
_POLICIES = ("MONITOR", "FLATTEN")


def _load() -> dict:
    try:
        d = json.loads(db.get_setting(_KEY) or "{}")
    except Exception as e:
        # A corrupted kill-switch value silently reading as "inactive" is a
        # real safety concern (this gates every new order submission) --
        # always surface it, even though _load() is on a hot path.
        _log.warning("killswitch._load: corrupt app_settings value, defaulting to INACTIVE/MONITOR: %r", e)
        d = {}
    return {"active": bool(d.get("active")),
            "reason": d.get("reason") or "",
            "policy": d.get("policy") if d.get("policy") in _POLICIES else "MONITOR",
            "ts": d.get("ts")}


def state() -> dict:
    return _load()


def is_active() -> bool:
    return _load()["active"]


def policy() -> str:
    return _load()["policy"]


def _save(active: bool, reason: str, pol: str):
    db.set_setting(_KEY, json.dumps({
        "active": bool(active), "reason": reason or "",
        "policy": pol if pol in _POLICIES else "MONITOR",
        "ts": datetime.now(timezone.utc).isoformat()}))


def activate(reason: str = "manual", pol: str | None = None):
    cur = _load()
    _save(True, reason, pol or cur["policy"])
    _event("KILL_SWITCH_ON", reason, pol or cur["policy"])
    return _load()


def deactivate(reason: str = "manual"):
    cur = _load()
    _save(False, reason, cur["policy"])
    _event("KILL_SWITCH_OFF", reason, cur["policy"])
    return _load()


def set_policy(pol: str):
    cur = _load()
    _save(cur["active"], cur["reason"], pol)
    _event("KILL_SWITCH_POLICY", cur["reason"], pol)
    return _load()


def _event(kind, reason, pol):
    try:
        db.insert_order_event(None, None, kind, json.dumps({"reason": reason, "policy": pol}))
    except Exception as e:
        _log.warning("killswitch._event(%s): failed to write audit event: %r", kind, e)
