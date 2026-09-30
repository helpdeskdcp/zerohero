"""Expiry audit table (own SQLite file, append-only): every resolution with source,
rule version and any metadata-vs-rule conflict."""
from __future__ import annotations

import os
import sqlite3

from .expiry import ExpiryResult
from .store import _DEFAULT

_COLS = ("exchange", "underlying", "flag", "expiry_date", "expiry_status", "expiry_source", "reason",
         "rule_version", "rule_evidence", "metadata_expiry", "calculated_expiry", "expiry_rule_conflict",
         "holiday_adjustment", "source_reference", "calculation_timestamp")


class ExpiryAudit:
    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("CHANAKYA_HIST_OPTIONS_DB", _DEFAULT)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with sqlite3.connect(self.path, timeout=30) as c:
            c.execute("CREATE TABLE IF NOT EXISTS expiry_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, bar_date TEXT, "
                      + ", ".join(f"{k} TEXT" for k in _COLS) + ")")

    def log(self, bar_date, r: ExpiryResult) -> None:
        d = r.as_dict()
        with sqlite3.connect(self.path, timeout=30) as c:
            c.execute(f"INSERT INTO expiry_audit (bar_date, {','.join(_COLS)}) VALUES (?, {','.join('?' * len(_COLS))})",
                      [str(bar_date)] + [None if d[k] is None else str(d[k]) for k in _COLS])

    def conflicts(self) -> list[dict]:
        with sqlite3.connect(self.path, timeout=30) as c:
            c.row_factory = sqlite3.Row
            return [dict(x) for x in c.execute("SELECT * FROM expiry_audit WHERE expiry_rule_conflict='True'")]
