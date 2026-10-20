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

log = logging.getLogger("arena.tg_reader")
HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIR = os.path.join(HERE, "data", "tg")
SESSION = os.path.join(DIR, "reader")
STATUS = os.path.join(DIR, "status.json")
QUARANTINE = os.path.join(CI.DIR, "channel_quarantine.jsonl")
LOOKS_LIKE_SIGNAL = re.compile(r"\b(CE|PE|TARGET|STOP\s*LOSS|STOPLOSS|BUY\s*(RANGE|ABOVE)|NEAR\s*LEVEL)\b", re.I)
_counts: dict = {}


def source_ids() -> set[str]:
    return {x.strip() for x in (os.environ.get("TG_SOURCE_CHANNEL_IDS") or "").split(",") if x.strip()}


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
    return {"running": bool(age is not None and age < 180 and st.get("connected")), "connected": st.get("connected", False), "submit_enabled": submit_enabled(), "source_channels": len(source_ids()),
            "last_event_age_sec": (time.time() - st["last_event"]) if st.get("last_event") else None, "status_age_sec": age, "counts": st.get("counts", {}), "last_error": st.get("last_error")}


def _bump(k: str) -> None: _counts[k] = _counts.get(k, 0) + 1


def to_post(chat_id, message) -> dict:
    p = {"chat": {"id": chat_id, "type": "channel"}, "message_id": message.id, "date": int(message.date.timestamp()), "text": message.message or ""}
    rt = getattr(message, "reply_to", None)
    if rt is not None and getattr(rt, "reply_to_msg_id", None): p["reply_to_message"] = {"message_id": rt.reply_to_msg_id}
    return p


def process(post: dict, state: dict, *, edited: bool = False, participant: str, mode_fn, now: float | None = None, **kw) -> dict:
    """Pure-ish core (testable without Telegram). Returns the outcome dict."""
    text = post.get("text") or ""
    if edited:
        rec = {"outcome": "EDIT_IGNORED", "chat_id": str(post["chat"]["id"]), "message_id": post.get("message_id"), "text": text[:1500]}
        CI._log(rec, kw.get("log", CI.LOG)); _quarantine({**rec, "why": "edited post, never re-submitted"}); _bump("edit_ignored"); return rec
    out = CI.handle_post(post, state, participant=participant, mode_fn=mode_fn, now=now, dry_run=not submit_enabled(), **kw)
    _bump(out["outcome"].lower())
    if out["outcome"] == "IGNORED_NOT_A_SIGNAL" and LOOKS_LIKE_SIGNAL.search(text): _quarantine({**out, "why": "looks like a signal but did not parse"}); _bump("quarantined")
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

    def handle(ev, edited):
        try:
            post = to_post(ev.chat_id, ev.message)
            if str(post["chat"]["id"]) not in ids: return                        # belt and braces: never touch a non-listed chat
            r = process(post, state, edited=edited, participant=c["name"], mode_fn=lambda: api.mode(c))
            json.dump(state, open(CI.STATE, "w")); write_status(connected=True, last_event=time.time())
            log.info("channel=%s msg=%s outcome=%s", post["chat"]["id"], post["message_id"], r["outcome"])
        except Exception as e:
            _bump("errors"); write_status(last_error=type(e).__name__); log.exception("handler failed (%s)", type(e).__name__)

    client.add_event_handler(lambda ev: handle(ev, False), events.NewMessage(chats=chats))
    client.add_event_handler(lambda ev: handle(ev, True), events.MessageEdited(chats=chats))
    backoff = 5
    while True:
        try:
            await client.connect()
            if not await client.is_user_authorized(): write_status(connected=False, last_error="NOT_AUTHORIZED"); raise SystemExit("session is not authorized: run scripts/telethon_login.py")
            write_status(connected=True, last_error=None, started=time.time()); backoff = 5
            while client.is_connected():
                write_status(connected=True); await asyncio.sleep(30)
        except FloodWaitError as e:
            wait = min(int(e.seconds) + 5, 3600); _bump("flood_wait"); write_status(connected=False, last_error=f"FLOOD_WAIT_{wait}s"); await asyncio.sleep(wait)
        except SystemExit: raise
        except Exception as e:
            _bump("reconnects"); write_status(connected=False, last_error=type(e).__name__); log.warning("disconnected (%s), retry in %ss", type(e).__name__, backoff)
            await asyncio.sleep(backoff); backoff = min(backoff * 2, 300)                   # bounded exponential back-off
