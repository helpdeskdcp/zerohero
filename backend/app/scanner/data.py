"""Daily bars for the six instruments. Base history = market_history.db market_candles (tf '1d'); the recent part (and all of GOLDM) = Upstox daily candles,
MCX futures chained by daily volume across the two nearest contracts and ratio-adjusted at rolls (latest prices true, older scaled); the base is scaled onto the Upstox
series at the overlap. Read-only; failures degrade to the base history."""
from __future__ import annotations

import gzip
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import store

_log = logging.getLogger("scanner.data")
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
MH = os.path.join(ROOT, "data", "market_history.db"); IST = timezone(timedelta(hours=5, minutes=30))
INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX", "NATURALGAS", "CRUDEOIL", "GOLDM"]
EXCH = {"NIFTY": "NSE", "BANKNIFTY": "NSE", "SENSEX": "NSE", "NATURALGAS": "MCX", "CRUDEOIL": "MCX", "GOLDM": "MCX"}      # SENSEX settles with the NSE schedule for our purposes (BSE 15:30)
BASE = {"NIFTY": ("INDEX", "NIFTY"), "BANKNIFTY": ("INDEX", "BANKNIFTY"), "SENSEX": ("INDEX", "SENSEX"), "NATURALGAS": ("FUTURE", "NATURALGAS"), "CRUDEOIL": ("FUTURE", "CRUDEOIL"), "GOLDM": None}
INDEX_KEYS = {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank", "SENSEX": "BSE_INDEX|SENSEX"}
MCX_UNDERLYING = {"NATURALGAS": "NATURALGAS", "CRUDEOIL": "CRUDEOIL", "GOLDM": "GOLDM"}
CLOSE_MIN = {"NSE": 15 * 60 + 45, "MCX": 23 * 60 + 40}
MCX_MASTER = os.path.join(ROOT, "data", "scanner", "mcx_master.json.gz")


def base_history(inst: str, path: str | None = None) -> pd.DataFrame:
    spec = BASE.get(inst)
    if not spec: return pd.DataFrame(columns=["date", "o", "h", "l", "c", "v"])
    kind, sym = spec
    c = sqlite3.connect(f"file:{path or MH}?mode=ro", uri=True, timeout=30)
    try: rows = c.execute("SELECT session_date_ist, o, h, l, c, v, id FROM market_candles WHERE symbol=? AND kind=? AND tf='1d' AND c IS NOT NULL ORDER BY id", (sym, kind)).fetchall()
    finally: c.close()
    d = pd.DataFrame(rows, columns=["date", "o", "h", "l", "c", "v", "id"]).drop_duplicates("date", keep="last").drop(columns="id")
    d["date"] = pd.to_datetime(d["date"]); d["v"] = d["v"].fillna(0.0); return d.sort_values("date").reset_index(drop=True)


def _mcx_master(refresh: bool = False) -> list:
    os.makedirs(os.path.dirname(MCX_MASTER), exist_ok=True)
    if refresh or not os.path.exists(MCX_MASTER) or time.time() - os.path.getmtime(MCX_MASTER) > 20 * 3600:
        import requests
        r = requests.get("https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz", timeout=60); r.raise_for_status(); open(MCX_MASTER, "wb").write(r.content)
    return json.load(gzip.open(MCX_MASTER))


def front_contracts(inst: str, today: datetime | None = None, n: int = 2) -> list[str]:
    today = today or datetime.now(IST); rows = [r for r in _mcx_master() if r.get("underlying_symbol") == MCX_UNDERLYING[inst] and r.get("instrument_type") == "FUT"]
    live = sorted((r for r in rows if datetime.fromtimestamp(r["expiry"] / 1000, IST).date() >= today.date()), key=lambda r: r["expiry"]); return [r["instrument_key"] for r in live[:n]]


def fetch_upstox(inst: str, ikey: str, days: int = 400, fetch=None, path: str | None = None) -> pd.DataFrame:
    """Daily candles of one instrument key, cached in scanner.db; fetch(ikey, from, to) -> list of [ts, o, h, l, c, v, oi] injectable for tests."""
    to = datetime.now(IST).date(); frm = to - timedelta(days=days)
    if fetch is None:
        from ..connectors import upstox as U
        def fetch(k, f, t):
            r = U.get_historical_candles(k, "day", t.isoformat(), f.isoformat())
            if r["http_status"] != 200: raise RuntimeError(f"upstox {r['http_status']}")
            return (r["json"] or {}).get("data", {}).get("candles", [])
    try: cs = fetch(ikey, frm, to)
    except Exception as e:
        _log.warning("upstox daily fetch %s failed: %r (using cache)", ikey, e); cs = []
    with store.conn(path) as c:
        for k in cs: c.execute("INSERT OR REPLACE INTO upstox_daily (inst, ikey, date, o, h, l, c, v) VALUES (?,?,?,?,?,?,?,?)", (inst, ikey, str(k[0])[:10], k[1], k[2], k[3], k[4], k[5] or 0))
        rows = c.execute("SELECT date, o, h, l, c, v FROM upstox_daily WHERE inst=? AND ikey=? ORDER BY date", (inst, ikey)).fetchall()
    d = pd.DataFrame([tuple(r) for r in rows], columns=["date", "o", "h", "l", "c", "v"]); d["date"] = pd.to_datetime(d["date"]); return d


def chain_contracts(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Volume-chained continuous series from several contracts: for each date the contract with the largest volume; at a switch date the older part is scaled so the
    close on the last day both existed matches (latest contract keeps its true prices)."""
    allr = []
    for i, f in enumerate(frames):
        if len(f): g = f.copy(); g["k"] = i; allr.append(g)
    if not allr: return pd.DataFrame(columns=["date", "o", "h", "l", "c", "v"])
    a = pd.concat(allr); dates = sorted(a["date"].unique()); pick = []
    for d in dates:
        g = a[a["date"] == d]; pick.append(g.sort_values(["v", "k"], ascending=[False, False]).iloc[0])
    p = pd.DataFrame(pick).reset_index(drop=True); out = p[["date", "o", "h", "l", "c", "v", "k"]].copy()
    for j in range(len(out) - 1, 0, -1):                                   # back-adjust: walk from newest to oldest, scale everything older than a switch
        if out.loc[j, "k"] != out.loc[j - 1, "k"]:
            prev_date = out.loc[j - 1, "date"]; new = a[(a["k"] == out.loc[j, "k"]) & (a["date"] == prev_date)]
            if len(new) and new.iloc[0]["c"] > 0:
                ratio = new.iloc[0]["c"] / out.loc[j - 1, "c"]
                for col in ("o", "h", "l", "c"): out.loc[:j - 1, col] = out.loc[:j - 1, col] * ratio
    return out.drop(columns="k").reset_index(drop=True)


def splice(base: pd.DataFrame, recent: pd.DataFrame) -> pd.DataFrame:
    """Recent series is the truth for its dates; base (older) is scaled onto it at the overlap (median close ratio of up to the last 10 shared dates)."""
    if recent.empty: return base
    if base.empty: return recent
    both = pd.merge(base[["date", "c"]], recent[["date", "c"]], on="date", suffixes=("_b", "_r"))
    ratio = float(np.median((both["c_r"] / both["c_b"]).tail(10))) if len(both) else 1.0
    old = base[base["date"] < recent["date"].min()].copy()
    for col in ("o", "h", "l", "c"): old[col] = old[col] * ratio
    return pd.concat([old, recent], ignore_index=True)


def completed_only(d: pd.DataFrame, inst: str, now: datetime | None = None) -> pd.DataFrame:
    now = now or datetime.now(IST); mins = now.hour * 60 + now.minute
    if len(d) and pd.Timestamp(d["date"].iloc[-1]).date() == now.date() and mins < CLOSE_MIN[EXCH[inst]]: d = d.iloc[:-1]       # today's bar is still forming
    return d.reset_index(drop=True)


def load_daily(inst: str, now: datetime | None = None, use_upstox: bool = True, fetch=None, path: str | None = None, base_path: str | None = None) -> pd.DataFrame:
    base = base_history(inst, base_path) if EXCH[inst] == "NSE" else base_history(inst, base_path).iloc[0:0]      # MCX stored history is unadjusted across rolls (spec clarification): Upstox chained series only
    recent = pd.DataFrame(columns=["date", "o", "h", "l", "c", "v"])
    if use_upstox:
        try:
            if inst in INDEX_KEYS: recent = fetch_upstox(inst, INDEX_KEYS[inst], 400, fetch, path)
            else: recent = chain_contracts([fetch_upstox(inst, k, 400, fetch, path) for k in front_contracts(inst)])
        except Exception as e:
            _log.warning("recent daily for %s unavailable: %r", inst, e)
    if inst == "GOLDM" and len(recent): recent = recent[recent["v"] >= 5].reset_index(drop=True)                # the first weeks of a new contract trade 1-lot ghosts
    d = splice(base, recent) if len(recent) else base
    return completed_only(d.dropna(subset=["c"]).sort_values("date").reset_index(drop=True), inst, now)
