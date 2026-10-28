# Telegram -> Arena ingestion (phases 1-3)

Reader: `scripts/arena_tg_reader.py` (systemd `arena-tg-reader`) -> `app/arena/tg_reader.py`. Journal: `data/tg/capture.db` (SQLite, mode 600). Logic without Telegram: `app/arena/tg_pipeline.py`, storage: `app/arena/tg_journal.py`.

## Flow
Telegram push event | 2.5 s poll (oldest first, `TG_FETCH_CAP`) | reconciler backfill | `catch_up()` replay
-> **journal insert** (`tg_capture`, UNIQUE chat/message/edit_version; the only work done in the Telegram callback)
-> **worker** (one, per-chat order, in a thread): `channel_ingest.handle_post` (parse, dedupe, contract check, ledger) -> row PROCESSED / retried with backoff / FAILED after 5 attempts (dead letter)
-> hash-chained ledger -> participant UI.
If a journal write fails the post is processed directly (never dropped). `TG_JOURNAL=0` restores the old direct path entirely.

## Tables
`tg_capture` (chat_id, message_id, edit_version, source_ts, received_at, update_state push|poll|catch_up|backfill|edit, content_hash, text, ingestion_status CAPTURED|PROCESSED|IGNORED|FAILED, recovery_status NONE|BACKFILLED, outcome, signal_id, attempts)
`tg_gap` (chat_id, from_id, to_id, kind MISSING_MESSAGE|FETCH_FAILED|CATCH_UP_FAILED|STATE_LOST, status OPEN|RECOVERED|UNRECOVERABLE|ACKED, attempts, recovered_count) and `tg_kv` (watermarks, last catch_up / reconcile).

## States (`/api/arena/public/ingest/status` -> `state`, `reasons`, `journal`)
- **LIVE** connected, polling, no open gap, no backlog, reconciliation running.
- **DEGRADED** not connected, poll silent > 60 s, open gap, backlog > 30 s, last catch_up failed, reconciler silent > 5 min, or the status file is stale.
- **RECOVERY_REQUIRED** an UNRECOVERABLE gap not acknowledged, or a dead-letter post. A range is never marked recovered unless a message was actually journaled.
The owner gets a Telegram alert when a bad state lasts a minute, and when it clears.

## Operate
`python scripts/tg_gap_ack.py status | gaps [STATUS] | ack GAP_ID "note" | failed | requeue ROW_ID` (never prints message text).
Env: `TG_JOURNAL` (1), `TG_RECONCILE_SEC` (60), `TG_FETCH_CAP` (200), `TG_POLL_SEC` (2.5), `TG_STATE_FSYNC` (0).

## Not done yet (phases 4-5)
Edits are journaled as their own versions but still not re-submitted (no MODIFY type); deletions are not handled; new signals are not pushed to clients (UI polls).
