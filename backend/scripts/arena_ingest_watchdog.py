#!/usr/bin/env python3
"""Cron every 5 min: tells the owner (private chat) when the Telegram reader is down/stale, and once when it is back. Read-only.  python scripts/arena_ingest_watchdog.py"""
import json, os, sys, time
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
for l in open(os.path.join(HERE, ".env")):
    if "=" in l and not l.startswith("#"): k, v = l.strip().split("=", 1); os.environ.setdefault(k, v)
STATUS = os.path.join(HERE, "data", "tg", "status.json"); MEMO = os.path.join(HERE, "data", "tg", "watchdog_state.json")
MAX_AGE_SEC = 180


def evaluate(status: dict | None, now: float) -> tuple[bool, str]:
    """-> (healthy, reason). Healthy = connected, status written within MAX_AGE_SEC and the 15 s poll loop ran within MAX_AGE_SEC."""
    if not status: return False, "no status file"
    if not status.get("connected"): return False, f"not connected (last_error {status.get('last_error')})"
    if now - float(status.get("updated") or 0) > MAX_AGE_SEC: return False, f"status stale {int(now - float(status.get('updated') or 0))} s"
    if now - float(status.get("last_poll") or 0) > MAX_AGE_SEC: return False, f"poll loop silent {int(now - float(status.get('last_poll') or 0))} s"
    return True, "ok"


def main() -> int:
    try: st = json.load(open(STATUS))
    except Exception: st = None
    ok, why = evaluate(st, time.time())
    try: memo = json.load(open(MEMO))
    except Exception: memo = {"down": False}
    from app.connectors import telegram
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not ok and not memo["down"]: telegram._send(f"Arena ingest watchdog: Telegram reader DOWN ({why}). Channel signals are NOT being read.", chat); memo = {"down": True, "since": time.time(), "why": why}
    elif ok and memo["down"]: telegram._send(f"Arena ingest watchdog: Telegram reader is back (down {int((time.time() - memo.get('since', time.time())) / 60)} min). Posts made meanwhile are caught up by the reader if under 30 min old; check the daily report for dropped ones.", chat); memo = {"down": False}
    json.dump(memo, open(MEMO, "w")); print("ok" if ok else f"DOWN: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
