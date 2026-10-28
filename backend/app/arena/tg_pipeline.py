"""Telegram ingestion pipeline pieces that do not need a live Telegram session (so they are unit-testable with fakes):

  capture()          event / poll / backfill message  ->  one durable journal row (idempotent)
  process_pending()  journal rows waiting for the worker  ->  parse / dedupe / ledger via the existing channel_ingest path, row marked PROCESSED / retried / FAILED (dead letter)
  reconcile_channel() compare what the SOURCE has above a watermark with the journal; journal anything missing and record the gap + its outcome
  run_catch_up()     Telethon's own missed-update recovery after a (re)connect, with the outcome recorded
  health()           LIVE | DEGRADED | RECOVERY_REQUIRED with reasons, from the journal summary and the reader status

Recovery state machine:  LIVE -> (disconnect / silence / failed catch_up / open gap) DEGRADED -> catch_up + reconcile -> LIVE
                          a range that cannot be fetched after MAX_GAP_ATTEMPTS, a lost watermark, or a dead-letter row -> RECOVERY_REQUIRED until a human acknowledges it.
Nothing here marks an unrecoverable range as recovered."""
from __future__ import annotations

import asyncio
import time

MAX_GAP_ATTEMPTS = 5
GRACE_SEC = 45.0                     # a post younger than this may still be on its way by push / poll: the reconciler leaves it alone
RETRY_BACKOFF_CAP = 60.0


def capture(journal, chat_id, message, *, source: str, edited: bool = False, sender_ok: bool = True, now: float | None = None, recovery: str = "NONE") -> tuple[int | None, bool]:
    """Journal one Telethon message (duck-typed: id, message, date, edit_date, reply_to, sender_id). -> (row id, is_new). A repeat delivery returns (None, False)."""
    text = getattr(message, "message", None) or ""; date = getattr(message, "date", None)
    rt = getattr(message, "reply_to", None); reply = getattr(rt, "reply_to_msg_id", None) if rt is not None else None
    ed = getattr(message, "edit_date", None)
    ev = (int(ed.timestamp()) if ed is not None else int(now or time.time())) if edited else 0       # an edit is its own row, keyed by the edit time
    return journal.record(chat_id=chat_id, message_id=message.id, source_ts=(date.timestamp() if date is not None else None), text=text, received_at=now, edit_version=ev,
                          update_state=("edit" if edited else source), reply_to=reply, sender_id=getattr(message, "sender_id", None), status=("CAPTURED" if sender_ok else "IGNORED"), recovery=recovery)


def row_to_post(row: dict) -> dict:
    p = {"chat": {"id": row["chat_id"], "type": "channel"}, "message_id": row["message_id"], "date": int(row["source_ts"] or row["received_at"]), "text": row["text"] or ""}
    if row.get("reply_to"): p["reply_to_message"] = {"message_id": row["reply_to"]}
    return p


def process_pending(journal, state: dict, *, process_fn, participant_for, default_participant: str, mode_fn, save_state, alert=None, batch: int = 50, now: float | None = None) -> int:
    """Process waiting rows in per-chat order. The ledger stamp is the Telegram post time (channel_ingest use_post_time), and `now=received_at` makes the stale-post rule and the logged latency refer to when the
    post was CAPTURED, not to when the worker got to it. A failure is retried with a backoff and, after MAX_ATTEMPTS, parked as FAILED and alerted once. -> rows finished (processed or parked)."""
    t = now or time.time(); done = 0
    for row in journal.pending(batch):
        if row["attempts"] and t - (row["processed_at"] or 0) < min(RETRY_BACKOFF_CAP, 2 ** row["attempts"]): continue          # backing off after a failure
        try:
            out = process_fn(row_to_post(row), state, edited=bool(row["edit_version"]), participant=participant_for(row["chat_id"], default_participant), mode_fn=mode_fn, now=row["received_at"], alert=alert)
            save_state()
            journal.mark_done(row["id"], str((out or {}).get("outcome", "?")), (out or {}).get("signal_id"))
        except Exception as e:
            if journal.mark_error(row["id"], f"{type(e).__name__}: {e}", now=t) == "FAILED" and alert:
                try: alert(f"post {row['chat_id']}:{row['message_id']} could not be processed after {row['attempts'] + 1} attempts ({type(e).__name__}); parked as FAILED in the capture journal. It is NOT in the ledger.")
                except Exception: pass
        done += 1
    return done


def _gap_for(journal, chat_id, kind):
    for g in journal.gaps("OPEN", 200):
        if g["kind"] == kind and g["chat_id"] == (None if chat_id is None else str(chat_id)): return g
    return None


async def reconcile_channel(client, journal, cid, *, fetch, now: float | None = None, grace: float = GRACE_SEC, cap: int = 500) -> dict:
    """Compare the source above this channel's watermark with the journal. A message the source has and the journal lacks is journaled now (update_state=backfill, recovery_status=BACKFILLED) and the gap is
    recorded as RECOVERED with a count; a fetch that fails is recorded as an OPEN gap and escalates to UNRECOVERABLE after MAX_GAP_ATTEMPTS. The watermark only advances over messages that are journaled.
    No history before the first run is back-filled (the watermark starts at the newest journaled id)."""
    k = str(cid); t = now or time.time(); res = {"checked": 0, "missing": 0, "recovered": 0, "failed": False}
    wm = int(journal.kv_get(f"wm:{k}", 0) or 0)
    if wm == 0:
        wm = journal.max_message_id(k)
        if wm == 0: return res                                                       # nothing journaled yet: the poll loop baselines this channel
        journal.kv_set(f"wm:{k}", wm)
    try:
        msgs = await fetch(client, cid, wm)
    except Exception as e:
        g = _gap_for(journal, cid, "FETCH_FAILED"); gid = g["id"] if g else journal.open_gap(chat_id=k, from_id=wm, to_id=None, kind="FETCH_FAILED", note=f"{type(e).__name__}")
        tries = (g["attempts"] if g else 0) + 1
        journal.update_gap(gid, attempt=True, status=("UNRECOVERABLE" if tries >= MAX_GAP_ATTEMPTS else "OPEN"), note=f"{type(e).__name__} (attempt {tries})")
        res["failed"] = True; return res
    g = _gap_for(journal, cid, "FETCH_FAILED")
    if g: journal.update_gap(g["id"], status="RECOVERED", note="source reachable again")      # the fetch works again; nothing was skipped because the watermark did not move
    new_wm = wm
    for m in msgs:
        if t - m.date.timestamp() < grace: break                                     # too fresh: leave it to push / poll, check again next round
        res["checked"] += 1
        if not journal.has(k, m.id, 0):
            gid = journal.open_gap(chat_id=k, from_id=m.id, to_id=m.id, kind="MISSING_MESSAGE", note="present at the source, absent from the journal")
            _, new = capture(journal, k, m, source="backfill", sender_ok=True, now=t, recovery="BACKFILLED")
            journal.update_gap(gid, status="RECOVERED", attempt=True, recovered=1 if new else 0, note="journaled by the reconciler")
            res["missing"] += 1; res["recovered"] += 1 if new else 0
        new_wm = max(new_wm, m.id)
    if new_wm != wm: journal.kv_set(f"wm:{k}", new_wm)
    return res


async def run_catch_up(client, journal, *, ctx: dict | None = None, timeout: float = 120.0) -> bool:
    """Ask Telegram for the updates missed while we were offline (Telethon replays them through the normal event handlers, which journal them as update_state=catch_up)."""
    journal.kv_set("last_catch_up_at", time.time())
    if ctx is not None: ctx["source"] = "catch_up"
    try:
        await asyncio.wait_for(client.catch_up(), timeout)
        journal.kv_set("last_catch_up_ok", "1")
        g = _gap_for(journal, None, "CATCH_UP_FAILED")
        if g: journal.update_gap(g["id"], status="RECOVERED", note="a later catch_up succeeded")
        return True
    except Exception as e:
        journal.kv_set("last_catch_up_ok", "0")
        g = _gap_for(journal, None, "CATCH_UP_FAILED")
        if g: journal.update_gap(g["id"], attempt=True, note=f"{type(e).__name__}")
        else: journal.open_gap(chat_id=None, from_id=None, to_id=None, kind="CATCH_UP_FAILED", note=f"{type(e).__name__}")
        return False
    finally:
        if ctx is not None: ctx["source"] = "push"


def health(summary: dict, status: dict, now: float | None = None, *, poll_stale_s: float = 60.0, backlog_s: float = 30.0, reconcile_stale_s: float = 300.0) -> dict:
    """-> {"state": LIVE | DEGRADED | RECOVERY_REQUIRED, "reasons": [...]}. RECOVERY_REQUIRED = something is lost or stuck that needs a human (an unrecoverable gap, a dead-letter post)."""
    t = now or time.time(); deg, req = [], []
    if not status.get("connected"): deg.append(f"not connected (last_error {status.get('last_error')})")
    lp = status.get("last_poll")
    if lp and t - float(lp) > poll_stale_s: deg.append(f"poll loop silent {int(t - float(lp))} s")
    if summary.get("open_gaps"): deg.append(f"{summary['open_gaps']} open gap(s) being recovered")
    oa = summary.get("oldest_pending_age_s")
    if oa and oa > backlog_s: deg.append(f"journal backlog: oldest waiting post {int(oa)} s")
    if summary.get("last_catch_up_ok") == "0": deg.append("last catch_up failed")
    lr, started = summary.get("last_reconcile_at"), status.get("started")
    if started and t - float(started) > reconcile_stale_s and (not lr or t - lr > reconcile_stale_s): deg.append("reconciliation has not run for over 5 min")
    if summary.get("unrecoverable_gaps"): req.append(f"{summary['unrecoverable_gaps']} unrecoverable gap(s) not yet acknowledged")
    if summary.get("dead_letter"): req.append(f"{summary['dead_letter']} dead-letter post(s) (could not be processed)")
    return {"state": "RECOVERY_REQUIRED" if req else "DEGRADED" if deg else "LIVE", "reasons": req + deg}
