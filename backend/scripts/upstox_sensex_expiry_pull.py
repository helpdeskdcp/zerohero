#!/usr/bin/env python3
"""Bounded, resumable, READ-ONLY pull of SENSEX expired-option 5m bars for the days E-3..E of each expiry (spec addendum in data/research/index_event/SPEC_OPTION_LEVEL_2026-10-21.md).
Writes data/historical/upstox/upstox_sensex.db (same table as the NIFTY research DB). No orders. Usage: python scripts/upstox_sensex_expiry_pull.py [--from 2025-08-22] [--steps 8]"""
import argparse, datetime as dt, importlib.util, os, sqlite3, sys, time
sys.path.insert(0, ".")
spec = importlib.util.spec_from_file_location("imp", "scripts/upstox_expired_options_import.py"); I = importlib.util.module_from_spec(spec); spec.loader.exec_module(I); U = I.U
ap = argparse.ArgumentParser(); ap.add_argument("--from", dest="dfrom", default="2025-08-22"); ap.add_argument("--steps", type=int, default=8); ap.add_argument("--min-have", type=int, default=30); a = ap.parse_args()
OUT = "data/historical/upstox/upstox_sensex.db"
schema = sqlite3.connect("file:data/historical/upstox/upstox_research.db?mode=ro", uri=True).execute("SELECT sql FROM sqlite_master WHERE name='normalized_option_bars'").fetchone()[0]
con = sqlite3.connect(OUT, timeout=60); con.execute(schema.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1)); con.execute("CREATE INDEX IF NOT EXISTS ix_nob ON normalized_option_bars(underlying, expiry, strike, option_type, timestamp)")
mh = sqlite3.connect("file:data/market_history.db?mode=ro", uri=True, timeout=60)
def spot_before(day):
    r = mh.execute("SELECT c FROM market_candles WHERE symbol='SENSEX' AND kind='INDEX' AND tf='1m' AND bar_start<? ORDER BY bar_start DESC LIMIT 1", (day + "T00:00:00Z",)).fetchone()
    return float(r[0]) if r else None
ik = "BSE_INDEX|SENSEX"
exps = [e for e in sorted(U.get_expiries(ik)["json"]["data"]) if a.dfrom <= e <= "2026-10-15"]
print(f"expiries {len(exps)} ({exps[0]}..{exps[-1]})", flush=True); tot = new = err = 0
for e in exps:
    have = con.execute("SELECT COUNT(DISTINCT instrument_key) FROM normalized_option_bars WHERE underlying='SENSEX' AND expiry=?", (e,)).fetchone()[0]
    if have >= a.min_have: print(f"{e}: have {have}, skip", flush=True); continue
    time.sleep(1.1); cr = U.get_expired_option_contracts(ik, e); contracts = (cr.get("json") or {}).get("data") or []
    spot = spot_before(e)                                                      # previous known close: information available BEFORE the expiry day
    if spot is None: print(f"{e}: no spot -> skip", flush=True); continue
    contracts, atm, step = I._atm_filter(contracts, spot, a.steps)
    frm = (dt.date.fromisoformat(e) - dt.timedelta(days=3)).isoformat(); got = 0
    for ct in contracts:
        eik = ct.get("expired_instrument_key") or ct.get("instrument_key"); hr = None
        for attempt in range(5):
            time.sleep(1.1)
            try: hr = U.get_expired_historical_candles(eik, "5minute", e, frm)
            except Exception: hr = None; break
            if hr["http_status"] != 429: break
            print(f"  429 rate limit, wait 35 s (attempt {attempt + 1})", flush=True); time.sleep(35)
        if hr is None or hr["http_status"] != 200 or not hr.get("json"): err += 1; continue
        rows = []
        for row in (hr["json"].get("data") or {}).get("candles") or []:
            ts, o, h, l, c, v, oi = I._norm_candle(row)
            if ts is None: continue
            rows.append((ts, "SENSEX", ct.get("exchange") or ct.get("segment"), eik, e, I._safe_float(ct.get("strike_price") or ct.get("strike")), ct.get("instrument_type") or ct.get("option_type"), "5minute", o, h, l, c, v, oi,
                         "upstox_expired_options", I._now(), ts, I._flag(o, h, l, c, v, oi)))
        con.executemany("INSERT OR IGNORE INTO normalized_option_bars(timestamp,underlying,exchange,instrument_key,expiry,strike,option_type,interval,open,high,low,close,volume,open_interest,source,download_timestamp,source_timestamp,quality_flag) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.commit(); got += len(rows)
    tot += len(contracts); new += got; print(f"{e}: ATM {atm} step {step} -> {len(contracts)} contracts, {got} bars", flush=True)
print(f"[done] contracts {tot}, bars {new}, errors {err}", flush=True)
