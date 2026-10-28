"""Durable capture journal for the Telegram -> Arena ingestion (SQLite, its own file: data/tg/capture.db, mode 600). Nothing here talks to Telegram or to the ledger.

Why: a post used to be parsed and submitted inside the Telegram event callback and remembered only in a JSON `seen` dictionary. A crash between "received" and "processed" lost the post silently, and
there was no record of what the source had that we did not. Now every post is first written here (one cheap insert), then a worker processes it, and a reconciler compares the source history with these rows.

Rows (tg_capture): one per (chat_id, message_id, edit_version). edit_version 0 is the post as first seen; an edit gets its own row (edit_version = the edit time), so history is preserved and an edit can never
overwrite or duplicate the original. The UNIQUE key is the idempotency guarantee: the same post arriving by push, by poll and by backfill is stored once.
  ingestion_status: CAPTURED (waiting for the worker) | PROCESSED | IGNORED (sender not allowed: no text kept) | FAILED (dead letter after MAX_ATTEMPTS, needs a human)
  update_state    : how it arrived: push | poll | catch_up | backfill | edit
  recovery_status : NONE | BACKFILLED (found by the reconciler, not by push/poll) | CATCH_UP
Gaps (tg_gap): every range the reconciler suspected or found missing, the attempts made and the outcome (OPEN | RECOVERED | UNRECOVERABLE | ACKED). An unrecoverable range is never marked recovered.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(HERE, "data", "tg", "capture.db")
MAX_ATTEMPTS = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS tg_capture (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id          TEXT    NOT NULL,
    message_id       INTEGER NOT NULL,
    edit_version     INTEGER NOT NULL DEFAULT 0,
    source_ts        REAL,
    received_at      REAL    NOT NULL,
    update_state     TEXT    NOT NULL,
    content_hash     TEXT,
    text             TEXT,
    reply_to         INTEGER,
    sender_id        TEXT,
    ingestion_status TEXT    NOT NULL DEFAULT 'CAPTURED',
    recovery_status  TEXT    NOT NULL DEFAULT 'NONE',
    outcome          TEXT,
    signal_id        TEXT,
    attempts         INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    processed_at     REAL,
    UNIQUE(chat_id, message_id, edit_version)
);
CREATE INDEX IF NOT EXISTS ix_tgc_status ON tg_capture(ingestion_status, chat_id, message_id);
CREATE INDEX IF NOT EXISTS ix_tgc_chat   ON tg_capture(chat_id, message_id);
CREATE TABLE IF NOT EXISTS tg_gap (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         TEXT,
    from_id         INTEGER,
    to_id           INTEGER,
    kind            TEXT NOT NULL,
    detected_at     REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'OPEN',
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_attempt_at REAL,
    recovered_count INTEGER NOT NULL DEFAULT 0,
    note            TEXT
);
CREATE INDEX IF NOT EXISTS ix_tgg_status ON tg_gap(status);
CREATE TABLE IF NOT EXISTS tg_kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS tg_delete (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      TEXT    NOT NULL,
    message_id   INTEGER NOT NULL,
    detected_at  REAL    NOT NULL,
    source       TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'PENDING',
    outcome      TEXT,
    signal_id    TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    UNIQUE(chat_id, message_id)
);
"""


def content_hash(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class Journal:
    """Thread-safe: one SQLite connection per thread (the event loop, the worker thread and the reconciler may all use it), WAL mode, a 5 s busy timeout."""

    def __init__(self, path: str | None = None):
        self.path = path or DB; self._local = threading.local()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        c = self._c(); c.executescript(SCHEMA)
        try: os.chmod(self.path, 0o600)
        except OSError: pass

    def _c(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=5.0, isolation_level=None); c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA synchronous=NORMAL"); c.execute("PRAGMA busy_timeout=5000")
            self._local.c = c
        return c

    # ------------------------------------------------------------------ captures
    def record(self, *, chat_id, message_id: int, source_ts: float | None, text: str | None, received_at: float | None = None, edit_version: int = 0,
               update_state: str = "push", reply_to: int | None = None, sender_id=None, status: str = "CAPTURED", recovery: str = "NONE") -> tuple[int | None, bool]:
        """Insert one capture. -> (row id, True) when new, (None, False) when this exact (chat, message, edit_version) is already journaled (a duplicate delivery). A row with status IGNORED keeps no text."""
        keep = text if status != "IGNORED" else None
        cur = self._c().execute("INSERT OR IGNORE INTO tg_capture(chat_id,message_id,edit_version,source_ts,received_at,update_state,content_hash,text,reply_to,sender_id,ingestion_status,recovery_status) "
                                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                (str(chat_id), int(message_id), int(edit_version), source_ts, received_at or time.time(), update_state, content_hash(text), keep, reply_to,
                                 None if sender_id is None else str(sender_id), status, recovery))
        return (cur.lastrowid, True) if cur.rowcount == 1 else (None, False)

    def has(self, chat_id, message_id: int, edit_version: int = 0) -> bool:
        return self._c().execute("SELECT 1 FROM tg_capture WHERE chat_id=? AND message_id=? AND edit_version=?", (str(chat_id), int(message_id), int(edit_version))).fetchone() is not None

    def max_message_id(self, chat_id) -> int:
        r = self._c().execute("SELECT MAX(message_id) FROM tg_capture WHERE chat_id=?", (str(chat_id),)).fetchone()
        return int(r[0] or 0)

    def pending(self, limit: int = 50) -> list[dict]:
        """Rows waiting for the worker, in a deterministic per-chat order (message id, then edit version): related posts are processed in the order they were posted."""
        return [dict(r) for r in self._c().execute("SELECT * FROM tg_capture WHERE ingestion_status='CAPTURED' ORDER BY chat_id, message_id, edit_version, id LIMIT ?", (int(limit),)).fetchall()]

    def mark_done(self, row_id: int, outcome: str, signal_id: str | None = None) -> None:
        self._c().execute("UPDATE tg_capture SET ingestion_status='PROCESSED', outcome=?, signal_id=?, processed_at=?, last_error=NULL WHERE id=?", (outcome, signal_id, time.time(), int(row_id)))

    def mark_error(self, row_id: int, err: str, now: float | None = None) -> str:
        """One failed processing attempt. After MAX_ATTEMPTS the row becomes FAILED (dead letter) and stays visible until a human handles it. -> the new status."""
        c = self._c(); c.execute("UPDATE tg_capture SET attempts=attempts+1, last_error=?, processed_at=? WHERE id=?", (str(err)[:300], now or time.time(), int(row_id)))      # processed_at doubles as "last attempt" for the backoff
        n = c.execute("SELECT attempts FROM tg_capture WHERE id=?", (int(row_id),)).fetchone()[0]
        if n >= MAX_ATTEMPTS:
            c.execute("UPDATE tg_capture SET ingestion_status='FAILED' WHERE id=?", (int(row_id),)); return "FAILED"
        return "CAPTURED"

    def requeue_failed(self, row_id: int) -> None:
        self._c().execute("UPDATE tg_capture SET ingestion_status='CAPTURED', attempts=0 WHERE id=? AND ingestion_status='FAILED'", (int(row_id),))

    # ------------------------------------------------------------------ deletions
    def record_delete(self, chat_id, message_id: int, source: str = "event", now: float | None = None) -> bool:
        """A message that disappeared from the channel. -> True when new (the same deletion seen by the event AND by the reconciler is stored once)."""
        cur = self._c().execute("INSERT OR IGNORE INTO tg_delete(chat_id,message_id,detected_at,source) VALUES (?,?,?,?)", (str(chat_id), int(message_id), now or time.time(), source))
        return cur.rowcount == 1

    def pending_deletes(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self._c().execute("SELECT * FROM tg_delete WHERE status='PENDING' ORDER BY id LIMIT ?", (int(limit),)).fetchall()]

    def mark_delete_done(self, row_id: int, outcome: str, signal_id: str | None = None) -> None:
        self._c().execute("UPDATE tg_delete SET status='DONE', outcome=?, signal_id=? WHERE id=?", (outcome, signal_id, int(row_id)))

    def mark_delete_error(self, row_id: int, err: str) -> str:
        c = self._c(); c.execute("UPDATE tg_delete SET attempts=attempts+1, last_error=? WHERE id=?", (str(err)[:300], int(row_id)))
        n = c.execute("SELECT attempts FROM tg_delete WHERE id=?", (int(row_id),)).fetchone()[0]
        if n >= MAX_ATTEMPTS: c.execute("UPDATE tg_delete SET status='FAILED' WHERE id=?", (int(row_id),)); return "FAILED"
        return "PENDING"

    def recent_signal_rows(self, chat_id, since: float, limit: int = 100) -> list[dict]:
        """Original posts (edit_version 0) that became a ledger signal, newer than `since` and not already known deleted: the ones whose disappearance matters."""
        return [dict(r) for r in self._c().execute(
            "SELECT message_id, signal_id FROM tg_capture c WHERE chat_id=? AND edit_version=0 AND signal_id IS NOT NULL AND received_at>=? "
            "AND NOT EXISTS (SELECT 1 FROM tg_delete d WHERE d.chat_id=c.chat_id AND d.message_id=c.message_id) ORDER BY message_id DESC LIMIT ?", (str(chat_id), since, int(limit))).fetchall()]

    # ------------------------------------------------------------------ gaps
    def open_gap(self, *, chat_id, from_id, to_id, kind: str, note: str = "", status: str = "OPEN") -> int:
        return self._c().execute("INSERT INTO tg_gap(chat_id,from_id,to_id,kind,detected_at,status,note) VALUES (?,?,?,?,?,?,?)", (None if chat_id is None else str(chat_id), from_id, to_id, kind, time.time(), status, note[:300])).lastrowid

    def update_gap(self, gap_id: int, *, status: str | None = None, attempt: bool = False, recovered: int = 0, note: str | None = None) -> None:
        c = self._c(); g = c.execute("SELECT * FROM tg_gap WHERE id=?", (int(gap_id),)).fetchone()
        if g is None: return
        c.execute("UPDATE tg_gap SET status=?, attempts=attempts+?, last_attempt_at=?, recovered_count=recovered_count+?, note=? WHERE id=?",
                  (status or g["status"], 1 if attempt else 0, time.time() if attempt else g["last_attempt_at"], int(recovered), (note if note is not None else g["note"]), int(gap_id)))

    def ack_gap(self, gap_id: int, note: str) -> bool:
        """A human accepted that this range is lost. Only an UNRECOVERABLE gap can be acknowledged; it then stops forcing RECOVERY_REQUIRED but stays in the history."""
        c = self._c(); r = c.execute("UPDATE tg_gap SET status='ACKED', note=? WHERE id=? AND status='UNRECOVERABLE'", (("ACK: " + note)[:300], int(gap_id)))
        return r.rowcount == 1

    def gaps(self, status: str | None = None, limit: int = 100) -> list[dict]:
        q = "SELECT * FROM tg_gap" + (" WHERE status=?" if status else "") + " ORDER BY id DESC LIMIT ?"
        return [dict(r) for r in self._c().execute(q, ((status, limit) if status else (limit,))).fetchall()]

    # ------------------------------------------------------------------ key / value
    def kv_get(self, key: str, default=None):
        r = self._c().execute("SELECT value FROM tg_kv WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def kv_set(self, key: str, value) -> None:
        self._c().execute("INSERT INTO tg_kv(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    # ------------------------------------------------------------------ summary (for the dashboard; never returns message text)
    def summary(self, now: float | None = None) -> dict:
        now = now or time.time(); c = self._c()
        by = {r[0]: r[1] for r in c.execute("SELECT ingestion_status, COUNT(*) FROM tg_capture GROUP BY ingestion_status")}
        oldest = c.execute("SELECT MIN(received_at) FROM tg_capture WHERE ingestion_status='CAPTURED'").fetchone()[0]
        gaps = {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM tg_gap GROUP BY status")}
        return {"rows": sum(by.values()), "by_status": by, "pending": by.get("CAPTURED", 0), "oldest_pending_age_s": (round(now - oldest, 1) if oldest else None), "dead_letter": by.get("FAILED", 0),
                "deletes_pending": c.execute("SELECT COUNT(*) FROM tg_delete WHERE status='PENDING'").fetchone()[0], "deletes_failed": c.execute("SELECT COUNT(*) FROM tg_delete WHERE status='FAILED'").fetchone()[0],
                "gaps": gaps, "open_gaps": gaps.get("OPEN", 0), "unrecoverable_gaps": gaps.get("UNRECOVERABLE", 0),
                "last_reconcile_at": float(self.kv_get("last_reconcile_at", 0) or 0) or None, "last_catch_up_at": float(self.kv_get("last_catch_up_at", 0) or 0) or None,
                "last_catch_up_ok": self.kv_get("last_catch_up_ok")}
