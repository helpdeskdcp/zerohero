"""Large-cap stock universe for the daily scan library (spec: data/research/scanner/SPEC_STOCKS_2026-10-24.md). Daily bars from Upstox historical candles, cached in scanner.db."""
from __future__ import annotations

import gzip
import json
import logging
import os
import time

import pandas as pd

from . import data as D

_log = logging.getLogger("scanner.stocks")
UNIVERSE = ("ADANIENT ADANIPORTS APOLLOHOSP ASIANPAINT AXISBANK BAJAJ-AUTO BAJFINANCE BAJAJFINSV BEL BHARTIARTL BPCL BRITANNIA CIPLA COALINDIA DRREDDY EICHERMOT ETERNAL GRASIM HCLTECH HDFCBANK HDFCLIFE "
            "HEROMOTOCO HINDALCO HINDUNILVR ICICIBANK INDUSINDBK INFY ITC JIOFIN JSWSTEEL KOTAKBANK LT M&M MARUTI NESTLEIND NTPC ONGC POWERGRID RELIANCE SBILIFE SBIN SHRIRAMFIN SUNPHARMA TATACONSUM "
            "TATASTEEL TCS TECHM TITAN TRENT ULTRACEMCO WIPRO INDIGO MAXHEALTH").split()
MASTER = os.path.join(D.ROOT, "data", "scanner", "nse_master.json.gz")
START = "2019-01-01"


def _master(refresh: bool = False) -> list:
    if refresh or not os.path.exists(MASTER) or time.time() - os.path.getmtime(MASTER) > 20 * 3600:
        import requests
        r = requests.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz", timeout=60); r.raise_for_status(); open(MASTER, "wb").write(r.content)
    return json.load(gzip.open(MASTER))


def keys() -> dict[str, str]:
    eq = {r["trading_symbol"]: r["instrument_key"] for r in _master() if r.get("segment") == "NSE_EQ" and r.get("instrument_type") == "EQ"}
    return {s: eq[s] for s in UNIVERSE if s in eq}


def load_stock(sym: str, fetch=None, path: str | None = None, ikey: str | None = None, now=None) -> pd.DataFrame:
    ikey = ikey or keys()[sym]; days = (pd.Timestamp.today().normalize() - pd.Timestamp(START)).days + 5
    d = D.fetch_upstox(sym, ikey, days, fetch, path)
    return D.completed_only(d.dropna(subset=["c"]).sort_values("date").reset_index(drop=True), "NIFTY", now)             # NSE close rule: today's forming bar is dropped
