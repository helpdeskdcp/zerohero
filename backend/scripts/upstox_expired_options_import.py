#!/usr/bin/env python3
"""
upstox_expired_options_import.py -- READ-ONLY historical importer for EXPIRED
NIFTY / BANKNIFTY option contracts via the Upstox Expired Instruments API v2.

RESEARCH / DATA-INGESTION ONLY. Places no orders. Touches no production /
trading / H1-H7 / spike / broker-execution logic and no production DB. Output
is a SEPARATE research store:
    data/historical/upstox/upstox_research.db
        raw_api_responses     -- every response, gzipped + sha256 (preservation)
        normalized_option_bars-- validated candles, common schema
    data/historical/upstox/raw/<endpoint>/<key>__<ts>.json.gz   -- raw files

Workflow (per the API docs):
  1. get_expiries(underlying)                -> historical expiry dates
  2. get_expired_option_contracts(u, expiry) -> expired CE/PE contracts
  3. per contract -> get_expired_historical_candles(expired_instrument_key,
     '1minute', to_date, from_date)          -> 1-min OHLC + volume + OI
  4. preserve raw, normalize, flag missing/invalid (never fabricate).

NOT a mass download: bounded by --max-expiries and --max-contracts-per-expiry;
refuses large plans without --i-understand-large. Use --dry-run to see the plan
without any network call.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.connectors import upstox as U

OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "historical" / "upstox"
RAW_DIR = OUT_DIR / "raw"
DB = OUT_DIR / "upstox_research.db"
HARD_CAP = 4000                     # contracts * expiries ceiling without override
PER_CALL_SLEEP = 0.30              # be gentle on the API

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_api_responses (
  received_ts TEXT NOT NULL,
  endpoint    TEXT NOT NULL,
  request_json TEXT,
  http_status INTEGER,
  sha256      TEXT NOT NULL UNIQUE,
  gzip_b64    TEXT NOT NULL,
  raw_file    TEXT
);
CREATE TABLE IF NOT EXISTS normalized_option_bars (
  timestamp        TEXT NOT NULL,
  underlying       TEXT NOT NULL,
  exchange         TEXT,
  instrument_key   TEXT NOT NULL,
  expiry           TEXT NOT NULL,
  strike           REAL,
  option_type      TEXT,             -- CE | PE
  interval         TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL,
  volume           REAL,             -- NULL if the API omitted it (never fabricated)
  open_interest    REAL,             -- NULL if the API omitted it (never fabricated)
  source           TEXT NOT NULL,    -- 'upstox_expired_instruments_v2'
  download_timestamp TEXT NOT NULL,
  source_timestamp TEXT,             -- raw candle timestamp string
  quality_flag     TEXT NOT NULL,    -- OK | NULL_VOL | NULL_OI | NULL_VOL_OI | BAD_OHLC | NULL_PRICE
  UNIQUE(instrument_key, interval, timestamp)
);
CREATE INDEX IF NOT EXISTS ix_nob ON normalized_option_bars(underlying, expiry, strike, option_type, timestamp);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _preserve_raw(con, resp: dict):
    body = json.dumps(resp.get("json") if resp.get("json") is not None else {"_text_len": resp.get("text_len")},
                      sort_keys=True)
    sha = hashlib.sha256((resp["endpoint"] + "|" + json.dumps(resp["request"], sort_keys=True) + "|" + body)
                         .encode()).hexdigest()
    d = RAW_DIR / resp["endpoint"].replace("/", "_")
    d.mkdir(parents=True, exist_ok=True)
    rf = d / f"{sha[:16]}__{int(time.time())}.json.gz"
    with gzip.open(rf, "wt") as f:
        json.dump({"endpoint": resp["endpoint"], "request": resp["request"],
                   "http_status": resp["http_status"], "json": resp.get("json")}, f)
    con.execute("INSERT OR IGNORE INTO raw_api_responses(received_ts,endpoint,request_json,http_status,sha256,gzip_b64,raw_file)"
                " VALUES (?,?,?,?,?,?,?)",
                (_now(), resp["endpoint"], json.dumps(resp["request"]), resp["http_status"], sha,
                 base64.b64encode(gzip.compress(body.encode())).decode(), str(rf)))
    con.commit()
    return sha


def _flag(o, h, l, c, v, oi):
    if None in (o, h, l, c):
        return "NULL_PRICE"
    if not (h >= max(o, c) - 1e-6 and l <= min(o, c) + 1e-6 and h >= l and l > 0):
        return "BAD_OHLC"
    if v is None and oi is None:
        return "NULL_VOL_OI"
    if v is None:
        return "NULL_VOL"
    if oi is None:
        return "NULL_OI"
    return "OK"


def _norm_candle(row):
    """Upstox candle = [ts, open, high, low, close, volume, open_interest] (7 cols).
    Missing / null -> stays None (NEVER fabricated)."""
    def g(i):
        try:
            x = row[i]
            return None if x in (None, "", "null") else float(x)
        except (IndexError, TypeError, ValueError):
            return None
    ts = row[0] if row and len(row) else None
    o, h, l, c, v, oi = g(1), g(2), g(3), g(4), g(5), g(6)
    return ts, o, h, l, c, v, oi


def run(underlyings, dfrom, dto, interval, max_exp, max_ct, dry, big_ok, out):
    p = lambda *a: print(*a, file=out)
    health = U.auth_health()
    p(f"[auth] configured={health['credentials_configured']} token_present={health['access_token_present']} "
      f"token_valid={health['access_token_valid']} api_reachable={health['api_reachable']}")
    p(f"       {health['note']}")

    plan = max_exp * max_ct * len(underlyings)
    p(f"[plan] underlyings={underlyings} expiries<= {max_exp} contracts/expiry<= {max_ct} interval={interval}  "
      f"=> up to {plan} contract-pulls")
    if plan > HARD_CAP and not big_ok:
        p(f"[STOP] plan {plan} > HARD_CAP {HARD_CAP}. Re-run with --i-understand-large to proceed.")
        return 2
    if not health["credentials_configured"]:
        p("[STOP] Upstox API key/secret not configured in .env.")
        return 2
    if not dry and not health["access_token_present"]:
        p("[STOP] No UPSTOX_ACCESS_TOKEN. Run:  venv/bin/python scripts/upstox_auth.py --login-url")
        p("       then complete the browser OAuth and store the token in .env:UPSTOX_ACCESS_TOKEN.")
        return 2
    if not dry and health["access_token_valid"] is False:
        p("[STOP] Access token present but rejected by the API (expired?). Re-run OAuth.")
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    totals = {"contracts": 0, "candles": 0, "norm_rows": 0, "null_vol": 0, "null_oi": 0,
              "bad_ohlc": 0, "errors": 0}

    for u in underlyings:
        ik = U.UNDERLYING_KEYS[u]
        p(f"\n=== {u}  ({ik}) ===")
        if dry:
            p("  [dry-run] would GET expired-instruments/expiries")
            continue
        er = U.get_expiries(ik)
        _preserve_raw(con, er)
        if er["http_status"] != 200 or not er.get("json"):
            p(f"  expiries: HTTP {er['http_status']} -- skip"); totals["errors"] += 1; continue
        exps = er["json"].get("data") or []
        exps = [e for e in exps if (not dfrom or e >= dfrom) and (not dto or e <= dto)]
        exps = sorted(exps)[-max_exp:]           # most recent within the window
        p(f"  expiries in window: {exps}")
        for expiry in exps:
            time.sleep(PER_CALL_SLEEP)
            cr = U.get_expired_option_contracts(ik, expiry)
            _preserve_raw(con, cr)
            if cr["http_status"] != 200 or not cr.get("json"):
                p(f"  {expiry}: contracts HTTP {cr['http_status']}"); totals["errors"] += 1; continue
            contracts = cr["json"].get("data") or []
            contracts = contracts[:max_ct]
            p(f"  {expiry}: {len(contracts)} contracts (capped at {max_ct})")
            for ct in contracts:
                totals["contracts"] += 1
                eik = ct.get("expired_instrument_key") or ct.get("instrument_key")
                strike = ct.get("strike_price") or ct.get("strike")
                otype = ct.get("instrument_type") or ct.get("option_type")
                exch = ct.get("exchange") or ct.get("segment")
                if not eik:
                    totals["errors"] += 1; continue
                time.sleep(PER_CALL_SLEEP)
                try:
                    hr = U.get_expired_historical_candles(eik, interval, expiry, dfrom or "2020-01-01")
                except Exception as e:  # noqa
                    p(f"    {eik}: candle error {type(e).__name__}"); totals["errors"] += 1; continue
                _preserve_raw(con, hr)
                if hr["http_status"] != 200 or not hr.get("json"):
                    totals["errors"] += 1; continue
                candles = (hr["json"].get("data") or {}).get("candles") or []
                totals["candles"] += len(candles)
                rows = []
                for row in candles:
                    ts, o, h, l, c, v, oi = _norm_candle(row)
                    if ts is None:
                        continue
                    fl = _flag(o, h, l, c, v, oi)
                    if fl in ("NULL_VOL", "NULL_VOL_OI"):
                        totals["null_vol"] += 1
                    if fl in ("NULL_OI", "NULL_VOL_OI"):
                        totals["null_oi"] += 1
                    if fl == "BAD_OHLC":
                        totals["bad_ohlc"] += 1
                    rows.append((ts, u, exch, eik, expiry, _safe_float(strike), otype, interval,
                                 o, h, l, c, v, oi, "upstox_expired_instruments_v2", _now(), ts, fl))
                con.executemany(
                    "INSERT OR IGNORE INTO normalized_option_bars(timestamp,underlying,exchange,instrument_key,"
                    "expiry,strike,option_type,interval,open,high,low,close,volume,open_interest,source,"
                    "download_timestamp,source_timestamp,quality_flag) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows)
                con.commit()
                totals["norm_rows"] += len(rows)

    tot = con.execute("SELECT COUNT(*) FROM normalized_option_bars").fetchone()[0]
    con.close()
    p("\n" + "=" * 70)
    p(f"[done] {totals}")
    p(f"[done] normalized_option_bars total rows now: {tot}")
    p(f"[done] DB: {DB}")
    return 0


def _safe_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--underlying", action="append", choices=list(U.UNDERLYING_KEYS),
                    help="repeatable; default NIFTY + BANKNIFTY")
    ap.add_argument("--from", dest="dfrom", default=None, help="earliest expiry YYYY-MM-DD")
    ap.add_argument("--to", dest="dto", default=None, help="latest expiry YYYY-MM-DD")
    ap.add_argument("--interval", default="1minute", choices=list(U.INTERVALS))
    ap.add_argument("--max-expiries", type=int, default=2)
    ap.add_argument("--max-contracts-per-expiry", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--i-understand-large", action="store_true")
    a = ap.parse_args()
    unders = a.underlying or ["NIFTY", "BANKNIFTY"]
    rc = run(unders, a.dfrom, a.dto, a.interval, a.max_expiries, a.max_contracts_per_expiry,
             a.dry_run, a.i_understand_large, sys.stdout)
    sys.exit(rc)


if __name__ == "__main__":
    main()
