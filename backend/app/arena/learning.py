"""Signals that must NOT be used to teach or calibrate any AI / research model (the owner's decision, with the reason on record). Scoring on the leaderboard is a separate matter and unchanged.
File: data/arena/learning_exclusions.json = {"<PARTICIPANT>": {"days": ["YYYY-MM-DD", ...], "note": "..."}}. Days are IST session days of the signal's post time."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "arena", "learning_exclusions.json")


def _load(path: str | None = None) -> dict:
    try:
        with open(path or FILE, encoding="utf-8") as f: d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def is_excluded(participant: str, t: float, path: str | None = None) -> bool:
    ex = _load(path).get(participant) or {}
    day = datetime.fromtimestamp(float(t), IST).strftime("%Y-%m-%d")
    return day in (ex.get("days") or [])


def for_learning(signals: list[dict], path: str | None = None) -> list[dict]:
    """The signals a learning / profiling job may use (excluded participant-days removed)."""
    return [s for s in signals if not is_excluded(s.get("participant", ""), s.get("t", 0), path)]
