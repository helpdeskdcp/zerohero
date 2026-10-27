"""Pull 1-minute Upstox v3 candles for the 53 large caps + NIFTY 50 index, month by month, resumable. Data -> data/historical/stocks_1m/stocks_1m.db (research only).
Optional args: symbols to pull instead of the default universe, e.g. `stocks_1m_pull.py BSE TMPV` (resolved from the NSE master, NSE_EQ/EQ only)."""
import os, sqlite3, sys, time
from datetime import date, timedelta
from urllib.parse import quote
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
from app.connectors import upstox as U
from app.scanner import stocks as S
DB = os.path.join(HERE, "data", "historical", "stocks_1m", "stocks_1m.db"); START, END = date(2024, 10, 1), date(2026, 10, 23)


def months():
    d = START
    while d <= END:
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1); yield d, min(nxt - timedelta(days=1), END); d = nxt


def main():
    c = sqlite3.connect(DB); c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS bars (sym TEXT, ts INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, PRIMARY KEY (sym, ts)) WITHOUT ROWID"); c.execute("CREATE TABLE IF NOT EXISTS done (sym TEXT, ym TEXT, n INTEGER, PRIMARY KEY (sym, ym))")
    keys = dict(S.keys()); keys["NIFTY"] = "NSE_INDEX|Nifty 50"; from datetime import datetime
    if len(sys.argv) > 1:                                               # explicit symbols (e.g. new index constituents not in the hardcoded universe)
        eq = {r["trading_symbol"]: r["instrument_key"] for r in S._master() if r.get("segment") == "NSE_EQ" and r.get("instrument_type") == "EQ"}
        want = [a.upper() for a in sys.argv[1:]]; miss = [w for w in want if w not in eq]
        if miss: sys.exit(f"not in the NSE master: {miss}")
        keys = {w: eq[w] for w in want}
    for sym, ik in keys.items():
        for a, b in months():
            ym = a.strftime("%Y-%m")
            if c.execute("SELECT 1 FROM done WHERE sym=? AND ym=?", (sym, ym)).fetchone(): continue
            for attempt in range(4):
                try:
                    r = U._authed_get(f"/v3/historical-candle/{quote(ik, safe='')}/minutes/1/{b.isoformat()}/{a.isoformat()}")
                    if r.status_code == 200: break
                    time.sleep(2 * (attempt + 1))
                except Exception: time.sleep(2 * (attempt + 1))
            else: print("FAIL", sym, ym, flush=True); continue
            cs = (r.json().get("data") or {}).get("candles", [])
            c.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)", [(sym, int(datetime.fromisoformat(k[0]).timestamp()), k[1], k[2], k[3], k[4], k[5] or 0) for k in cs])
            c.execute("INSERT OR REPLACE INTO done VALUES (?,?,?)", (sym, ym, len(cs))); c.commit(); time.sleep(0.25)
        print("done", sym, flush=True)


if __name__ == "__main__": main()
