"""Consent-based ingestion of the institution's own Telegram channel posts into the arena ledger (route A: our bot is an ADMIN of the channel; the owner agreed).
Only channels whitelisted in ARENA_CHANNEL_IDS are read. Each post is stamped with OUR receive time (never earlier than the post, so no look-ahead); the Telegram post time is logged beside it.
A post that cannot be parsed, is unsupported, or names an unscorable contract is LOGGED and NOT submitted (nothing is guessed). Channel result claims ("target hit", "points achieved") are stored as claims only and never scored:
the arena settles from market data. 'Trade cancelled' becomes a CANCEL event on the signal the post replies to (else the latest signal from that channel). Idempotent by (chat_id, message_id)."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from . import channel_parse as CP
from . import contracts as CT
from . import submit as SU
from .jsonio import read_json, write_json

from .markets import EXCH, IST  # noqa: E402,F401
DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "arena")
STATE = os.path.join(DIR, "channel_ingest_state.json")
LOG = os.path.join(DIR, "channel_ingest_log.jsonl")


def channel_ids() -> set[str]:
    return {x.strip() for x in (os.environ.get("ARENA_CHANNEL_IDS") or "").split(",") if x.strip()}


def _load(p, default):
    return read_json(p, default)


def save_json(path: str, obj) -> None:
    """Atomic JSON write: dump to a temp file in the SAME directory, then os.replace over the target. A crash or kill at any instant leaves either the complete old file or the complete new one, never the
    truncated file that `open(path, "w")` + dump leaves behind (the dedupe record `seen` lives in this state file: a truncated one would let an already submitted post be submitted again).
    Set TG_STATE_FSYNC=1 to also fsync before the rename (survives power loss, but an fsync on this shared disk can take 100+ ms inside the event loop, so it is off by default)."""
    write_json(path, obj, fsync=os.environ.get("TG_STATE_FSYNC") == "1")


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
    chat = str(post["chat"]["id"]); mid = post.get("message_id"); key = post.get("_key") or f"{chat}:{mid}"                  # _key: a post that first became a signal through an EDIT is keyed per edit version
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
    if post.get("_alias"): state["by_msg"][post["_alias"]] = rec["id"]                      # later edits / a deletion of the ORIGINAL post id find this signal
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


EDITS_KEEP = 5000


def _levels(recs: list, sig: dict) -> dict:
    """The entry / stop / targets in force for a signal: the submitted values with every earlier management event applied in order."""
    cur = {"entry": sig.get("entry"), "sl": sig.get("sl"), "targets": sorted(sig.get("targets") or [])}
    for r in recs:
        if r.get("type") != "HUMAN_EVENT" or r.get("signal_id") != sig["id"]: continue
        a = r.get("action")
        if a == "AMEND_SL" and r.get("sl") is not None: cur["sl"] = float(r["sl"])
        elif a == "AMEND_TARGETS" and r.get("targets"): cur["targets"] = sorted(r["targets"])
        elif a == "AMEND_ENTRY" and r.get("entry") is not None: cur["entry"] = float(r["entry"])
    return cur


def handle_edit(post: dict, state: dict, *, participant: str, mode_fn, ledger: str | None = None, log: str = LOG, db_path: str | None = None, now: float | None = None, dry_run: bool = False, alert=None) -> dict:
    """An EDITED channel post (post["edit_date"] = when the edit was made). Rules, all prospective (an edit can never rewrite what the market already did):
      * the original became a ledger signal and the edit names the SAME contract -> the changed stop / targets / entry become AMEND_SL / AMEND_TARGETS / AMEND_ENTRY events stamped at the EDIT time
        (settlement applies them after the usual execution delay; an entry change is ignored once the order has filled, exactly like a participant's own amendment);
      * the edit names a DIFFERENT contract -> nothing is applied (a different trade must be a new post) and the owner is alerted;
      * the original never became a signal (not a signal, dropped, still waiting for data) and the edit parses as one -> it is a NEW signal stamped at the EDIT time, never backdated to the original post;
      * an edit read more than STALE_SEC after it was made is logged and not applied."""
    chat = str(post["chat"]["id"]); mid = post.get("message_id"); key = f"{chat}:{mid}"; t_recv = now or time.time()
    ed = float(post.get("edit_date") or t_recv); t_edit = min(ed, t_recv); ekey = f"{key}:e{int(ed)}"; text = post.get("text") or ""
    base = {"key": ekey, "chat_id": chat, "message_id": mid, "edited": True, "edit_iso": datetime.fromtimestamp(t_edit, tz=timezone.utc).isoformat(), "latency_sec": round(t_recv - t_edit, 1), "text": text[:1500]}
    seen = state.setdefault("edits_seen", {})
    if ekey in seen: return {"outcome": "DUPLICATE", **base}
    out = _decide_edit(post, state, base, key, ekey, text, t_edit, t_recv, participant, mode_fn, ledger, db_path, dry_run, alert)
    if not out.get("retry_scheduled"):
        seen[ekey] = out["outcome"]
        while len(seen) > EDITS_KEEP: seen.pop(next(iter(seen)))
    _log({**base, **out}, log)
    if alert and not dry_run and out["outcome"] in ("EDIT_CONTRACT_CHANGED", "EDIT_PARTIAL", "EDIT_REJECTED"):
        try: alert(f"Edited channel post {chat}:{mid} NOT fully applied ({out['outcome']}): {out.get('reason') or out.get('rejected') or ''}. The original signal {out.get('signal_id') or ''} stays as first posted; a different trade needs a new post.")
        except Exception: pass
    return {**base, **out}


def _decide_edit(post, state, base, key, ekey, text, t_edit, t_recv, participant, mode_fn, ledger, db_path, dry_run, alert) -> dict:
    r = CP.parse_signal(text)
    if not r["ok"]: return {"outcome": "EDIT_NOT_A_SIGNAL", "reason": r["reason"]}
    s = r["signal"]
    if not s["supported"]: return {"outcome": "UNSUPPORTED_INSTRUMENT", "underlying": s["underlying"]}
    if t_recv - t_edit > STALE_SEC: return {"outcome": "EDIT_STALE_NOT_APPLIED", "underlying": s["underlying"]}
    sid = state.setdefault("by_msg", {}).get(key)
    if not sid:                                                                                # the original is not in the ledger: this edit is a new signal, stamped when the edit was made
        state.setdefault("pending", {}).pop(key, None)
        p2 = {**{k: v for k, v in post.items() if k != "edit_date"}, "date": int(t_edit), "_key": ekey, "_alias": key}
        out = handle_post(p2, state, participant=participant, mode_fn=mode_fn, ledger=ledger, log=os.devnull, db_path=db_path, now=t_recv, dry_run=dry_run, use_post_time=True, alert=alert)
        return {**{k: v for k, v in out.items() if k not in base}, "from_edit": True}
    recs = SU.load(ledger); sig = next((x for x in recs if x.get("type", "HUMAN_SIGNAL") == "HUMAN_SIGNAL" and x["id"] == sid), None)
    if sig is None: return {"outcome": "EDIT_NO_TARGET", "signal_id": sid}
    hint = s.get("expiry_hint"); exp = str(sig.get("expiry") or "")
    same = (s["underlying"] == sig.get("underlying") and float(s["strike"]) == float(sig.get("strike")) and s["option_type"] == sig.get("option_type")
            and not (hint and exp and (int(exp[5:7]) != hint["month"] or (hint.get("day") is not None and int(exp[8:10]) != hint["day"]))))
    if not same: return {"outcome": "EDIT_CONTRACT_CHANGED", "signal_id": sid, "reason": f"edit names {s['underlying']} {s['strike']:g} {s['option_type']}, the signal is {sig.get('underlying')} {float(sig.get('strike')):g} {sig.get('option_type')}"}
    cur = _levels(recs, sig); evs = []
    if s.get("sl") is not None and abs(float(s["sl"]) - float(cur["sl"] or 0)) > 1e-9: evs.append({"action": "AMEND_SL", "sl": s["sl"]})
    if s.get("targets") and sorted(float(x) for x in s["targets"]) != [float(x) for x in cur["targets"]]: evs.append({"action": "AMEND_TARGETS", "targets": s["targets"]})
    if abs(float(s["entry"]) - float(cur["entry"] or 0)) > 1e-9:
        hi = s["entry_range"][1] if (s["entry_mode"] == "ABOVE" and s["entry_range"][1] > s["entry"]) else None
        evs.append({"action": "AMEND_ENTRY", "entry": s["entry"], **({"entry_hi": hi} if hi else {})})
    if not evs: return {"outcome": "EDIT_NO_CHANGE", "signal_id": sid}
    if dry_run: return {"outcome": "WOULD_AMEND", "signal_id": sid, "actions": [e["action"] for e in evs]}
    t = max(t_edit, float(sig.get("server_ts") or 0)); ok, bad = [], []
    for e in evs:
        try: ok.append(SU.submit_event({**e, "signal_id": sid, "note": "channel: post edited"}, participant, path=ledger, now=t)["id"])
        except SU.SubmissionError as ex: bad.append(f"{e['action']}: {ex}")
    if bad: return {"outcome": "EDIT_PARTIAL" if ok else "EDIT_REJECTED", "signal_id": sid, "event_ids": ok, "rejected": bad}
    return {"outcome": "EDIT_APPLIED", "signal_id": sid, "event_ids": ok, "actions": [e["action"] for e in evs]}


def handle_delete(chat, message_id, state: dict, *, participant: str, ledger: str | None = None, log: str = LOG, now: float | None = None, dry_run: bool = False, alert=None) -> dict:
    """A channel post was deleted. A signal still waiting for data is dropped and never submitted. A submitted signal gets a CANCEL event at the detection time: it cancels an order that has not filled yet and has NO effect
    on a filled trade (settlement only honours CANCEL before the fill), so deleting a losing post can never erase the trade. Idempotent."""
    key = f"{chat}:{message_id}"; t = now or time.time(); base = {"key": key, "chat_id": str(chat), "message_id": message_id, "deleted": True, "detected_iso": datetime.fromtimestamp(t, tz=timezone.utc).isoformat()}
    pend = state.setdefault("pending", {})
    if key in pend:
        pend.pop(key, None); state.setdefault("seen", {})[key] = "DELETED_BEFORE_SUBMIT"; out = {"outcome": "DELETED_BEFORE_SUBMIT"}
    else:
        sid = state.setdefault("by_msg", {}).get(key)
        if not sid: out = {"outcome": "DELETE_NO_SIGNAL"}
        elif dry_run: out = {"outcome": "WOULD_CANCEL_ON_DELETE", "signal_id": sid}
        else:
            try:
                ev = SU.submit_event({"signal_id": sid, "action": "CANCEL", "note": "channel: post deleted"}, participant, path=ledger, now=t); out = {"outcome": "DELETE_CANCEL_SUBMITTED", "signal_id": sid, "event_id": ev["id"]}
            except SU.SubmissionError as e: out = {"outcome": "DELETE_CANCEL_REJECTED", "signal_id": sid, "reason": str(e)}
            if alert:
                try: alert(f"Channel post {chat}:{message_id} (signal {sid}) was DELETED; recorded as a cancel. It cancels the order only if it has not filled; a filled trade is unaffected and keeps being scored.")
                except Exception: pass
    _log({**base, **out}, log)
    return {**base, **out}


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
