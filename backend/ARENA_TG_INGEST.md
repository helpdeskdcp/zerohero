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

## Edits, deletions, push (phases 4-5)
Everything is prospective: an edit or a deletion can never rewrite what the market already did.
- **Edited post** (`channel_ingest.handle_edit`, `TG_EDITS=0` turns it off): the original became a ledger signal and the edit names the SAME contract -> the changed stop / targets / entry become `AMEND_SL` / `AMEND_TARGETS` / `AMEND_ENTRY`
  events stamped at the EDIT time (an entry change is ignored once the order filled). A DIFFERENT contract -> nothing applied, owner alerted (`EDIT_CONTRACT_CHANGED`). The original never became a signal (not a signal / dropped / still waiting
  for data) and the edit parses as one -> a NEW signal stamped at the edit time (never backdated); a pending original is replaced. An edit read > 30 min after it was made is not applied. Each edit version is processed once.
- **Deleted post** (`handle_delete`, `TG_DELETES=0` turns it off): detected by Telethon's `MessageDeleted` event AND by the reconciler (signal posts of the last 6 h are re-checked at the source; a failed check is never taken for a deletion).
  A post still waiting for data is dropped and never submitted. A submitted signal gets a `CANCEL` event at the detection time: it cancels an order that has not filled, and has NO effect on a filled trade (settlement honours CANCEL only before
  the fill), so deleting a losing post cannot erase it. Journal table `tg_delete` (unique per message), retried, FAILED after 5 attempts -> health RECOVERY_REQUIRED.
- **Push to clients**: `GET /api/arena/public/signals/stream` (token header, SSE, 10 min, <= 4 per participant) tails the ledger FILE (the reader is another process) and announces only `{type,id,signal_id,action,t}` of the caller's own
  records. `static/js/arena_push.js` keeps one stream per page; "My signals" and the Terminal reload at once; their own polling stays as the fallback.
