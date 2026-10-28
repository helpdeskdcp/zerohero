"""Consent-based ingestion of the institution's own Telegram channel posts into the arena ledger (route A: our bot is an ADMIN of the channel; the owner agreed).
Only channels whitelisted in ARENA_CHANNEL_IDS are read. Each post is stamped with OUR receive time (never earlier than the post, so no look-ahead); the Telegram post time is logged beside it.
A post that cannot be parsed, is unsupported, or names an unscorable contract is LOGGED and NOT submitted (nothing is guessed). Channel result claims ("target hit", "points achieved") are stored as claims only and never scored:
the arena settles from market data. 'Trade cancelled' becomes a CANCEL event on the signal the post replies to (else the latest signal from that channel). Idempotent by (chat_id, message_id)."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

from . import channel_parse as CP
from . import contracts as CT
from . import submit as SU

IST = timezone(timedelta(hours=5, minutes=30))
DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "arena")
STATE = os.path.join(DIR, "channel_ingest_state.json")
LOG = os.path.join(DIR, "channel_ingest_log.jsonl")
EXCH = {"NIFTY": "NSE", "BANKNIFTY": "NSE", "SENSEX": "BSE", "NATURALGAS": "MCX", "CRUDEOIL": "MCX", "GOLDM": "MCX"}


def channel_ids() -> set[str]:
    return {x.strip() for x in (os.environ.get("ARENA_CHANNEL_IDS") or "").split(",") if x.strip()}


def _load(p, default):
    try: return json.load(open(p))
    except Exception: return default


_SAVE_LOCK = threading.Lock()


def save_json(path: str, obj) -> None:
    """Atomic JSON write: dump to a temp file in the SAME directory, then os.replace over the target. A crash or kill at any instant leaves either the complete old file or the complete new one, never the
    truncated file that `open(path, "w")` + dump leaves behind (the dedupe record `seen` lives in this state file: a truncated one would let an already submitted post be submitted again).
    Set TG_STATE_FSYNC=1 to also fsync before the rename (survives power loss, but an fsync on this shared disk can take 100+ ms inside the event loop, so it is off by default)."""
    d = os.path.dirname(os.path.abspath(path)); os.makedirs(d, exist_ok=True)
    with _SAVE_LOCK:
        fd, tmp = tempfile.mkstemp(dir=d, prefix=os.path.basename(path) + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(obj, f)
                if os.environ.get("TG_STATE_FSYNC") == "1": f.flush(); os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try: os.unlink(tmp)
            except OSError: pass
            raise


def _log(rec: dict, log: str):
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "a") as f: f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def pick_expiry(underlying: str, today_iso: str, path: str | None = None) -> str | None:
    ex = sorted(e for e in CT.captured_fresh(underlying, None, path)["expiries"] if e >= today_iso)
    return ex[0] if ex else None


def _default_lots(participant: str) -> int:
    try:
        from . import api
        return int(api.cfg()["flags"].get(str(participant).upper(), {}).get("default_lots", 1))
    except Exception:
        return 1


def _market_open(und: str) -> bool:
    try:
        from .. import market_calendar as mc
        return bool(mc.is_trading(EXCH[und]))
    except Exception:
        return False


RETRYABLE = {"NO_CAPTURED_EXPIRY", "NO_CAPTURED_EXPIRY_FOR_POST", "UNSCORABLE_NOT_SUBMITTED"}          # data not there YET (capture lag, expiry roll): retried until the post is STALE_SEC old
DROPPED = RETRYABLE | {"STALE_NOT_SUBMITTED", "REJECTED"}                                                    # a signal-like post that never reached the ledger: always alerted + listed in the daily report


def handle_post(post: dict, state: dict, *, participant: str, mode_fn, ledger: str | None = None, log: str = LOG, db_path: str | None = None, now: float | None = None, dry_run: bool = False,
                use_post_time: bool = False, retry: bool = False, alert=None) -> dict:
    """One channel_post -> outcome dict (also appended to the log). Mutates `state['seen']`.
    use_post_time: the ledger stamp is Telegram's own time of the post (the instant the signal became public), never later than receipt, so a late reader cannot penalise the poster;
    a post read more than STALE_SEC after it was posted is logged and NOT submitted.
    A RETRYABLE outcome (the contract data is not captured yet) is not final: the post waits in state['pending'] and is retried (retry=True) until it is STALE_SEC old; only the first attempt and the
    final outcome are logged. `alert(text)` is called when a signal-like post ends up dropped, and once when a retried post is finally submitted."""
    chat = str(post["chat"]["id"]); mid = post.get("message_id"); key = f"{chat}:{mid}"
    t_recv = now or time.time(); text = post.get("text") or post.get("caption") or ""
    t = min(float(post["date"]), t_recv) if (use_post_time and post.get("date")) else t_recv
    stale = bool(use_post_time and t_recv - t > STALE_SEC)
    base = {"key": key, "chat_id": chat, "message_id": mid, "tg_date": datetime.fromtimestamp(post.get("date", t), tz=timezone.utc).isoformat(), "received_iso": datetime.fromtimestamp(t_recv, tz=timezone.utc).isoformat(),
            "latency_sec": round(t_recv - t, 1), "text": text[:1500]}
    if key in state["seen"]: return {"outcome": "DUPLICATE", **base}
    pend = state.setdefault("pending", {}); out = _decide(post, state, base, text, t, participant, mode_fn, ledger, db_path, dry_run, stale)
    if out["outcome"] in RETRYABLE and use_post_time and not dry_run and not stale:
        first = key not in pend; rec = pend.setdefault(key, {"post": post, "participant": participant, "tries": 0, "first_outcome": out["outcome"]}); rec["tries"] += 1; rec["last_outcome"] = out["outcome"]
        if first: _log({**base, **out, "retry_scheduled": True}, log)
        return {**base, **out, "retry_scheduled": True}
    was_pending = pend.pop(key, None)
    state["seen"][key] = out.get("signal_id") or out["outcome"]
    _log({**base, **out, **({"after_retries": was_pending["tries"]} if was_pending else {})}, log)
    if alert and not dry_run:
        try:
            if out["outcome"] in DROPPED:
                alert(f"DROPPED channel post ({out['outcome']}): {text[:200]!r}\n{out.get('reason') or out.get('hint') or out.get('problems') or ''}\nchat {chat} msg {mid}. It is NOT in the ledger.")
            elif was_pending and out["outcome"] == "SUBMITTED":
                alert(f"Recovered: channel post {chat}:{mid} was submitted after {was_pending['tries']} retries ({was_pending['first_outcome']}); stamped at its Telegram post time.")
        except Exception:
            pass
    return {**base, **out}


FILL_WINDOW_SEC = 900                               # a channel entry (range / limit / above) that is not filled within 15 minutes of the post expires
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
    und = s["underlying"]
    if mode_fn() == "ENDED": return {"outcome": "ARENA_ENDED"}
    scored = mode_fn() == "ARENA_LIVE"
    if scored and not _market_open(und): return {"outcome": "MARKET_CLOSED_NOT_SUBMITTED", "underlying": und}
    today = datetime.fromtimestamp(t, tz=IST).date().isoformat()
    hint = s.get("expiry_hint")
    if hint:                                                                                  # the post names day + month: use ONLY a captured expiry with exactly that day and month on/after the post date
        cands = [e for e in sorted(CT.captured_fresh(und, None, db_path)["expiries"]) if e >= today and int(e[5:7]) == hint["month"] and (hint.get("day") is None or int(e[8:10]) == hint["day"])]       # month-only posts ("EXPIRY - OCT"): the captured expiry of that month
        exp = cands[0] if cands else None
        if not exp: return {"outcome": "NO_CAPTURED_EXPIRY_FOR_POST", "underlying": und, "hint": hint}
    else:
        exp = pick_expiry(und, today, db_path)
    if not exp: return {"outcome": "NO_CAPTURED_EXPIRY", "underlying": und}
    risk_module = s.get("sl") is None or not s.get("targets")
    if risk_module:                                                                           # entry-only channel: ZeroHero's rule-based risk module sets the stop and the 1R/2R/3R ladder (spec addendum in data/research/sl_detector)
        from .. import sl_detector
        try: ltps = sl_detector.prior_ltps(und, exp, s["strike"], s["option_type"], t, db_path)
        except Exception: ltps = []
        if s.get("sl") is None: est = sl_detector.estimate(s["entry"], ltps); s["sl"] = est["sl"]
        if not s.get("targets"):
            r = s["entry"] - s["sl"]; s["targets"] = [round(round((s["entry"] + k * r) / 0.05) * 0.05, 2) for k in (1, 2, 3)]
    chk = CT.check(und, exp, s["strike"], s["option_type"], s["entry"], s["entry_mode"], sl=s["sl"], path=db_path)
    if chk["problems"] and scored: return {"outcome": "UNSCORABLE_NOT_SUBMITTED", "problems": chk["problems"], "parsed": s, "expiry": exp}
    if dry_run: return {"outcome": "WOULD_SUBMIT", "parsed": s, "expiry": exp, "practice": not scored, "warnings": chk["warnings"]}
    try:
        rec = SU.submit({"participant": participant, "underlying": und, "expiry": exp, "strike": s["strike"], "option_type": s["option_type"], "entry_mode": s["entry_mode"], "entry": s["entry"], "sl": s["sl"],
                         "targets": s["targets"], **({"entry_hi": s["entry_range"][1]} if (s["entry_mode"] == "ABOVE" and s["entry_range"][1] > s["entry"]) else {}), "valid_sec": FILL_WINDOW_SEC, "lots": _default_lots(participant), "note": f"via channel post {base['message_id']} ({s.get('horizon') or 'n/a'}; range {s['entry_range'][0]:g}-{s['entry_range'][1]:g}; expiry assumed = nearest captured{'; stop/targets set by the ZeroHero risk module (1R/2R/3R)' if risk_module else ''})"},
                        path=ledger, now=t, practice=not scored)
    except SU.SubmissionError as e: return {"outcome": "REJECTED", "reason": str(e), "parsed": s}
    state["by_msg"][base["key"]] = rec["id"]; state["last_by_chat"][base["chat_id"]] = rec["id"]
    try:                                                                                      # dense (~3 s) quotes for this contract until the exchange closes, so its fill/exit can be resolved precisely
        from ..histcap import priority
        priority.request_watch(und, exp, s["strike"], s["option_type"])
    except Exception:
        pass
    if risk_module and not s.get("sl_published", True):                                          # experiment only (data/research/sl_detector/SPEC_2026-10-21.md): never affects the score, never blocks ingest
        try:
            from .. import sl_detector
            sl_detector.record_prediction(rec["id"], und, exp, s["strike"], s["option_type"], s["entry"], t)
        except Exception:
            pass
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
    save_json(state_path, st)
    return res
