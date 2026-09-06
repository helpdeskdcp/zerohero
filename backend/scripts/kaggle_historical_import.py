#!/usr/bin/env python3
"""
kaggle_historical_import.py -- Kaggle historical-data validation + normalized import.

READ-ONLY w.r.t. production: does NOT touch market_history.db / market_candles /
broker capture / trading / H1-H7 / spike logic. Raw Kaggle files are left
untouched. Output goes to a DEDICATED research DB + inventory in the historical
dir:
    data/historical/kaggle/research_historical.db   (table: normalized_bars)
    data/historical/kaggle/inventory.csv

DATA POLICY (enforced): no fabricated OI, no volume->OI, no invented timestamps,
no synthetic fill. OHLCV-without-OI keeps open_interest = NULL.
"""
from __future__ import annotations

import csv as _csv
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "data" / "historical" / "kaggle"
DB = BASE / "research_historical.db"
INV = BASE / "inventory.csv"
IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime.now(timezone.utc)

NORM_SCHEMA = """
CREATE TABLE IF NOT EXISTS normalized_bars (
  timestamp        TEXT NOT NULL,     -- ISO, UTC
  symbol           TEXT NOT NULL,
  exchange         TEXT NOT NULL,
  instrument_type  TEXT NOT NULL,     -- INDEX | FUTURE
  expiry           TEXT,
  open REAL, high REAL, low REAL, close REAL,
  volume           REAL,              -- NULL if the source has none
  open_interest    REAL,              -- NULL unless the source genuinely has OI
  oi_change        REAL,              -- NULL unless genuinely present
  timeframe        TEXT NOT NULL,     -- 1m | 5m | 1d
  source           TEXT NOT NULL,     -- 'kaggle'
  source_dataset   TEXT NOT NULL,
  source_timestamp TEXT,              -- raw timestamp string as-is
  timezone         TEXT NOT NULL,
  UNIQUE(symbol, timeframe, timestamp, source_dataset)
);
CREATE INDEX IF NOT EXISTS ix_nb ON normalized_bars(symbol, timeframe, timestamp);
"""

# ---- selected raw files: (symbol, exch, itype, tf, dataset, path, parser) ----
def _dt_naive_ist(s):
    """'2015-01-09 09:15:00' (naive, IST) -> UTC iso."""
    d = datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    return d.astimezone(timezone.utc).isoformat(), s.strip()


def _dt_ddmmyyyy_time(date_s, time_s):
    """'09-01-2015' + '9:15:00' (IST) -> UTC iso."""
    d = datetime.strptime(f"{date_s.strip()} {time_s.strip()}", "%d-%m-%Y %H:%M:%S").replace(tzinfo=IST)
    return d.astimezone(timezone.utc).isoformat(), f"{date_s} {time_s}"


def _dt_yyyymmdd_hhmm(date_s, time_s):
    """'20080101' + '09:55' (IST) -> UTC iso."""
    d = datetime.strptime(f"{date_s.strip()} {time_s.strip()}", "%Y%m%d %H:%M").replace(tzinfo=IST)
    return d.astimezone(timezone.utc).isoformat(), f"{date_s} {time_s}"


def _dt_ddmonyyyy(s):
    """'1-April-2021' -> local trading-date iso (daily; NOT tz-shifted)."""
    d = datetime.strptime(s.strip(), "%d-%B-%Y")
    return d.date().isoformat(), s.strip()


def _dt_dd_mm_yyyy(s):
    d = datetime.strptime(s.strip().strip('"'), "%d-%m-%Y")
    return d.date().isoformat(), s.strip()


def _num(x):
    if x is None:
        return None
    x = str(x).strip().strip('"').replace(",", "")
    if x == "" or x.lower() in ("nan", "null", "none"):
        return None
    try:
        return float(x)
    except ValueError:
        return None


def p_debashis(path):  # date,open,high,low,close,volume  (naive IST, volume col all 0 for index)
    with open(path) as f:
        for r in _csv.DictReader(f):
            try:
                ts, raw = _dt_naive_ist(r["date"])
            except ValueError:
                continue
            v = _num(r.get("volume"))
            yield ts, raw, _num(r["open"]), _num(r["high"]), _num(r["low"]), _num(r["close"]), \
                (v if (v is not None and v > 0) else None), None, None


def p_mavrick(path):  # datetime,open,high,low,close,volume  (tz-aware +05:30)
    with open(path) as f:
        for r in _csv.DictReader(f):
            s = r["datetime"].strip()
            try:
                d = datetime.fromisoformat(s)
                if d.tzinfo is None:
                    d = d.replace(tzinfo=IST)
                ts = d.astimezone(timezone.utc).isoformat()
            except ValueError:
                continue
            v = _num(r.get("volume"))
            yield ts, s, _num(r["open"]), _num(r["high"]), _num(r["low"]), _num(r["close"]), \
                (v if (v is not None and v > 0) else None), None, None


def p_nishanth(path):  # Instrument,Date,Time,Open,High,Low,Close  (no volume col)
    with open(path) as f:
        for r in _csv.DictReader(f):
            try:
                ts, raw = _dt_yyyymmdd_hhmm(r["Date"], r["Time"])
            except (ValueError, KeyError):
                continue
            yield ts, raw, _num(r["Open"]), _num(r["High"]), _num(r["Low"]), _num(r["Close"]), \
                None, None, None


def p_sandeep(path):  # Instrument,Date,Time,Open,High,Low,Close  (DD-MM-YYYY, no volume)
    with open(path) as f:
        for r in _csv.DictReader(f):
            try:
                ts, raw = _dt_ddmmyyyy_time(r["Date"], r["Time"])
            except (ValueError, KeyError):
                continue
            yield ts, raw, _num(r["Open"]), _num(r["High"]), _num(r["Low"]), _num(r["Close"]), \
                None, None, None


def p_rbtj(path):  # Symbol,Date,Expiry,Open,High,Low,Close,LTP,Settle,No.of contracts,Turnover,Open Int,Change in OI,Underlying
    with open(path) as f:
        for r in _csv.DictReader(f):
            try:
                d = datetime.strptime(r["Date"].strip(), "%d/%m/%Y")
                ts = d.date().isoformat()   # daily: local trading date
            except (ValueError, KeyError):
                continue
            exp = None
            try:
                exp = datetime.strptime(r["Expiry"].strip(), "%d/%m/%Y").date().isoformat()
            except (ValueError, KeyError):
                pass
            yield (ts, r["Date"].strip(), _num(r["Open"]), _num(r["High"]), _num(r["Low"]),
                   _num(r["Close"]), _num(r.get("No. of contracts")),
                   _num(r.get("Open Int")), _num(r.get("Change in OI")), exp)


def p_naveen(path, sym):  # "Date","Price","Open","High","Low","Vol.","Change %" (daily MCX)
    with open(path, encoding="utf-8-sig") as f:
        for r in _csv.DictReader(f):
            try:
                ts, raw = _dt_dd_mm_yyyy(r["Date"])
            except (ValueError, KeyError):
                continue
            vs = str(r.get("Vol.", "")).strip().strip('"')
            vol = None
            if vs and vs not in ("-", ""):
                mult = 1000 if vs.upper().endswith("K") else (1_000_000 if vs.upper().endswith("M") else 1)
                try:
                    vol = float(vs.rstrip("KkMm")) * mult
                except ValueError:
                    vol = None
            yield ts, raw, _num(r["Open"]), _num(r["High"]), _num(r["Low"]), _num(r["Price"]), vol, None, None


def p_sensex(path):  # Date,Open,High,Low,Close,XYZ  (daily, DD-Month-YYYY)
    with open(path) as f:
        for r in _csv.DictReader(f):
            try:
                ts, raw = _dt_ddmonyyyy(r["Date"])
            except (ValueError, KeyError):
                continue
            yield ts, raw, _num(r["Open"]), _num(r["High"]), _num(r["Low"]), _num(r["Close"]), None, None, None


SELECTED = [
    # symbol, exch, itype, tf, dataset_ref, relpath, parser, tz
    ("NIFTY", "NSE", "INDEX", "5m", "debashis74017/nifty-50-minute-data",
     "debashis74017__nifty-50-minute-data/NIFTY_50_5minute.csv", p_debashis, "IST"),
    ("NIFTY", "NSE", "INDEX", "1m", "debashis74017/nifty-50-minute-data",
     "debashis74017__nifty-50-minute-data/NIFTY_50_minute.csv", p_debashis, "IST"),
    ("BANKNIFTY", "NSE", "INDEX", "5m", "debashis74017/nifty-50-minute-data",
     "debashis74017__nifty-50-minute-data/NIFTY BANK_5minute.csv", p_debashis, "IST"),
    ("NIFTY", "NSE", "INDEX", "1m", "mavrick136/10-years-of-nifty-50-1-minute-data-20152025",
     "mavrick136__10-years-of-nifty-50-1-minute-data-20152025/NIFTY_1MIN_2015-2025.csv", p_mavrick, "IST"),
    ("NIFTY", "NSE", "INDEX", "1m", "nishanthsalian/indian-stock-index-1minute-data-2008-2020",
     "nishanthsalian__indian-stock-index-1minute-data-2008-2020/NIFTY_2008_2020.csv", p_nishanth, "IST"),
    ("BANKNIFTY", "NSE", "INDEX", "5m", "sandeepkapri/banknifty-data-upto-2024",
     "sandeepkapri__banknifty-data-upto-2024/bank-nifty-5m-data.csv", p_sandeep, "IST"),
    ("BANKNIFTY", "NSE", "INDEX", "1m", "sandeepkapri/banknifty-data-upto-2024",
     "sandeepkapri__banknifty-data-upto-2024/bank-nifty-1m-data.csv", p_sandeep, "IST"),
    ("NIFTY", "NSE", "FUTURE", "1d", "rbtj1729/nifty-futures-data-jan-2021-march-2026",
     "rbtj1729__nifty-futures-data-jan-2021-march-2026/nifty_futures_combined.csv", p_rbtj, "IST"),
    ("GOLD", "MCX", "INDEX", "1d", "naveennas/metals-price-historical-data-mcx-data-7-metals",
     "naveennas__metals-price-historical-data-mcx-data-7-metals/Refined Gold Historical Data.csv",
     lambda p: p_naveen(p, "GOLD"), "IST"),
    ("SILVER", "MCX", "INDEX", "1d", "naveennas/metals-price-historical-data-mcx-data-7-metals",
     "naveennas__metals-price-historical-data-mcx-data-7-metals/Silver Historical Data.csv",
     lambda p: p_naveen(p, "SILVER"), "IST"),
    ("SENSEX", "BSE", "INDEX", "1d", "prathamsatani/s-and-p-bse-sensex-01-apr-23-to-15-dec-23",
     "prathamsatani__s-and-p-bse-sensex-01-apr-23-to-15-dec-23/dataset_total.csv", p_sensex, "IST"),
]

STEP = {"1m": 60, "5m": 300, "1d": 86400}


def validate_and_load(con, sym, exch, itype, tf, ds, path, parser, tz):
    rows = list(parser(path))
    n_raw = len(rows)
    if not n_raw:
        return {"dataset": ds, "symbol": sym, "tf": tf, "status": "EMPTY/UNPARSED", "rows": 0}
    seen = set()
    dup_ts = dup_st = ohlc_bad = zero_vol = null_oi = weekend = future_ts = grid_off = 0
    tss = []
    ins = []
    for ts, raw, o, h, l, c, v, oi, doi, *exp in rows:
        expv = exp[0] if exp else None
        key = (sym, tf, ts, ds)
        if key in seen:
            dup_st += 1
            continue
        seen.add(key)
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:              # daily bars carry a bare date -> treat as IST
            dt = dt.replace(tzinfo=IST)
        tss.append(dt)
        if dt > NOW + timedelta(days=1):
            future_ts += 1
        if dt.weekday() >= 5:
            weekend += 1
        if None not in (o, h, l, c):
            if not (h >= max(o, c) - 1e-6 and l <= min(o, c) + 1e-6 and h >= l and l > 0):
                ohlc_bad += 1
        if v is None:
            zero_vol += 1
        if oi is None:
            null_oi += 1
        if tf in ("1m", "5m"):
            secs = int(dt.timestamp())
            if secs % STEP[tf] != 0:
                grid_off += 1
        ins.append((ts, sym, exch, itype, expv, o, h, l, c, v, oi, doi, tf, "kaggle", ds, raw, tz))
    tss.sort()
    dup_ts = len(tss) - len(set(tss))
    gaps = 0
    if tf in ("1m", "5m") and len(tss) > 1:
        for a, b in zip(tss, tss[1:]):
            d = (b - a).total_seconds()
            # only count intra-session gaps (< 6h) that aren't a clean multiple
            if 0 < d < 6 * 3600 and d != STEP[tf]:
                gaps += 1
    con.executemany(
        "INSERT OR IGNORE INTO normalized_bars(timestamp,symbol,exchange,instrument_type,expiry,"
        "open,high,low,close,volume,open_interest,oi_change,timeframe,source,source_dataset,"
        "source_timestamp,timezone) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ins)
    con.commit()
    loaded = con.execute("SELECT COUNT(*) FROM normalized_bars WHERE symbol=? AND timeframe=? "
                         "AND source_dataset=?", (sym, tf, ds)).fetchone()[0]
    has_real_oi = null_oi < len(ins)
    status = "OK"
    if ohlc_bad > len(ins) * 0.01:
        status = "OHLC_WARN"
    if dup_st or dup_ts:
        status = "DUP_WARN"
    return {
        "dataset": ds, "symbol": sym, "exchange": exch, "instrument_type": itype, "tf": tf,
        "rows_raw": n_raw, "rows_loaded": loaded,
        "ts_min": tss[0].isoformat() if tss else None, "ts_max": tss[-1].isoformat() if tss else None,
        "dup_ts": dup_ts, "dup_sym_ts": dup_st, "ohlc_bad": ohlc_bad,
        "null_volume": zero_vol, "null_oi": null_oi, "has_real_oi": has_real_oi,
        "weekend_rows": weekend, "future_ts": future_ts,
        "grid_off_5m_1m": grid_off if tf in ("1m", "5m") else None,
        "intrasession_gaps": gaps if tf in ("1m", "5m") else None,
        "status": status,
    }


def main():
    con = sqlite3.connect(DB)
    con.executescript(NORM_SCHEMA)
    inv = []
    for sym, exch, itype, tf, ds, rel, parser, tz in SELECTED:
        p = BASE / rel
        if not p.exists():
            inv.append({"dataset": ds, "symbol": sym, "tf": tf, "status": "FILE_MISSING", "rows_loaded": 0})
            print(f"MISSING {rel}")
            continue
        r = validate_and_load(con, sym, exch, itype, tf, ds, p, parser, tz)
        inv.append(r)
        print(f"{sym:9} {tf:3} {ds:52} loaded={r.get('rows_loaded',0):>9}  "
              f"{r.get('ts_min','?')[:10]}..{r.get('ts_max','?')[:10]}  "
              f"OI={'REAL' if r.get('has_real_oi') else 'NULL'}  grid_off={r.get('grid_off_5m_1m')}  "
              f"dups={r.get('dup_ts',0)}/{r.get('dup_sym_ts',0)}  {r['status']}")
    fields = sorted({k for row in inv for k in row})
    with INV.open("w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(inv)
    # Disk-responsible: the normalized DB keeps 5m + daily only. 1m raw files are
    # preserved on disk and their stats are in inventory.csv, but ~4.3M jitter/
    # dup-prone 1m rows are not carried in the DB (they inflate it to ~1.6 GB).
    n1m = con.execute("DELETE FROM normalized_bars WHERE timeframe='1m'").rowcount
    con.commit()
    con.execute("VACUUM")
    tot = con.execute("SELECT COUNT(*) FROM normalized_bars").fetchone()[0]
    print(f"\npruned {n1m} 1m rows from the DB (raw 1m files + inventory retained)")
    print(f"normalized_bars total rows (5m + 1d): {tot}")
    print(f"DB: {DB}  ({DB.stat().st_size / 1e6:.0f} MB)")
    print(f"inventory: {INV}")
    con.close()


if __name__ == "__main__":
    main()
