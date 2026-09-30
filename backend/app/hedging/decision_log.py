"""Immutable decision-snapshot log for the hedging engine (MCP 3.1.2 items
12-14): every autonomous OPEN persists a full snapshot of the market/
candidate state that produced it, so a past trade can be explained --
WHY this strike, WHY this hedge, what else was evaluated and rejected --
without needing to have kept the original (now long-stale) option chain
around.

Same db.get_setting/set_setting JSON-blob persistence as position.py and
capital.py (this subpackage's own established convention) rather than a
new SQLite file -- app.structural_break has its own dedicated DB for this
kind of audit trail, but that's built for a much higher event volume than
hedging's handful of trades a day; a second heavyweight persistence
mechanism here would just be DUPLICATE_LOGIC for no benefit at this scale.

Append-only: record() only ever appends (oldest entries dropped once
_MAX_ENTRIES is exceeded) -- a correction is a new entry with a new
decision_id, never an edit of a past one.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .. import db

_KEY = "hedging_decision_log"
_MAX_ENTRIES = 1000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _all() -> list[dict]:
    try:
        raw = db.get_setting(_KEY)
        return json.loads(raw) if raw else []
    except Exception:
        return []


def record(snapshot: dict) -> str:
    """Appends one immutable snapshot, returns its decision_id (generated
    if the caller didn't supply one)."""
    decision_id = snapshot.get("decision_id") or str(uuid.uuid4())
    entry = {**snapshot, "decision_id": decision_id, "logged_at": _now_iso()}
    log = _all()
    log.append(entry)
    if len(log) > _MAX_ENTRIES:
        log = log[-_MAX_ENTRIES:]
    db.set_setting(_KEY, json.dumps(log, default=str))
    return decision_id


def get(decision_id: str) -> dict | None:
    for entry in _all():
        if entry.get("decision_id") == decision_id:
            return entry
    return None


def recent(limit: int = 50) -> list[dict]:
    return _all()[-limit:][::-1]
