#!/usr/bin/env python3
"""Dhan daily-login reminder. The Dhan access token lives 24 h and the OAuth login (mobile + MPIN) is HUMAN-ONLY: this script never logs in, never touches
credentials and never stores anything. If the stored token will not cover today's MCX session (ends 23:30 IST) it asks the Dhan consent endpoint for a fresh
login_url (the same call the dashboard button makes) and sends that link to YOUR private Telegram chat so the login is one tap.
Cron: 20 8,50 8 * * 1-5 style entries (see dhan_login_reminder_cron.sh). Usage: python scripts/dhan_login_reminder.py [--dry-run]"""
import base64
import datetime as dt
import json
import os
import sys

import requests

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
COVER_UNTIL = (23, 35)                       # the token must stay valid past the MCX close
BASE = os.environ.get("ZH_BASE", "http://127.0.0.1:7060")
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "ops", "dhan_login_reminder_state.json")


def needs_login(status: dict, now: dt.datetime) -> tuple[bool, str]:
    """(need, reason). Fails towards reminding: anything unknown means 'needs login'."""
    if not isinstance(status, dict) or status.get("token_state") != "VALID":
        return True, f"token state {status.get('token_state') if isinstance(status, dict) else 'unknown'}"
    exp = status.get("expiry_time")
    try:
        exp_dt = dt.datetime.fromisoformat(str(exp)).replace(tzinfo=IST) if dt.datetime.fromisoformat(str(exp)).tzinfo is None else dt.datetime.fromisoformat(str(exp)).astimezone(IST)
    except (TypeError, ValueError):
        return True, f"expiry unreadable ({exp})"
    need_until = now.astimezone(IST).replace(hour=COVER_UNTIL[0], minute=COVER_UNTIL[1], second=0, microsecond=0)
    if exp_dt < need_until:
        return True, f"token expires {exp_dt:%a %d-%b %H:%M} IST, before today's MCX close"
    return False, f"token valid until {exp_dt:%a %d-%b %H:%M} IST"


def build_message(reason: str, login_url: str | None) -> str:
    if login_url:
        return (f"Dhan login needed before 09:00 ({reason}).\nOpen this link and log in (mobile + MPIN) - one tap, it covers today's full session:\n{login_url}\n"
                f"No order is placed by this link; live trading stays disabled.")
    return f"Dhan login needed ({reason}) but a login link could not be created automatically - open the dashboard and use 'Connect Dhan'."


def _auth_header() -> dict:
    u, p = os.environ.get("CHANAKYA_ADMIN_USERNAME"), os.environ.get("CHANAKYA_ADMIN_PASSWORD")
    return {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()} if u and p else {}


def main(argv) -> int:
    dry = "--dry-run" in argv
    now = dt.datetime.now(IST)
    try:
        st = requests.get(f"{BASE}/api/dhan/connection-status", headers=_auth_header(), timeout=15).json()
    except Exception as e:
        st = {"token_state": f"status unavailable: {type(e).__name__}"}
    need, reason = needs_login(st, now)
    print(f"{now:%Y-%m-%d %H:%M} IST need_login={need} ({reason})")
    if not need:
        return 0
    key = f"{now:%Y-%m-%d %H}:{now.minute // 30}"               # at most one message per half hour
    try:
        last = json.load(open(STATE)).get("last")
    except Exception:
        last = None
    if last == key and not dry:
        print("already reminded in this half hour; skipping")
        return 0
    url = None
    if not dry:
        try:
            r = requests.post(f"{BASE}/api/dhan/generate-consent", headers=_auth_header(), timeout=20).json()
            url = r.get("login_url") if r.get("status") == "OK" else None
        except Exception as e:
            print("consent generation failed:", type(e).__name__)
    msg = build_message(reason, url)
    if dry:
        print("DRY RUN message:\n" + msg)
        return 0
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from app.connectors.telegram import _send
    res = _send(msg, os.environ.get("TELEGRAM_CHAT_ID"))
    print("telegram:", {k: v for k, v in res.items() if k != "message_id"})
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump({"last": key}, open(STATE, "w"))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
