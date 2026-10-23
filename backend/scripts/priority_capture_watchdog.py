#!/usr/bin/env python3
"""Cron every 2 min: if a contract on the priority watch list (a live arena trade) has no stored quote for > MAX_AGE_SEC while its exchange is open, tell the owner on the PRIVATE chat (once per incident, and once on recovery). Read-only.
   python scripts/priority_capture_watchdog.py"""
import json, os, sqlite3, sys, time
from datetime import datetime, timezone
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
for l in open(os.path.join(HERE, ".env")):
    if "=" in l and not l.startswith("#"): k, v = l.strip().split("=", 1); os.environ.setdefault(k, v)
MAX_AGE_SEC = 90; MEMO = os.path.join(HERE, "data", "priority_watchdog_state.json"); WATCH = os.path.join(HERE, "data", "priority_watch.json")
EXCH = {"NATURALGAS": "MCX", "CRUDEOIL": "MCX", "GOLDM": "MCX", "NIFTY": "NSE", "BANKNIFTY": "NSE", "SENSEX": "BSE"}


def stale_contracts(ages: dict, max_age: float = MAX_AGE_SEC) -> list:
    """ages: {label: age_sec or None} -> labels that are stale (None = never stored = stale)."""
    return sorted(k for k, a in ages.items() if a is None or a > max_age)


def main() -> int:
    from app import market_calendar
    now = time.time()
    try: watch = [e for e in json.load(open(WATCH)) if e.get("until", 0) > now and e.get("kind") == "signal"]
    except Exception: watch = []
    c = sqlite3.connect("file:" + os.path.join(HERE, "data", "market_history.db") + "?mode=ro", uri=True, timeout=30)
    mx = c.execute("SELECT MAX(id) FROM quote_snapshots").fetchone()[0] or 0; ages = {}
    for e in watch:
        if not market_calendar.is_trading(EXCH.get(e["symbol"], "NSE")): continue
        r = c.execute("SELECT received_ts FROM quote_snapshots WHERE id>? AND symbol=? AND expiry=? AND strike=? AND option_type=? ORDER BY id DESC LIMIT 1", (mx - 600000, e["symbol"], e["expiry"], e["strike"], e["option_type"])).fetchone()
        ages[f"{e['symbol']} {e['expiry']} {e['strike']:g} {e['option_type']}"] = (now - datetime.fromisoformat(r[0].replace("Z", "+00:00")).timestamp()) if r else None
    bad = stale_contracts(ages)
    try: memo = json.load(open(MEMO))
    except Exception: memo = {"down": False}
    from app.connectors import telegram
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if bad and not memo["down"]:
        telegram._send("Arena price capture STALE for a live trade: " + "; ".join(f"{k} ({'never' if ages[k] is None else str(int(ages[k])) + ' s'})" for k in bad) + ". The 3-second capture is behind; check oi-dashboard.", chat); memo = {"down": True, "since": now}
    elif not bad and memo["down"]:
        telegram._send(f"Arena price capture recovered (was stale {int((now - memo.get('since', now)) / 60)} min).", chat); memo = {"down": False}
    json.dump(memo, open(MEMO, "w")); print("STALE: " + ", ".join(bad) if bad else f"ok ({len(ages)} watched contracts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
