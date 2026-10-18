#!/usr/bin/env python3
"""Bounded, resumable pull of NIFTY weekly expired-option 5m bars for ONLY the trading days around each expiry (E-6 days .. E): the original importer asked for
2020-01-01..expiry per contract, which the API rejects for older expiries. Reuses the importer's filters/normalisation; writes the same normalized_option_bars
table (INSERT OR IGNORE => idempotent). READ-ONLY API use, no orders. Usage: python scripts/upstox_expiry_days_pull.py [--max-expiries 60] [--atm-steps 5]"""
import argparse
import datetime as dt
import importlib.util
import sqlite3
import sys
import time

sys.path.insert(0, ".")
spec = importlib.util.spec_from_file_location("imp", "scripts/upstox_expired_options_import.py")
I = importlib.util.module_from_spec(spec)
spec.loader.exec_module(I)
U = I.U

ap = argparse.ArgumentParser()
ap.add_argument("--max-expiries", type=int, default=60)
ap.add_argument("--atm-steps", type=int, default=12)
ap.add_argument("--min-have", type=int, default=45, help="skip an expiry that already has this many contracts")
a = ap.parse_args()
con = sqlite3.connect(str(I.DB), timeout=60)
ik = U.UNDERLYING_KEYS["NIFTY"]
er = U.get_expiries(ik)
exps = sorted(er["json"]["data"])[-a.max_expiries:]
print(f"expiries: {len(exps)} ({exps[0]} .. {exps[-1]})", flush=True)
tot = new = err = 0
for e in exps:
    have = con.execute("SELECT COUNT(DISTINCT instrument_key) FROM normalized_option_bars WHERE underlying='NIFTY' AND expiry=?", (e,)).fetchone()[0]
    if have >= a.min_have:
        print(f"{e}: already {have} contracts, skip", flush=True)
        continue
    time.sleep(I.PER_CALL_SLEEP)
    cr = U.get_expired_option_contracts(ik, e)
    contracts = (cr.get("json") or {}).get("data") or []
    # CENTRE THE WINDOW ON INFORMATION KNOWN BEFORE THE EXPIRY DAY (the previous daily close), never on the expiry-day close: centring on the close silently
    # drops exactly the big-move days (the 09:20 ATM falls outside the window) and biases a short-premium test (found and fixed 2026-10-18)
    spot = I._spot_on(str(I.DB), "NIFTY", (dt.date.fromisoformat(e) - dt.timedelta(days=1)).isoformat())
    contracts, atm, step = I._atm_filter(contracts, spot, a.atm_steps)
    if spot is None:
        print(f"{e}: no spot -> skip (never pull unfiltered)", flush=True)
        continue
    frm = (dt.date.fromisoformat(e) - dt.timedelta(days=6)).isoformat()
    got = 0
    for ct in contracts:
        eik = ct.get("expired_instrument_key") or ct.get("instrument_key")
        hr = None
        for attempt in range(4):                                  # HTTP 429 (UDAPI10005) = rate limit (~2000 calls / 30 min): back off and retry, never skip silently
            time.sleep(1.1)
            try:
                hr = U.get_expired_historical_candles(eik, "5minute", e, frm)
            except Exception:
                hr = None
                break
            if hr["http_status"] != 429:
                break
            print(f"  429 rate limit, waiting 30 s (attempt {attempt + 1})", flush=True)
            time.sleep(30)
        if hr is None or hr["http_status"] != 200 or not hr.get("json"):
            err += 1
            continue
        rows = []
        for row in (hr["json"].get("data") or {}).get("candles") or []:
            ts, o, h, l, c, v, oi = I._norm_candle(row)
            if ts is None:
                continue
            rows.append((ts, "NIFTY", ct.get("exchange") or ct.get("segment"), eik, e, I._safe_float(ct.get("strike_price") or ct.get("strike")),
                         ct.get("instrument_type") or ct.get("option_type"), "5minute", o, h, l, c, v, oi, "upstox_expired_options", I._now(), ts,
                         I._flag(o, h, l, c, v, oi)))
        con.executemany("INSERT OR IGNORE INTO normalized_option_bars(timestamp,underlying,exchange,instrument_key,expiry,strike,option_type,interval,open,high,low,close,"
                        "volume,open_interest,source,download_timestamp,source_timestamp,quality_flag) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
        got += len(rows)
    tot += len(contracts)
    new += got
    print(f"{e}: ATM {atm} -> {len(contracts)} contracts, {got} bars", flush=True)
print(f"[done] contracts {tot}, bars {new}, errors {err}", flush=True)
