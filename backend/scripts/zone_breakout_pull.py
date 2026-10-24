#!/usr/bin/env python3
"""READ-ONLY pull of 1-minute index candles for the indices ZeroHero does not have long history for (FINNIFTY, MIDCPNIFTY, BANKEX), month by month, from the Upstox historical API,
into data/historical/zone_breakout/index_1m.db (spec: data/research/zone_breakout/SPEC_2026-10-25.md).   python scripts/zone_breakout_pull.py [--from 2024-10-01]"""
import os, sqlite3, sys, time
from datetime import date, datetime, timedelta, timezone
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
for l in open(os.path.join(HERE, ".env")):
    if "=" in l and not l.startswith("#"): k, v = l.strip().split("=", 1); os.environ.setdefault(k, v)
from app.connectors import upstox as U

KEYS = {"FINNIFTY": "NSE_INDEX|Nifty Fin Service", "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT", "BANKEX": "BSE_INDEX|BANKEX"}
DB = os.path.join(HERE, "data", "historical", "zone_breakout", "index_1m.db")
frm = date.fromisoformat(sys.argv[sys.argv.index("--from") + 1]) if "--from" in sys.argv else date(2024, 10, 1)
c = sqlite3.connect(DB, timeout=30); c.execute("CREATE TABLE IF NOT EXISTS bars (symbol TEXT NOT NULL, ts INTEGER NOT NULL, o REAL, h REAL, l REAL, c REAL, PRIMARY KEY (symbol, ts)) WITHOUT ROWID"); c.execute("PRAGMA journal_mode=WAL")
today = date.today()
for sym, key in KEYS.items():
    d = frm; n = 0
    while d <= today:
        e = min((d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1), today)
        for attempt in range(4):
            try:
                r = U.get_historical_candles(key, "1minute", e.isoformat(), d.isoformat())
                if r["http_status"] == 200: break
            except Exception: pass
            time.sleep(2 * (attempt + 1))
        cs = ((r["json"] or {}).get("data") or {}).get("candles") or []
        rows = [(sym, int(datetime.fromisoformat(k[0]).astimezone(timezone.utc).timestamp()), k[1], k[2], k[3], k[4]) for k in cs]
        c.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?)", rows); c.commit(); n += len(rows); time.sleep(0.3); d = e + timedelta(days=1)
    print(sym, n, "rows", flush=True)
