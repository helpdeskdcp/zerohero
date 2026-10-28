"""Read-only Telegram channel reader (Telethon, an authorized member account). Reads ONLY the channel ids in TG_SOURCE_CHANNEL_IDS (verified numeric ids, never titles). New posts go through
channel_ingest.handle_post (same parser/dedupe/ledger path as the bot route). Until TG_INGEST_SUBMIT=1 it is LOG-ONLY (outcomes WOULD_SUBMIT/WOULD_CANCEL, nothing enters the ledger).
Edited posts are never re-submitted (logged + quarantined). Messages that look like a signal but do not parse are quarantined for human review. It sends nothing, joins nothing, forwards nothing;
no history is back-filled (signals are stamped with the receive time). Secrets are never logged."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time

from . import channel_ingest as CI
from . import tg_journal as TJ
from . import tg_pipeline as TP

log = logging.getLogger("arena.tg_reader")
HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIR = os.path.join(HERE, "data", "tg")
SESSION = os.path.join(DIR, "reader")
STATUS = os.path.join(DIR, "status.json")
QUARANTINE = os.path.join(CI.DIR, "channel_quarantine.jsonl")
LAST_IDS = os.path.join(DIR, "last_ids.json")
POLL_SEC = float(os.environ.get("TG_POLL_SEC", "2.5"))   # was 15 s (median post->reader lag 9 s on 2026-10-26, p90 15 s); Telegram does not push these channel posts in real time to this session, so the loop is the only fast path
FETCH_CAP = int(os.environ.get("TG_FETCH_CAP", "200"))     # posts taken per channel per poll, OLDEST first; a bigger backlog is drained over consecutive polls without a pause
RECONCILE_SEC = float(os.environ.get("TG_RECONCILE_SEC", "60"))   # how often the reconciler re-checks the source history against the capture journal
POLL_MAX_SEC = 15.0                                        # after a FloodWait the interval doubles up to this and decays back by itself


async def fetch_new(client, cid, last_id: int, cap: int | None = None) -> list:
    """Every message of `cid` with id > last_id, OLDEST FIRST, at most `cap` per call. Telethon's reverse iteration starts at min_id + 1 and walks forward, so advancing last_id message by message can never
    skip one: whatever exceeds `cap` is simply fetched by the next call. (The previous get_messages(limit=20, min_id=...) returned the 20 NEWEST messages above last_id; with more than 20 new posts
    (a burst, or a restart after an outage) the older ones were never fetched and last_id jumped past them: a silent, permanent gap.)"""
    out = []
    async for m in client.iter_messages(cid, min_id=last_id, reverse=True, limit=cap or FETCH_CAP): out.append(m)
    out.sort(key=lambda x: x.id)
    return out


async def drain_channel(client, cid, last: dict, handle_msg, save) -> int:
    """One poll step of one channel: fetch everything above last[cid] (oldest first, capped), hand each message to `handle_msg`, advance and persist the high-water mark AFTER each message (so a crash
    re-reads at most the message being handled; the per-post dedupe makes that harmless). Returns how many messages were handled."""
    k = str(cid); new = await fetch_new(client, cid, last[k])
    for m in new:
        handle_msg(cid, m); last[k] = max(last[k], m.id); save()
    return len(new)


def next_poll(poll: float, flooded: bool, since_flood: float, base: float = POLL_SEC, cap: float = POLL_MAX_SEC, calm_sec: float = 300.0) -> float:
    """Adaptive polling interval: x2 (up to `cap`) right after a FloodWait, halved (down to `base`) once `calm_sec` seconds passed without one."""
    if flooded: return min(cap, max(poll, base) * 2)
    if poll > base and since_flood >= calm_sec: return max(base, poll / 2)
    return poll
LOOKS_LIKE_SIGNAL = re.compile(r"\b(CE|PE|TARGET|STOP\s*LOSS|STOPLOSS|BUY\s*(RANGE|ABOVE)|NEAR\s*LEVEL)\b", re.I)
CLAIM_LIKE = re.compile(r"\b(GAINING|PROFIT|TARGET\s*HIT|ALL\s*TARGET|FULL\s*HIT|ACHIEVED|BOOKED|RESULT)\b", re.I)          # result/claim chatter is not a missed signal: no alert
_counts: dict = {}


async def fetch_ids(client, cid, ids: list) -> set:
    """Which of these message ids still exist in the channel (a missing id comes back as None / MessageEmpty). An error raises: it must never be mistaken for a deletion."""
    got = await client.get_messages(cid, ids=list(ids))
    return {m.id for m in (got or []) if m is not None and m.__class__.__name__ != "MessageEmpty" and getattr(m, "id", None)}


def source_ids() -> set[str]:
    return {x.strip() for x in (os.environ.get("TG_SOURCE_CHANNEL_IDS") or "").split(",") if x.strip()}


def participant_for(chat_id, default: str) -> str:
    """Which arena participant a channel belongs to: TG_CHANNEL_PARTICIPANTS='<channel id>:<NAME>,...'; unlisted channels fall back to the default participant."""
    m = {}
    for x in (os.environ.get("TG_CHANNEL_PARTICIPANTS") or "").split(","):
        if ":" in x: k, v = x.split(":", 1); m[k.strip()] = v.strip().upper()
    return m.get(str(chat_id), default)


def anon_only_chats() -> set[str]:
    return {x.strip() for x in (os.environ.get("TG_ANON_ONLY_CHATS") or "").split(",") if x.strip()}


def sender_allowed(chat_id, sender_id) -> bool:
    """A GROUP whose posts come from an anonymous admin / the group itself (no user sender) may be listed in TG_ANON_ONLY_CHATS: only such messages count; a message from an ordinary member (a real user id) is ignored.
    Channels (broadcast) need no such check."""
    if str(chat_id) not in anon_only_chats(): return True
    return sender_id is None or str(sender_id) == str(chat_id)


def journaling_enabled() -> bool:
    """TG_JOURNAL=0 switches back to the old direct path (parse + submit inside the Telegram callback) without a code change."""
    return (os.environ.get("TG_JOURNAL") or "1").strip().lower() not in ("0", "false", "off", "no")


def edits_enabled() -> bool:
    """TG_EDITS=0 restores the old behaviour (an edit is journaled and logged, never applied)."""
    return os.environ.get("TG_EDITS", "1") != "0"


def deletes_enabled() -> bool:
    return os.environ.get("TG_DELETES", "1") != "0"


def submit_enabled() -> bool:
    return (os.environ.get("TG_INGEST_SUBMIT") or "").strip().lower() in ("1", "true", "on", "yes")


def write_status(**kw) -> None:
    st = _load_status(); st.update(kw, updated=time.time(), counts=_counts)
    os.makedirs(DIR, exist_ok=True); tmp = STATUS + ".tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, STATUS)


def _load_status() -> dict:
    try: return json.load(open(STATUS))
    except Exception: return {}


def public_status() -> dict:
    st = _load_status(); age = (time.time() - st["updated"]) if st.get("updated") else None
    stale = age is None or age > 180
    state = st.get("state") if not stale else "DEGRADED"                                                  # a reader that stopped writing its status is never reported as LIVE
    reasons = list(st.get("reasons") or []) + ([f"reader status stale ({int(age)} s)" if age is not None else "no status file"] if stale else [])
    return {"running": bool(age is not None and age < 180 and st.get("connected")), "connected": st.get("connected", False), "submit_enabled": submit_enabled(), "source_channels": len(source_ids()),
            "last_event_age_sec": (time.time() - st["last_event"]) if st.get("last_event") else None, "status_age_sec": age, "counts": st.get("counts", {}), "last_error": st.get("last_error"),
            "journaling": journaling_enabled(), "state": state or ("LIVE" if not journaling_enabled() else "UNKNOWN"), "reasons": reasons, "journal": st.get("journal")}


def _bump(k: str) -> None: _counts[k] = _counts.get(k, 0) + 1


def to_post(chat_id, message) -> dict:
    p = {"chat": {"id": chat_id, "type": "channel"}, "message_id": message.id, "date": int(message.date.timestamp()), "text": message.message or ""}
    rt = getattr(message, "reply_to", None)
    if rt is not None and getattr(rt, "reply_to_msg_id", None): p["reply_to_message"] = {"message_id": rt.reply_to_msg_id}
    ed = getattr(message, "edit_date", None)
    if ed is not None: p["edit_date"] = int(ed.timestamp())
    return p


def process(post: dict, state: dict, *, edited: bool = False, participant: str, mode_fn, now: float | None = None, alert=None, **kw) -> dict:
    """Pure-ish core (testable without Telegram). Returns the outcome dict."""
    text = post.get("text") or ""
    if edited and edits_enabled():
        out = CI.handle_edit(post, state, participant=participant, mode_fn=mode_fn, now=now, dry_run=not submit_enabled(), alert=alert if alert is not None else send_alert, **kw)
        _bump(out["outcome"].lower()); return out
    if edited:
        rec = {"outcome": "EDIT_IGNORED", "chat_id": str(post["chat"]["id"]), "message_id": post.get("message_id"), "text": text[:1500]}
        CI._log(rec, kw.get("log", CI.LOG)); _quarantine({**rec, "why": "edited post, never re-submitted"}); _bump("edit_ignored"); return rec
    out = CI.handle_post(post, state, participant=participant, mode_fn=mode_fn, now=now, dry_run=not submit_enabled(), use_post_time=True, alert=alert if alert is not None else send_alert, **kw)
    _bump(out["outcome"].lower())
    if out["outcome"] == "IGNORED_NOT_A_SIGNAL" and LOOKS_LIKE_SIGNAL.search(text):
        _quarantine({**out, "why": "looks like a signal but did not parse"}); _bump("quarantined")
        if submit_enabled() and not CLAIM_LIKE.search(text): send_alert(f"signal-like post NOT parsed (chat {post['chat']['id']} msg {post.get('message_id')}): {text[:200]!r}")
    return out


def _send_alert_now(text: str) -> None:
    """Private message to the owner's Telegram chat (never a public channel). Failure to alert must never break ingestion."""
    try:
        from ..connectors import telegram
        telegram._send("Arena ingest: " + text, os.environ.get("TELEGRAM_CHAT_ID"))
    except Exception:
        log.warning("alert could not be sent")


def send_alert(text: str) -> None:
    """Fire-and-forget: the HTTP call (timeout 8 s) runs in a daemon thread, so an alert can never stall the event loop that receives and polls the posts."""
    import threading
    threading.Thread(target=_send_alert_now, args=(text,), daemon=True, name="arena-ingest-alert").start()


def retry_pending(state: dict, *, mode_fn, default_participant: str, now: float | None = None, **kw) -> list:
    """Re-run posts whose contract data was not captured yet. Each is stamped at its Telegram post time when it finally goes through; at STALE_SEC it ends as STALE_NOT_SUBMITTED (alerted)."""
    out = []
    for key, v in list(state.get("pending", {}).items()):
        out.append(process(v["post"], state, participant=v.get("participant") or default_participant, mode_fn=mode_fn, now=now, retry=True, **kw))
    return out


def _quarantine(rec: dict) -> None:
    os.makedirs(os.path.dirname(QUARANTINE), exist_ok=True)
    with open(QUARANTINE, "a") as f: f.write(json.dumps(rec, ensure_ascii=False) + "\n")


async def run() -> None:                                                       # pragma: no cover (needs a live session)
    from telethon import TelegramClient, events
    from telethon.errors import FloodWaitError
    ids = source_ids()
    if not ids: raise SystemExit("TG_SOURCE_CHANNEL_IDS is empty: nothing to read")
    from . import api
    c = api.cfg(); state = CI._load(CI.STATE, {}); [state.setdefault(k, {}) for k in ("seen", "by_msg", "last_by_chat")]
    client = TelegramClient(SESSION, int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"], connection_retries=-1, retry_delay=5, auto_reconnect=True)
    chats = [int(x) for x in ids]
    journal = None
    if journaling_enabled():
        try: journal = TJ.Journal()
        except Exception as e: _bump("journal_unavailable"); log.error("capture journal unavailable (%s): using the direct path", type(e).__name__)
    ctx = {"source": "push"}; wake = asyncio.Event(); mode_fn = lambda: api.mode(api.cfg())

    def handle_legacy(ev, edited):
        """The original direct path: parse + submit inside the callback. Used when TG_JOURNAL=0, when the journal cannot be opened, and as the fallback if a journal write fails (a post is never dropped)."""
        try:
            post = to_post(ev.chat_id, ev.message)
            if str(post["chat"]["id"]) not in ids: return
            if not sender_allowed(post["chat"]["id"], getattr(ev.message, "sender_id", None)): _bump("member_message_ignored"); return          # group member, not the anonymous admin
            r = process(post, state, edited=edited, participant=participant_for(post["chat"]["id"], c["name"]), mode_fn=mode_fn)
            CI.save_json(CI.STATE, state); write_status(connected=True, last_event=time.time())
            log.info("channel=%s msg=%s outcome=%s", post["chat"]["id"], post["message_id"], r["outcome"])
        except Exception as e:
            _bump("errors"); write_status(last_error=type(e).__name__); log.exception("handler failed (%s)", type(e).__name__)

    def handle(ev, edited, source=None):
        """Event / poll callback. With the journal this does ONE cheap insert and wakes the worker: no network, no parsing, no ledger work in the Telegram callback."""
        if journal is None: return handle_legacy(ev, edited)
        try:
            cid = ev.chat_id
            if str(cid) not in ids: return
            ok = sender_allowed(cid, getattr(ev.message, "sender_id", None))
            _, new = TP.capture(journal, cid, ev.message, source=source or ctx["source"], edited=edited, sender_ok=ok)
            if not ok: _bump("member_message_ignored")
            elif new: wake.set()
            else: _bump("duplicate")
            write_status(connected=True, last_event=time.time())
        except Exception as e:
            _bump("journal_errors"); log.exception("journal write failed (%s): processing this post directly", type(e).__name__)
            handle_legacy(ev, edited)

    def handle_msg(cid, m):
        class _E: pass
        e = _E(); e.chat_id = cid; e.message = m; handle(e, False, "poll")

    def process_logged(post, st, **kw):
        r = process(post, st, **kw); log.info("channel=%s msg=%s outcome=%s", post["chat"]["id"], post.get("message_id"), r["outcome"]); write_status(last_processed=time.time()); return r

    async def process_loop():
        """The single worker: takes journaled posts in per-chat order and runs the existing parse / dedupe / ledger path in a thread (so a slow disk or lock never stalls the Telegram loop)."""
        loop = asyncio.get_running_loop()
        while True:
            n = 0
            try:
                n = await loop.run_in_executor(None, lambda: TP.process_pending(journal, state, process_fn=process_logged, participant_for=participant_for, default_participant=c["name"], mode_fn=mode_fn,
                                                                                    save_state=lambda: CI.save_json(CI.STATE, state), alert=send_alert))
                if deletes_enabled():
                    n += await loop.run_in_executor(None, lambda: TP.process_deletes(journal, state, delete_fn=CI.handle_delete, participant_for=participant_for, default_participant=c["name"],
                                                                                      save_state=lambda: CI.save_json(CI.STATE, state), alert=send_alert))
                if state.get("pending"):
                    await loop.run_in_executor(None, lambda: (retry_pending(state, mode_fn=mode_fn, default_participant=c["name"]), CI.save_json(CI.STATE, state)))
            except Exception as e:
                _bump("worker_errors"); write_status(last_error=type(e).__name__); log.exception("worker failed (%s)", type(e).__name__)
            if n == 0:
                try: await asyncio.wait_for(wake.wait(), 1.0)
                except asyncio.TimeoutError: pass
                wake.clear()

    async def poll_loop():
        # Telegram pushes channel posts to this session only in ~30-minute batches, so every POLL_SEC seconds the new messages are fetched directly (oldest first, see fetch_new).
        # Read-only; the high-water mark is persisted, and with the journal it can be rebuilt from the newest journaled id if the pointer file is ever lost.
        last = CI._load(LAST_IDS, {}); poll = POLL_SEC; last_flood = 0.0
        while True:
            backlog = False
            try:
                for cid in chats:
                    k = str(cid)
                    if k not in last:
                        jm = journal.max_message_id(k) if journal else 0
                        if jm: last[k] = jm                                                                  # pointer file lost, journal intact: resume exactly where the journal ends
                        else:
                            if journal and journal.kv_get(f"seen_chat:{k}"):
                                journal.open_gap(chat_id=k, from_id=None, to_id=None, kind="STATE_LOST", status="UNRECOVERABLE", note="poll pointer and journal both missing for a known channel: posts made meanwhile cannot be listed")
                            top = await client.get_messages(cid, limit=1); last[k] = top[0].id if top else 0
                            if journal: journal.kv_set(f"seen_chat:{k}", "1")
                        CI.save_json(LAST_IDS, last); continue
                    if await drain_channel(client, cid, last, handle_msg, lambda: CI.save_json(LAST_IDS, last)) >= FETCH_CAP:
                        backlog = True; _bump("fetch_capped")                                                   # more may be waiting: poll again at once
                if state.get("pending") and journal is None:
                    retry_pending(state, mode_fn=mode_fn, default_participant=c["name"]); CI.save_json(CI.STATE, state)
                write_status(connected=True, last_poll=time.time())
            except FloodWaitError as e:
                _bump("flood_wait"); last_flood = time.time(); poll = next_poll(poll, True, 0.0); await asyncio.sleep(min(int(e.seconds) + 5, 3600))
            except Exception as e:
                _bump("poll_errors"); write_status(last_error=type(e).__name__)
            poll = next_poll(poll, False, time.time() - last_flood)
            await asyncio.sleep(0.2 if backlog else poll)

    async def reconcile_loop():
        """Once per connection: Telethon catch_up (replays missed updates through the handlers). Then, every RECONCILE_SEC, compare the source with the journal and journal anything missing."""
        if journal is None: return
        await TP.run_catch_up(client, journal, ctx=ctx)
        while True:
            try:
                for cid in chats:
                    await TP.reconcile_channel(client, journal, cid, fetch=fetch_new)
                    if deletes_enabled() and await TP.detect_deletions(client, journal, cid, fetch_ids=fetch_ids): wake.set()
                journal.kv_set("last_reconcile_at", time.time())
            except FloodWaitError as e:
                _bump("flood_wait"); await asyncio.sleep(min(int(e.seconds) + 5, 3600))
            except Exception as e:
                _bump("reconcile_errors"); log.warning("reconcile failed (%s)", type(e).__name__)
            await asyncio.sleep(RECONCILE_SEC)

    health_memo = {"state": "LIVE", "since": time.time(), "alerted": False, "was_bad": False}

    def publish_health():
        """LIVE / DEGRADED / RECOVERY_REQUIRED into status.json every few seconds; the owner is alerted once when a bad state lasts a minute, and once when it clears."""
        if journal is None: return
        now = time.time(); h = TP.health(journal.summary(now), _load_status(), now); write_status(state=h["state"], reasons=h["reasons"], journal=journal.summary(now))
        if h["state"] != health_memo["state"]: health_memo.update(state=h["state"], since=now, alerted=False)
        if h["state"] != "LIVE" and not health_memo["alerted"] and now - health_memo["since"] >= 60:
            send_alert(f"ingestion {h['state']}: " + "; ".join(h["reasons"])[:300]); health_memo.update(alerted=True, was_bad=True)
        elif h["state"] == "LIVE" and health_memo["was_bad"] and now - health_memo["since"] >= 30:
            send_alert("ingestion is LIVE again"); health_memo["was_bad"] = False

    async def on_new(ev): handle(ev, False)                                              # Telethon awaits the callback: a plain lambda returned None -> "TypeError: NoneType can't be used in 'await'" on every pushed event
    async def on_edit(ev): handle(ev, True)

    async def on_delete(ev):
        """Telegram says these message ids were deleted: one cheap journal insert per id, the worker does the rest. A deletion event of a channel carries the channel id; if it does not, the id is matched against every watched channel's journal."""
        if journal is None or not deletes_enabled(): return
        try:
            cids = [str(ev.chat_id)] if getattr(ev, "chat_id", None) is not None else [str(x) for x in ids]
            for cid in cids:
                if cid not in {str(x) for x in ids}: continue
                for mid in (ev.deleted_ids or []):
                    if getattr(ev, "chat_id", None) is None and not journal.has(cid, mid, 0): continue
                    if journal.record_delete(cid, mid, "event"): wake.set()
            write_status(connected=True, last_event=time.time())
        except Exception as e:
            _bump("journal_errors"); log.exception("delete journal write failed (%s)", type(e).__name__)
    client.add_event_handler(on_new, events.NewMessage(chats=chats))
    client.add_event_handler(on_edit, events.MessageEdited(chats=chats))
    client.add_event_handler(on_delete, events.MessageDeleted())                           # deletions carry no sender / chat filter reliably: on_delete filters by the journal itself
    worker = asyncio.ensure_future(process_loop()) if journal is not None else None            # runs for the life of the process: captured posts are processed even while Telegram is down
    backoff = 5; tasks: list = []
    while True:
        for t in tasks: t.cancel()                                                       # whatever a previous connection left running (poller / reconciler) must not run twice
        tasks.clear()
        try:
            await client.connect()
            if not await client.is_user_authorized(): write_status(connected=False, last_error="NOT_AUTHORIZED"); raise SystemExit("session is not authorized: run scripts/telethon_login.py")
            write_status(connected=True, last_error=None, started=time.time()); backoff = 5
            poller = asyncio.ensure_future(poll_loop()); recon = asyncio.ensure_future(reconcile_loop()); tasks.extend([poller, recon])
            while client.is_connected():
                write_status(connected=True)
                try: publish_health()
                except Exception: _bump("health_errors")
                await asyncio.sleep(5)
            poller.cancel(); recon.cancel()
        except FloodWaitError as e:
            wait = min(int(e.seconds) + 5, 3600); _bump("flood_wait"); write_status(connected=False, last_error=f"FLOOD_WAIT_{wait}s"); await asyncio.sleep(wait)
        except SystemExit: raise
        except Exception as e:
            _bump("reconnects"); write_status(connected=False, last_error=type(e).__name__); log.warning("disconnected (%s), retry in %ss", type(e).__name__, backoff)
            await asyncio.sleep(backoff); backoff = min(backoff * 2, 300)                   # bounded exponential back-off
