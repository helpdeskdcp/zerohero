"""Evidence publisher. Destination: EVIDENCE_CHAT_ID, else TELEGRAM_SIGNALS_CHANNEL_ID, else the private TELEGRAM_CHAT_ID. Every post is also appended to a hash-chained local ledger
(data/evidence_feed/posts.jsonl) with the Telegram message_id, so a referee can compare the ledger with the chat history. EVIDENCE_FEED=0 disables."""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone

from ..forward_test import ledger as L

IST = timezone(timedelta(hours=5, minutes=30))
PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "evidence_feed", "posts.jsonl")
DISCLAIMER = "PAPER-TRADING evidence record of an automated research engine — not a recommendation, not investment advice."
_lock = threading.Lock(); _seen: set = set()


def enabled() -> bool:
    v = os.environ.get("EVIDENCE_FEED", "1").strip().lower()
    if os.environ.get("PYTEST_CURRENT_TEST") and v != "force": return False
    return v not in ("0", "false", "off", "no")


def chat_id() -> str:
    return (os.environ.get("EVIDENCE_CHAT_ID") or os.environ.get("TELEGRAM_SIGNALS_CHANNEL_ID") or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()


def fmt_time(ts: float) -> str: return datetime.fromtimestamp(ts, tz=IST).strftime("%Y-%m-%d %H:%M:%S IST")


def format_signal(*, label: str, rid: str, ts: float, underlying: str, strike, option_type: str, expiry: str, entry, sl, t1, t2=None, extra: str = "") -> str:
    t = f"T1 {t1}" + (f" · T2 {t2}" if t2 else "")
    return (f"🧾 <b>ZeroHero AI — PAPER signal</b> · {label}\nID: <code>{rid}</code> · {fmt_time(ts)}\n<b>{underlying} {strike:g} {option_type}</b> · expiry {expiry}\n"
            f"Entry (premium) {entry} · SL {sl} · {t}\n{extra}\n<i>{DISCLAIMER}</i>")


def _post(kind: str, rid: str, text: str, ts: float, sender=None, path: str | None = None) -> dict | None:
    with _lock:
        if (kind, rid) in _seen: return None
        _seen.add((kind, rid))
    if sender is None:
        from ..connectors import telegram
        sender = lambda t, c: telegram._send(t, c, evidence=True)
    cid = chat_id(); res = sender(text, cid) if cid else {"ok": False, "reason": "NO_CHAT_ID"}
    with _lock:
        return L.append(path or PATH, {"type": "EVIDENCE_POST", "kind": kind, "id": rid, "signal_ts": ts, "posted_ts": time.time(), "text": text, "chat": "evidence" if os.environ.get("EVIDENCE_CHAT_ID") else "signals_or_private",
                                       "telegram": {k: res.get(k) for k in ("ok", "message_id", "status_code", "reason")}})


def post_async(kind: str, rid: str, text: str, ts: float, **kw) -> None:
    """Fire-and-forget in a daemon thread (the runner must never wait on the network). Never raises."""
    try:
        if not enabled(): return
        threading.Thread(target=lambda: _safe(kind, rid, text, ts, **kw), daemon=True).start()
    except Exception:
        return


def _safe(kind, rid, text, ts, **kw):
    try: _post(kind, rid, text, ts, **kw)
    except Exception: return


def publish_open_trade(sym: str, sig: dict, row: dict, signal_id: str) -> None:
    """Frozen AUTOSCALP entry (called from runner._open_paper after the paper trade is opened)."""
    try:
        if not enabled(): return
        ts = time.time()
        txt = format_signal(label="engine AUTOSCALP (frozen entrant)", rid=str(row.get("trade_id") or signal_id), ts=ts, underlying=sym.upper(), strike=float(sig.get("strike") or 0), option_type=sig["decision"].split("_")[1],
                            expiry=str(sig.get("expiry") or "—"), entry=sig["entry"], sl=sig["stop_loss"], t1=sig["target_1"], t2=sig.get("target_2"),
                            extra=f"regime {sig.get('regime')} · {sig.get('signal_type')} · confidence {sig.get('confidence')}")
        post_async("AUTOSCALP", str(row.get("trade_id") or signal_id), txt, ts)
    except Exception:
        return


def publish_arm_signal(rec: dict) -> None:
    """ACTIVE arm record (called by active_arm.shadow.record after the row is stored)."""
    try:
        if not enabled(): return
        grp = "ACTIVE-ONLY (frozen engine did not take it)" if rec.get("frozen_blocked") else "COMMON (frozen engine also took it)"
        txt = format_signal(label=f"EXPLORATORY ARM (filters removed) · {grp}", rid=f"ARM-{rec['id']}", ts=rec["ts"], underlying=rec["symbol"], strike=float(rec.get("strike") or 0), option_type=rec["decision"].split("_")[1],
                            expiry=str(rec.get("expiry") or "—"), entry=rec["entry"], sl=rec["stop_loss"], t1=rec["target_1"], t2=rec.get("target_2"), extra=f"regime {rec.get('regime')} · {rec.get('signal_type')} · confidence {rec.get('confidence')}")
        post_async("ARM", f"ARM-{rec['id']}", txt, rec["ts"])
    except Exception:
        return
