#!/usr/bin/env python3
"""READ-ONLY pull of NIFTY weekly option 1-minute bars (close, volume, OI) for the last 5 sessions of every weekly expiry, ATM +/-300 points per session open, for the option-flow study
(data/research/option_flow/SPEC_2026-10-24.md). Resumable (an expiry is marked done only after all its contracts are stored). Writes data/historical/option_flow/option_flow.db.
   python scripts/option_flow_pull.py [--limit N] [--from 2024-10-03] [--to 2026-10-20]"""
import argparse, os, sqlite3, sys, time
from datetime import datetime, timedelta, timezone
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
for l in open(os.path.join(HERE, ".env")):
    if "=" in l and not l.startswith("#"): k, v = l.strip().split("=", 1); os.environ.setdefault(k, v)
from app.connectors import upstox as U

CFG = {"NIFTY": {"key": "NSE_INDEX|Nifty 50", "symbol": "NIFTY", "step": 50, "db": "option_flow.db", "default_from": "2024-10-03", "default_to": "2026-10-20"},
       "SENSEX": {"key": "BSE_INDEX|SENSEX", "symbol": "SENSEX", "step": 100, "db": "option_flow_sensex.db", "default_from": "2024-10-04", "default_to": "2026-10-22"}}
UND = "SENSEX" if "--underlying" in sys.argv and sys.argv[sys.argv.index("--underlying") + 1].upper() == "SENSEX" else "NIFTY"; C = CFG[UND]
DB = os.path.join(HERE, "data", "historical", "option_flow", C["db"]); MH = os.path.join(HERE, "data", "market_history.db"); KEY = C["key"]; STEP = C["step"]; IST = timezone(timedelta(hours=5, minutes=30))
SCHEMA = """CREATE TABLE IF NOT EXISTS bars (expiry TEXT NOT NULL, strike REAL NOT NULL, otype TEXT NOT NULL, ts INTEGER NOT NULL, c REAL, v REAL, oi REAL, PRIMARY KEY (expiry, strike, otype, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS done (expiry TEXT PRIMARY KEY, n_contracts INTEGER, n_rows INTEGER, days TEXT, finished REAL);"""


def sessions():
    m = sqlite3.connect(f"file:{MH}?mode=ro", uri=True, timeout=30)
    try: rows = m.execute("SELECT session_date_ist, MIN(bar_start) FROM market_candles WHERE symbol=? AND kind='INDEX' AND tf='1m' AND session_date_ist>='2024-09-01' GROUP BY session_date_ist ORDER BY 1", (C["symbol"],)).fetchall()
    finally: m.close()
    out = {}
    for d, bs in rows:
        m2 = sqlite3.connect(f"file:{MH}?mode=ro", uri=True, timeout=30)
        try: o = m2.execute("SELECT o FROM market_candles WHERE symbol=? AND kind='INDEX' AND tf='1m' AND bar_start=?", (C["symbol"], bs)).fetchone()[0]
        finally: m2.close()
        out[d] = float(o)
    return out


def call(fn, *a, tries=4):
    for i in range(tries):
        try:
            r = fn(*a)
            if r["http_status"] == 200: return r
            if r["http_status"] in (429, 500, 502, 503): time.sleep(2 * (i + 1)); continue
            return r
        except Exception: time.sleep(2 * (i + 1))
    return {"http_status": 0, "json": None}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=0); ap.add_argument("--from", dest="frm", default=C["default_from"]); ap.add_argument("--to", default=C["default_to"]); ap.add_argument("--underlying", default="NIFTY"); a = ap.parse_args()
    sess = sessions(); days_all = sorted(sess); c = sqlite3.connect(DB, timeout=30); c.executescript(SCHEMA); c.execute("PRAGMA journal_mode=WAL")
    ex = call(U.get_expiries, KEY)["json"]["data"]; ex = sorted(e for e in ex if a.frm <= e <= a.to); done = {r[0] for r in c.execute("SELECT expiry FROM done")}; todo = [e for e in ex if e not in done]
    if a.limit: todo = todo[:a.limit]
    print(f"{len(ex)} expiries in range, {len(done)} done, {len(todo)} to do", flush=True)
    for e in todo:
        t0 = time.time(); upto = [d for d in days_all if d <= e][-5:]
        if len(upto) < 3: print(e, "skip: <3 index sessions", flush=True); continue
        strikes = set()
        for d in upto:
            atm = round(sess[d] / STEP) * STEP
            strikes |= {atm + STEP * k for k in range(-6, 7)}
        cons = call(U.get_expired_option_contracts, KEY, e)["json"]; cons = [x for x in (cons or {}).get("data", []) if float(x["strike_price"]) in strikes and x["instrument_type"] in ("CE", "PE")]
        rows = 0; first, last = upto[0], upto[-1]
        for x in cons:
            r = call(U.get_expired_historical_candles, x["instrument_key"], "1minute", last, first); cs = ((r["json"] or {}).get("data") or {}).get("candles") or []; batch = []
            for k in cs:
                ts = datetime.fromisoformat(k[0]).astimezone(timezone.utc); d = ts.astimezone(IST).strftime("%Y-%m-%d")
                if d in upto: batch.append((e, float(x["strike_price"]), x["instrument_type"], int(ts.timestamp()), k[4], k[5], k[6]))
            c.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)", batch); c.commit(); rows += len(batch); time.sleep(0.25)
        c.execute("INSERT OR REPLACE INTO done VALUES (?,?,?,?,?)", (e, len(cons), rows, ",".join(upto), time.time())); c.commit()
        print(f"{e}: {len(cons)} contracts, {rows} rows, {round(time.time() - t0)}s", flush=True)


if __name__ == "__main__":
    main()
