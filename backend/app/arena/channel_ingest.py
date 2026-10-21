"""Consent-based ingestion of the institution's own Telegram channel posts into the arena ledger (route A: our bot is an ADMIN of the channel; the owner agreed).
Only channels whitelisted in ARENA_CHANNEL_IDS are read. Each post is stamped with OUR receive time (never earlier than the post, so no look-ahead); the Telegram post time is logged beside it.
A post that cannot be parsed, is unsupported, or names an unscorable contract is LOGGED and NOT submitted (nothing is guessed). Channel result claims ("target hit", "points achieved") are stored as claims only and never scored:
the arena settles from market data. 'Trade cancelled' becomes a CANCEL event on the signal the post replies to (else the latest signal from that channel). Idempotent by (chat_id, message_id)."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

from . import channel_parse as CP
from . import contracts as CT
from . import submit as SU

IST = timezone(timedelta(hours=5, minutes=30))
DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "arena")
STATE = os.path.join(DIR, "channel_ingest_state.json")
LOG = os.path.join(DIR, "channel_ingest_log.jsonl")
EXCH = {"NIFTY": "NSE", "BANKNIFTY": "NSE", "SENSEX": "BSE", "NATURALGAS": "MCX", "CRUDEOIL": "MCX"}


def channel_ids() -> set[str]:
    return {x.strip() for x in (os.environ.get("ARENA_CHANNEL_IDS") or "").split(",") if x.strip()}


def _load(p, default):
    try: return json.load(open(p))
    except Exception: return default


def _log(rec: dict, log: str):
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "a") as f: f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def pick_expiry(underlying: str, today_iso: str, path: str | None = None) -> str | None:
    ex = sorted(e for e in CT.captured(underlying, None, path)["expiries"] if e >= today_iso)
    return ex[0] if ex else None


def _market_open(und: str) -> bool:
    try:
        from .. import market_calendar as mc
        return bool(mc.is_trading(EXCH[und]))
    except Exception:
        return False


def handle_post(post: dict, state: dict, *, participant: str, mode_fn, ledger: str | None = None, log: str = LOG, db_path: str | None = None, now: float | None = None, dry_run: bool = False, use_post_time: bool = False) -> dict:
    """One channel_post -> outcome dict (also appended to the log). Mutates `state['seen']`.
    use_post_time: the ledger stamp is Telegram's own time of the post (the instant the signal became public), never later than receipt, so a late reader cannot penalise the poster;
    a post read more than STALE_SEC after it was posted is logged and NOT submitted."""
    chat = str(post["chat"]["id"]); mid = post.get("message_id"); key = f"{chat}:{mid}"
    t_recv = now or time.time(); text = post.get("text") or post.get("caption") or ""
    t = min(float(post["date"]), t_recv) if (use_post_time and post.get("date")) else t_recv
    stale = bool(use_post_time and t_recv - t > STALE_SEC)
    base = {"key": key, "chat_id": chat, "message_id": mid, "tg_date": datetime.fromtimestamp(post.get("date", t), tz=timezone.utc).isoformat(), "received_iso": datetime.fromtimestamp(t_recv, tz=timezone.utc).isoformat(),
            "latency_sec": round(t_recv - t, 1), "text": text[:1500]}
    if key in state["seen"]: return {"outcome": "DUPLICATE", **base}
    out = _decide(post, state, base, text, t, participant, mode_fn, ledger, db_path, dry_run, stale)
    state["seen"][key] = out.get("signal_id") or out["outcome"]
    _log({**base, **out}, log)
    return {**base, **out}


DEFAULT_SL_FRAC = 0.70                              # default stop when a post publishes none: 30% below the entry (fixed in advance, same for everyone, not an AI output)
STALE_SEC = 1800                                   # a post read more than 30 min after it was posted is logged, not submitted


def _decide(post, state, base, text, t, participant, mode_fn, ledger, db_path, dry_run=False, stale=False) -> dict:
    if not text.strip(): return {"outcome": "IGNORED_NO_TEXT"}
    r = CP.parse_signal(text)
    if not r["ok"]:
        u = CP.parse_update(text)
        if u is None: return {"outcome": "IGNORED_NOT_A_SIGNAL", "reason": r["reason"]}
        if u["kind"] != "CANCEL": return {"outcome": "CLAIM_RECORDED", "claim": u["kind"], "note": "channel claim, never scored"}
        reply = (post.get("reply_to_message") or {}).get("message_id")
        sid = state["by_msg"].get(f"{base['chat_id']}:{reply}") if reply else state["last_by_chat"].get(base["chat_id"])
        if not sid: return {"outcome": "CANCEL_NO_TARGET"}
        if dry_run: return {"outcome": "WOULD_CANCEL", "signal_id": sid}
        try: ev = SU.submit_event({"signal_id": sid, "action": "CANCEL", "note": "channel: trade cancelled"}, participant, path=ledger, now=t)
        except SU.SubmissionError as e: return {"outcome": "CANCEL_REJECTED", "reason": str(e)}
        return {"outcome": "CANCEL_SUBMITTED", "event_id": ev["id"], "signal_id": sid}
    s = r["signal"]
    if not s["supported"]: return {"outcome": "UNSUPPORTED_INSTRUMENT", "underlying": s["underlying"]}
    if stale: return {"outcome": "STALE_NOT_SUBMITTED", "underlying": s["underlying"]}
    default_sl = s.get("sl") is None
    if default_sl: s["sl"] = round(s["entry"] * DEFAULT_SL_FRAC, 2)                          # the channel does not publish its stop ("STOPLOSS :- VIP"): the pre-registered arena default applies, NOT the AI
    und = s["underlying"]
    if mode_fn() == "ENDED": return {"outcome": "ARENA_ENDED"}
    scored = mode_fn() == "ARENA_LIVE"
    if scored and not _market_open(und): return {"outcome": "MARKET_CLOSED_NOT_SUBMITTED", "underlying": und}
    today = datetime.fromtimestamp(t, tz=IST).date().isoformat()
    hint = s.get("expiry_hint")
    if hint:                                                                                  # the post names day + month: use ONLY a captured expiry with exactly that day and month on/after the post date
        cands = [e for e in sorted(CT.captured(und, None, db_path)["expiries"]) if e >= today and int(e[8:10]) == hint["day"] and int(e[5:7]) == hint["month"]]
        exp = cands[0] if cands else None
        if not exp: return {"outcome": "NO_CAPTURED_EXPIRY_FOR_POST", "underlying": und, "hint": hint}
    else:
        exp = pick_expiry(und, today, db_path)
    if not exp: return {"outcome": "NO_CAPTURED_EXPIRY", "underlying": und}
    chk = CT.check(und, exp, s["strike"], s["option_type"], s["entry"], s["entry_mode"], sl=s["sl"], path=db_path)
    if chk["problems"] and scored: return {"outcome": "UNSCORABLE_NOT_SUBMITTED", "problems": chk["problems"], "parsed": s, "expiry": exp}
    if dry_run: return {"outcome": "WOULD_SUBMIT", "parsed": s, "expiry": exp, "practice": not scored, "warnings": chk["warnings"]}
    try:
        rec = SU.submit({"participant": participant, "underlying": und, "expiry": exp, "strike": s["strike"], "option_type": s["option_type"], "entry_mode": s["entry_mode"], "entry": s["entry"], "sl": s["sl"],
                         "targets": s["targets"], "note": f"via channel post {base['message_id']} ({s.get('horizon') or 'n/a'}; range {s['entry_range'][0]:g}-{s['entry_range'][1]:g}; expiry assumed = nearest captured{'; SL not published, arena default stop -30%' if default_sl else ''})"},
                        path=ledger, now=t, practice=not scored)
    except SU.SubmissionError as e: return {"outcome": "REJECTED", "reason": str(e), "parsed": s}
    state["by_msg"][base["key"]] = rec["id"]; state["last_by_chat"][base["chat_id"]] = rec["id"]
    return {"outcome": "SUBMITTED", "signal_id": rec["id"], "expiry": exp, "practice": not scored, "warnings": chk["warnings"]}


def poll(token: str, *, participant: str, mode_fn, call=None, ledger: str | None = None, log: str = LOG, state_path: str = STATE, db_path: str | None = None) -> list[dict]:
    """One getUpdates round (only channel posts of whitelisted channels). `call(method, **params)` is injectable for tests."""
    import urllib.parse, urllib.request
    call = call or (lambda m, **q: json.load(urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{m}?" + urllib.parse.urlencode(q), timeout=20)))
    st = _load(state_path, {}); st.setdefault("offset", 0); st.setdefault("seen", {}); st.setdefault("by_msg", {}); st.setdefault("last_by_chat", {})
    wl = channel_ids(); res = []
    ups = call("getUpdates", offset=st["offset"], timeout=0, limit=100, allowed_updates=json.dumps(["channel_post", "edited_channel_post", "my_chat_member"])).get("result", [])
    for u in ups:
        st["offset"] = max(st["offset"], u["update_id"] + 1)
        p = u.get("channel_post")
        if p and str(p["chat"]["id"]) in wl and p["chat"].get("type") == "channel": res.append(handle_post(p, st, participant=participant, mode_fn=mode_fn, ledger=ledger, log=log, db_path=db_path))
        elif u.get("edited_channel_post") and str(u["edited_channel_post"]["chat"]["id"]) in wl: _log({"outcome": "EDIT_IGNORED", "chat_id": str(u["edited_channel_post"]["chat"]["id"]), "message_id": u["edited_channel_post"].get("message_id")}, log)
    os.makedirs(os.path.dirname(state_path), exist_ok=True); json.dump(st, open(state_path, "w"))
    return res
