#!/usr/bin/env python3
"""
upstox_historical_import.py -- READ-ONLY historical-candle importer via the
Upstox Historical Data API v2 (/v2/historical-candle/...), using the Analytics
token (UPSTOX_ANALYTICS_TOKEN, preferred by app.connectors.upstox).

RESEARCH / INGESTION ONLY. Places no orders. Touches no trading / H1-H7 / spike /
broker-execution logic and no production DB. Output = a SEPARATE research store:
    data/historical/upstox/upstox_research.db
        raw_api_responses  -- every response gzipped + sha256 (preservation)
        normalized_bars    -- validated candles, the common research schema
    data/historical/upstox/raw/historical-candle/*.json.gz

Upstox 1minute range cap is ~1 month/call (UDAPI1148 over-range) -> this pages
automatically in safe windows. Never fabricates volume / OI / candles / prices;
missing values stay NULL and are flagged + counted.
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.connectors import upstox as U

OUT = Path(__file__).resolve().parents[1] / "data" / "historical" / "upstox"
RAW = OUT / "raw" / "historical-candle"
DB = OUT / "upstox_research.db"
PER_CALL_SLEEP = 0.25

# conservative window sizes (days) per interval -- stay well under UDAPI1148
WINDOW_DAYS = {"1minute": 28, "3minute": 28, "5minute": 90, "15minute": 180,
               "30minute": 365, "day": 365}
IV_TF = {"1minute": "1m", "3minute": "3m", "5minute": "5m", "15minute": "15m",
         "30minute": "30m", "day": "1d"}

PRESETS = {
    "NIFTY": ("NSE_INDEX|Nifty 50", "NSE", "INDEX"),
    "BANKNIFTY": ("NSE_INDEX|Nifty Bank", "NSE", "INDEX"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_api_responses (
  received_ts TEXT NOT NULL, endpoint TEXT NOT NULL, request_json TEXT,
  http_status INTEGER, sha256 TEXT NOT NULL UNIQUE, gzip_b64 TEXT NOT NULL, raw_file TEXT
);
CREATE TABLE IF NOT EXISTS normalized_bars (
  timestamp TEXT NOT NULL, symbol TEXT NOT NULL, exchange TEXT NOT NULL,
  instrument_type TEXT NOT NULL, expiry TEXT,
  open REAL, high REAL, low REAL, close REAL,
  volume REAL, open_interest REAL, oi_change REAL,
  timeframe TEXT NOT NULL, source TEXT NOT NULL, source_dataset TEXT NOT NULL,
  source_timestamp TEXT, timezone TEXT NOT NULL, quality_flag TEXT NOT NULL,
  UNIQUE(symbol, timeframe, timestamp, source_dataset)
);
CREATE INDEX IF NOT EXISTS ix_ub ON normalized_bars(symbol, timeframe, timestamp);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _windows(dfrom: date, dto: date, wdays: int):
    cur = dto
    while cur >= dfrom:
        w_from = max(dfrom, cur - timedelta(days=wdays - 1))
        yield w_from.isoformat(), cur.isoformat()
        cur = w_from - timedelta(days=1)


def _preserve(con, resp):
    body = json.dumps(resp.get("json") or {}, sort_keys=True)
    sha = hashlib.sha256((resp["endpoint"] + "|" + json.dumps(resp["request"], sort_keys=True)
                          + "|" + body).encode()).hexdigest()
    RAW.mkdir(parents=True, exist_ok=True)
    rf = RAW / f"{sha[:16]}__{int(time.time())}.json.gz"
    with gzip.open(rf, "wt") as f:
        json.dump({"endpoint": resp["endpoint"], "request": resp["request"],
                   "http_status": resp["http_status"], "json": resp.get("json")}, f)
    con.execute("INSERT OR IGNORE INTO raw_api_responses"
                "(received_ts,endpoint,request_json,http_status,sha256,gzip_b64,raw_file) VALUES(?,?,?,?,?,?,?)",
                (_now(), resp["endpoint"], json.dumps(resp["request"]), resp["http_status"], sha,
                 base64.b64encode(gzip.compress(body.encode())).decode(), str(rf)))
    con.commit()


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


def _f(x):
    try:
        return None if x in (None, "", "null") else float(x)
    except (TypeError, ValueError):
        return None


def _resample_5m(candles):
    """Aggregate real 1-minute candles -> 5-minute OHLCV+OI on IST 5-min
    boundaries. This is aggregation of genuine bars, NOT fabrication.
    candles: Upstox rows [ts, o, h, l, c, v, oi] (any order). Returns rows
    newest-first-agnostic list of (ts_iso, o, h, l, c, v, oi)."""
    buckets = {}
    for row in candles:
        ts = row[0] if row else None
        if not ts or len(row) < 5:
            continue
        # ts like '2026-04-01T09:17:00+05:30' -> bucket key = date + HH + (MM//5)
        dpart, tpart = ts[:10], ts[11:19]
        hh, mm = int(tpart[:2]), int(tpart[3:5])
        key = (dpart, hh, (mm // 5) * 5)
        o, h, l, c = _f(row[1]), _f(row[2]), _f(row[3]), _f(row[4])
        v = _f(row[5]) if len(row) > 5 else None
        oi = _f(row[6]) if len(row) > 6 else None
        b = buckets.get(key)
        if b is None:
            buckets[key] = [ts, o, h, l, c, (v or 0.0), oi, ts]   # +last_ts for close pick
        else:
            b[2] = h if b[2] is None else (h if (h is not None and h > b[2]) else b[2])
            b[3] = l if b[3] is None else (l if (l is not None and l < b[3]) else b[3])
            if ts < b[0]:
                b[0], b[1] = ts, o          # earliest -> open
            if ts > b[7]:
                b[7], b[4] = ts, c          # latest -> close, oi
                b[6] = oi
            b[5] = (b[5] or 0.0) + (v or 0.0)
    out = []
    for (dpart, hh, mm5) in sorted(buckets):
        b = buckets[(dpart, hh, mm5)]
        bar_ts = f"{dpart}T{hh:02d}:{mm5:02d}:00+05:30"
        out.append([bar_ts, b[1], b[2], b[3], b[4], b[5], b[6]])
    return out


def run(instruments, dfrom, dto, interval, dry, out, resample=None):
    p = lambda *a: print(*a, file=out)
    h = U.auth_health()
    p(f"[auth] token_kind={h['token_kind']} valid={h['access_token_valid']} "
      f"historical_data_api={h['historical_data_api']}")
    if not dry and h["access_token_valid"] is not True:
        p(f"[STOP] token not usable: {h['note']}")
        return 2
    if not dry and "OK" not in str(h["historical_data_api"]):
        p(f"[STOP] Historical Data API not available: {h['historical_data_api']}")
        return 2

    # Upstox v2 historical-candle supports only 1minute / 30minute / day.
    # For 5m we fetch 1minute and resample locally (real aggregation).
    fetch_iv = "1minute" if resample == "5m" else interval
    tf = "5m" if resample == "5m" else IV_TF[interval]
    ds = ("upstox_1m_resampled_5m" if resample == "5m"
          else f"upstox_historical_candle_v2:{interval}")
    wdays = WINDOW_DAYS[fetch_iv]
    OUT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    tot = {"windows": 0, "candles": 0, "rows": 0, "null_vol": 0, "null_oi": 0,
           "bad_ohlc": 0, "http_err": 0}

    for name in instruments:
        ik, exch, itype = PRESETS.get(name, (name, "NSE", "INDEX"))
        sym = name if name in PRESETS else ik
        p(f"\n=== {sym}  ({ik})  {tf}  {dfrom}..{dto}"
          f"{'  (fetch 1m -> resample 5m)' if resample == '5m' else ''} ===")
        latest = None
        for w_from, w_to in _windows(dfrom, dto, wdays):
            tot["windows"] += 1
            if dry:
                p(f"  [dry] window {w_from}..{w_to}")
                continue
            time.sleep(PER_CALL_SLEEP)
            r = U.get_historical_candles(ik, fetch_iv, w_to, w_from)
            _preserve(con, r)
            if r["http_status"] != 200 or not r.get("json"):
                p(f"  {w_from}..{w_to}: HTTP {r['http_status']}"); tot["http_err"] += 1; continue
            candles = (r["json"].get("data") or {}).get("candles") or []
            if resample == "5m":
                candles = _resample_5m(candles)
            tot["candles"] += len(candles)
            rows = []
            for row in candles:
                ts = row[0] if row else None
                if not ts:
                    continue
                o, hi, lo, c = _f(row[1] if len(row) > 1 else None), _f(row[2] if len(row) > 2 else None), \
                    _f(row[3] if len(row) > 3 else None), _f(row[4] if len(row) > 4 else None)
                v = _f(row[5]) if len(row) > 5 else None
                oi = _f(row[6]) if len(row) > 6 else None
                fl = _flag(o, hi, lo, c, v, oi)
                if fl in ("NULL_VOL", "NULL_VOL_OI"):
                    tot["null_vol"] += 1
                if fl in ("NULL_OI", "NULL_VOL_OI"):
                    tot["null_oi"] += 1
                if fl == "BAD_OHLC":
                    tot["bad_ohlc"] += 1
                rows.append((ts, sym, exch, itype, None, o, hi, lo, c, v, oi, None,
                             tf, "upstox", ds, ts, "IST(+05:30)", fl))
                if latest is None or ts > latest:
                    latest = ts
            before = con.total_changes
            con.executemany(
                "INSERT OR IGNORE INTO normalized_bars(timestamp,symbol,exchange,instrument_type,expiry,"
                "open,high,low,close,volume,open_interest,oi_change,timeframe,source,source_dataset,"
                "source_timestamp,timezone,quality_flag) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            con.commit()
            new = con.total_changes - before
            tot["rows"] += new
            p(f"  {w_from}..{w_to}: {len(candles)} candles -> +{new} new rows ({len(rows) - new} already present)")
        if latest:
            p(f"  latest bar: {latest}")

    n = con.execute("SELECT COUNT(*) FROM normalized_bars WHERE source='upstox'").fetchone()[0]
    p("\n" + "=" * 66)
    p(f"[done] {tot}")
    p(f"[done] upstox rows in normalized_bars: {n}   DB: {DB}")
    con.close()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", action="append",
                    help="repeatable: NIFTY | BANKNIFTY | a raw Upstox instrument_key. Default NIFTY+BANKNIFTY.")
    ap.add_argument("--interval", default="1minute", choices=list(WINDOW_DAYS),
                    help="Upstox v2 historical supports 1minute / 30minute / day only")
    ap.add_argument("--resample", choices=("5m",), default=None,
                    help="fetch 1minute and aggregate locally to this timeframe (real bars, not fabricated)")
    ap.add_argument("--from", dest="dfrom", default=None, help="YYYY-MM-DD (default: --to minus --days)")
    ap.add_argument("--to", dest="dto", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--days", type=int, default=30, help="lookback when --from omitted (default 30)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    dto = date.fromisoformat(a.dto) if a.dto else date.today()
    dfrom = date.fromisoformat(a.dfrom) if a.dfrom else (dto - timedelta(days=a.days))
    insts = a.instrument or ["NIFTY", "BANKNIFTY"]
    sys.exit(run(insts, dfrom, dto, a.interval, a.dry_run, sys.stdout, resample=a.resample))


if __name__ == "__main__":
    main()
