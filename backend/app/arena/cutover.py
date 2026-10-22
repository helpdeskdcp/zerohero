"""Rehearsal -> official contest cut-over. On the official start date the scoring window start moves to that date (data/arena/contest.json, read at every request: no restart). Pure core, so it can be tested."""
from __future__ import annotations

import json
import os


def apply(path: str, today: str) -> dict:
    """-> {"done": bool, "reason": str, "old_start": ..., "new_start": ...}. Idempotent: does nothing before the official start date or when the window already starts on it."""
    d = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    off = d.get("official_start")
    if not off: return {"done": False, "reason": "no official_start configured"}
    if today < off: return {"done": False, "reason": f"before the official start {off}"}
    if d.get("start") == off: return {"done": False, "reason": "already cut over", "old_start": d.get("start"), "new_start": off}
    old = d.get("start"); d["start"] = off; d["rehearsal"] = {"from": old, "to_inclusive": (d.get("rehearsal") or {}).get("to_inclusive")}
    tmp = path + ".tmp"; json.dump(d, open(tmp, "w"), indent=1); os.replace(tmp, path)
    return {"done": True, "reason": "cut over", "old_start": old, "new_start": off}
