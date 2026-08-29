"""
Instrument registry — maps a friendly symbol ("NIFTY", "BANKNIFTY", …) to
the Angel One SmartAPI parameters the historical-candle endpoint needs
(exchange + symboltoken), plus timeframe → Angel interval translation and
lookback-window computation.

Why this exists: the dashboard's Run form and the scalp watchlist send a
name and a timeframe. Without a token + a date range the broker request is
all-nulls and every run returns DATA_UNAVAILABLE. This module fills the gap.

Seeds cover the liquid index underlyings. Anything else (MCX contracts are
expiry-dated, single stocks, weekly option strikes) the user adds at
runtime via POST /api/instruments — persisted in app_settings, no redeploy.
"""
import json
import os
import time
import requests
from datetime import datetime, timezone, timedelta

from . import db

SETTINGS_KEY = "instrument_registry"

# exchange + symboltoken for the *spot* index series on Angel One SmartAPI.
# These are the widely-published values; correct any via /api/instruments.
_SEED = {
    "NIFTY":      {"exchange": "NSE", "symboltoken": "99926000", "market": "NSE", "aliases": ["NIFTY50", "NIFTY 50"]},
    "BANKNIFTY":  {"exchange": "NSE", "symboltoken": "99926009", "market": "NSE", "aliases": ["NIFTYBANK", "BANK NIFTY"]},
    "FINNIFTY":   {"exchange": "NSE", "symboltoken": "99926037", "market": "NSE", "aliases": ["NIFTYFIN", "FIN NIFTY"]},
    "MIDCPNIFTY": {"exchange": "NSE", "symboltoken": "99926074", "market": "NSE", "aliases": ["MIDCAPNIFTY", "NIFTYMIDSELECT"]},
    "SENSEX":     {"exchange": "BSE", "symboltoken": "99919000", "market": "BSE", "aliases": []},
    "BANKEX":     {"exchange": "BSE", "symboltoken": "99919012", "market": "BSE", "aliases": []},
    # MCX commodity FUTURES tokens are expiry-dated — the values below are the
    # front-month contract and MUST be rolled (POST /api/instruments) each expiry.
    # NATURALGAS front month: 25SEP2026 (roll after ~25 Sep 2026 to 27OCT2026 = 570750).
    "NATURALGAS": {"exchange": "MCX", "symboltoken": "568245", "market": "MCX", "aliases": ["NATGAS", "NG"]},
    # NATGASMINI (lot 250) front month: 25SEP2026 (roll to 27OCT2026 = 570751).
    "NATGASMINI": {"exchange": "MCX", "symboltoken": "568246", "market": "MCX", "aliases": ["NGMINI", "NATURALGASMINI"]},
    "CRUDEOILMINI": {"exchange": "MCX", "symboltoken": "", "market": "MCX", "aliases": ["CRUDE OIL MINI"]},
    "GOLD": {"exchange": "MCX", "symboltoken": "", "market": "MCX", "aliases": []},
    "GOLDMINI": {"exchange": "MCX", "symboltoken": "", "market": "MCX", "aliases": ["GOLD MINI"]},
    "SILVER": {"exchange": "MCX", "symboltoken": "", "market": "MCX", "aliases": []},
    "SILVERMINI": {"exchange": "MCX", "symboltoken": "", "market": "MCX", "aliases": ["SILVER MINI"]},
}


def canonical(symbol: str) -> str:
    """Return the canonical underlying name, never a loose alias."""
    meta = resolve(symbol)
    return str(meta.get("canonical") if meta else _norm(symbol)).upper()

# minute-per-bar for each Angel interval, used for the lookback window
_TF_TO_INTERVAL = {
    "1m": ("ONE_MINUTE", 1), "1": ("ONE_MINUTE", 1), "one_minute": ("ONE_MINUTE", 1),
    "3m": ("THREE_MINUTE", 3), "3": ("THREE_MINUTE", 3),
    "5m": ("FIVE_MINUTE", 5), "5": ("FIVE_MINUTE", 5),
    "10m": ("TEN_MINUTE", 10),
    "15m": ("FIFTEEN_MINUTE", 15), "15": ("FIFTEEN_MINUTE", 15),
    "30m": ("THIRTY_MINUTE", 30),
    "1h": ("ONE_HOUR", 60), "60m": ("ONE_HOUR", 60), "60": ("ONE_HOUR", 60),
    "1d": ("ONE_DAY", 375), "day": ("ONE_DAY", 375),
}

_IST = timezone(timedelta(hours=5, minutes=30))
MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
MASTER_CACHE = os.environ.get("CHANAKYA_INSTRUMENT_MASTER", "./data/instrument_master.json")


def interval_for(timeframe: str):
    """('ONE_MINUTE', 1) — Angel interval name + minutes-per-bar. Defaults to 5m."""
    return _TF_TO_INTERVAL.get(str(timeframe or "").strip().lower(), ("FIVE_MINUTE", 5))


def _norm(name: str) -> str:
    return str(name or "").strip().upper().replace(" ", "").replace("-", "").replace("_", "")


def _load_overrides() -> dict:
    try:
        raw = db.get_setting(SETTINGS_KEY)
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def registry() -> dict:
    """Merged view: seeds overlaid with user-added / user-corrected entries."""
    merged = {k: {**dict(v), "canonical": k} for k, v in _SEED.items()}
    for k, v in _load_overrides().items():
        key = _norm(k)
        merged[key] = {**merged.get(key, {}), **(v or {}), "canonical": key}
    return merged


def add_instrument(name: str, exchange: str, symboltoken: str, market: str | None = None,
                   aliases: list | None = None) -> dict:
    if not name or not exchange or not symboltoken:
        raise ValueError("name, exchange and symboltoken are all required")
    ov = _load_overrides()
    key = _norm(name)
    ov[key] = {
        "exchange": str(exchange).upper(),
        "symboltoken": str(symboltoken),
        "market": (market or exchange).upper(),
        "aliases": aliases or [],
    }
    db.set_setting(SETTINGS_KEY, json.dumps(ov))
    return {key: ov[key]}


def resolve(symbol: str) -> dict | None:
    """friendly name (or alias) -> {exchange, symboltoken, market}. None if unknown."""
    if not symbol:
        return None
    want = _norm(symbol)
    reg = registry()
    if want in reg:
        return reg[want]
    for key, meta in reg.items():
        if want == key or want in {_norm(a) for a in (meta.get("aliases") or [])}:
            return meta
    return None


def refresh_master(*, timeout: float = 15) -> dict:
    """Download official Angel One scrip master (read-only) and cache locally."""
    try:
        r = requests.get(MASTER_URL, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list) or not data:
            raise ValueError("empty instrument master")
        path = os.path.abspath(MASTER_CACHE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return {"status": "OK", "rows": len(data), "source": "ANGELONE_SCRIP_MASTER", "timestamp": time.time()}
    except Exception as e:
        return {"status": "API_ERROR", "rows": 0, "reason": type(e).__name__}


def master_rows(*, refresh: bool = False) -> list[dict]:
    path = os.path.abspath(MASTER_CACHE)
    if refresh or not os.path.exists(path):
        refresh_master()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _master_meta(r: dict) -> dict:
    exch = str(r.get("exch_seg") or r.get("exchange") or "").upper()
    raw_strike = r.get("strike")
    try: strike = float(raw_strike) / 100 if float(raw_strike) > 100000 else float(raw_strike)
    except (TypeError, ValueError): strike = None
    return {"exchange": exch, "symbol": r.get("symbol") or "", "symboltoken": str(r.get("token") or r.get("symboltoken") or ""),
            "underlying": str(r.get("name") or "").upper(), "expiry": r.get("expiry") or "", "strike": strike,
            "option_type": str(r.get("symbol") or "").upper().rstrip("0123456789").endswith("CE") and "CE" or ("PE" if str(r.get("symbol") or "").upper().endswith("PE") else ""),
            "lot_size": r.get("lotsize"), "tick_size": r.get("tick_size") or r.get("ticksize"), "instrumenttype": r.get("instrumenttype") or ""}


def resolve_nse_option(underlying: str, expiry: str = "AUTO", strike="ATM", option_type: str = "CE", *, spot: float | None = None) -> dict:
    """Resolve an NFO option solely from the current official master."""
    u = canonical(underlying); typ = str(option_type or "").upper()
    rows = [_master_meta(r) for r in master_rows()]
    rows = [r for r in rows if r["exchange"] == "NFO" and r["underlying"] == u and r["option_type"] == typ and r["symboltoken"]]
    if not rows: return {"status": "DATA_UNAVAILABLE", "reason": "no NFO contracts in instrument master"}
    valid = sorted({r["expiry"] for r in rows if r["expiry"]})
    now = datetime.now(_IST).date()
    def key(x):
        for f in ("%d%b%Y", "%Y-%m-%d", "%d-%b-%Y"):
            try: return datetime.strptime(x.upper(), f).date()
            except ValueError: pass
        return None
    valid = [x for x in valid if key(x) and key(x) >= now]
    if not valid: return {"status": "CONTRACT_INVALID", "reason": "no non-expired expiry"}
    mode = str(expiry or "AUTO").upper()
    selected = valid[0] if mode in ("AUTO", "CURRENT") else (valid[1] if mode == "NEXT" and len(valid) > 1 else valid[-1] if mode == "LATEST" else mode)
    rows = [r for r in rows if r["expiry"] == selected]
    if not rows: return {"status": "CONTRACT_INVALID", "reason": "requested expiry unavailable"}
    if strike == "ATM":
        if spot is None: return {"status": "DATA_UNAVAILABLE", "reason": "spot required for ATM"}
        chosen = min(rows, key=lambda r: abs((r["strike"] or 0) - float(spot)))
    else:
        try: chosen = next(r for r in rows if abs((r["strike"] or 0) - float(strike)) < 1e-9)
        except (StopIteration, TypeError, ValueError): return {"status": "CONTRACT_INVALID", "reason": "strike unavailable"}
    return {**chosen, "status": "OK", "expiry_selection_mode": mode, "available_expiries": valid,
            "current_expiry": valid[0], "next_expiry": valid[1] if len(valid) > 1 else None, "latest_expiry": valid[-1]}


def resolve_mcx_future(symbol: str, expiry: str = "AUTO") -> dict:
    u = canonical(symbol); rows = [_master_meta(r) for r in master_rows()]
    rows = [r for r in rows if r["exchange"] == "MCX" and (r["underlying"] == u or u in str(r["symbol"]).upper()) and r["symboltoken"]]
    valid = sorted({r["expiry"] for r in rows if r["expiry"]})
    if not valid: return {"status": "DATA_UNAVAILABLE", "reason": "no MCX contracts in instrument master"}
    selected = valid[0] if str(expiry).upper() in ("AUTO", "CURRENT") else valid[1] if str(expiry).upper() == "NEXT" and len(valid) > 1 else valid[-1]
    row = next((r for r in rows if r["expiry"] == selected), None)
    return {**row, "status": "OK", "available_expiries": valid, "expiry_selection_mode": str(expiry).upper()} if row else {"status": "CONTRACT_INVALID"}


def lookback_window(timeframe: str, bars: int = 120, now: datetime | None = None):
    """Return (fromdate, todate) as Angel-formatted 'YYYY-MM-DD HH:MM' IST strings
    covering roughly `bars` candles ending at the most recent session time.

    If called outside NSE/MCX hours the window is walked back to the previous
    trading session so a request still returns data (weekends included).
    """
    now = (now or datetime.now(timezone.utc)).astimezone(_IST)
    _, mins_per_bar = interval_for(timeframe)
    span = timedelta(minutes=mins_per_bar * max(bars, 20) + 15)

    end = now
    # if before the open, or on a weekend, roll back to a weekday close (15:30 IST)
    def is_weekend(d):
        return d.weekday() >= 5

    open_min = 9 * 60 + 15
    if is_weekend(end):
        while is_weekend(end):
            end -= timedelta(days=1)
        end = end.replace(hour=15, minute=30, second=0, microsecond=0)
    elif end.hour * 60 + end.minute < open_min:
        end = end - timedelta(days=1)
        while is_weekend(end):
            end -= timedelta(days=1)
        end = end.replace(hour=15, minute=30, second=0, microsecond=0)

    start = end - span
    fmt = "%Y-%m-%d %H:%M"
    return start.strftime(fmt), end.strftime(fmt)
