"""
Dhan instrument-master resolution -- front-month future + nearest-expiry ATM
option security_ids for NSE index derivatives (NIFTY/BANKNIFTY), refreshed
once per calendar day (IST).

WHY THIS EXISTS: unlike an equity ISIN, an index derivative's security_id
rolls to a brand new contract every expiry. NIFTY/BANKNIFTY weekly options
expire on a specific trading day and are then dead; the front-month FUTURE
rolls monthly. A security_id hand-resolved once (e.g. against today's spot)
goes stale the moment that contract expires -- and the ATM strike itself
drifts as spot moves, even before expiry. This module re-resolves against
Dhan's own published scrip master + this app's live spot price every day,
so the orderflow feed (app/connectors/dhan_ws.py) always subscribes a
currently-tradable, genuinely-near-the-money contract instead of a fixed
one someone picked by hand on a specific date.

KNOWN LIMITATION (documented, not silently papered over): ATM is
recalculated once per calendar day, not continuously intraday. If spot
moves far enough during the day that the strike picked this morning is no
longer near-the-money, this feed keeps tracking that strike until the next
day's refresh. This is a research/observability feed, not a live execution
system -- a full intraday ATM-follower would need to re-subscribe mid-day,
which is a bigger change than this fix.

Source: https://images.dhan.co/api-data/api-scrip-master.csv (Dhan's own
public instrument master, refreshed periodically by Dhan) -- same
"download once, cache per day" pattern already used for AngelOne's
instrument master elsewhere in this codebase.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime, timedelta, timezone

import requests

_log = logging.getLogger("dhan_instruments")

MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100}

_cache: dict = {"day": None, "rows": None}


def today_ist() -> date:
    """IST = UTC+5:30 -- a plain UTC date() would flip a day early for the
    last 5.5h of every IST calendar day, which is exactly the tail of a
    trading session."""
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def _load_master(force: bool = False) -> list[dict]:
    today = today_ist()
    if not force and _cache["rows"] is not None and _cache["day"] == today:
        return _cache["rows"]
    resp = requests.get(MASTER_URL, timeout=60)
    resp.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    _cache["rows"], _cache["day"] = rows, today
    _log.info("dhan_instruments: master reloaded, %d rows (day=%s)", len(rows), today)
    return rows


def _parse_expiry(row: dict) -> date | None:
    raw = (row.get("SEM_EXPIRY_DATE") or "").strip()
    if not raw or raw.startswith("0001"):
        return None
    try:
        return datetime.strptime(raw.split(" ")[0], "%Y-%m-%d").date()
    except ValueError:
        return None


def _matches_symbol(row: dict, symbol: str) -> bool:
    """SM_SYMBOL_NAME is populated for MCX rows but consistently BLANK for
    NSE F&O rows in Dhan's published master (verified live 2026-09-28) --
    SEM_TRADING_SYMBOL's "{symbol}-..." prefix is the one field reliably
    present on every row. The trailing hyphen is required so "NIFTY"
    doesn't also match "NIFTYFPI-..." (a real, different, listed contract)."""
    return (row.get("SEM_TRADING_SYMBOL") or "").startswith(f"{symbol}-")


def resolve_front_month_future(symbol: str, *, exch_id: str = "NSE", segment: str = "D",
                               rows: list[dict] | None = None) -> dict | None:
    """Nearest not-yet-expired FUTIDX/FUTCOM contract for `symbol`."""
    today = today_ist()
    candidates = []
    for row in (rows if rows is not None else _load_master()):
        if row.get("SEM_EXM_EXCH_ID") != exch_id or row.get("SEM_SEGMENT") != segment:
            continue
        if row.get("SEM_INSTRUMENT_NAME") not in ("FUTIDX", "FUTCOM"):
            continue
        if not _matches_symbol(row, symbol):
            continue
        exp = _parse_expiry(row)
        if exp is None or exp < today:
            continue
        candidates.append((exp, row))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    exp, row = candidates[0]
    return {"security_id": row["SEM_SMST_SECURITY_ID"], "expiry": exp.isoformat(),
            "trading_symbol": row.get("SEM_TRADING_SYMBOL")}


def resolve_atm_options(symbol: str, spot: float, *, exch_id: str = "NSE", segment: str = "D",
                        rows: list[dict] | None = None) -> dict | None:
    """Nearest not-yet-expired OPTIDX expiry's CE+PE closest to `spot`."""
    if not spot or spot <= 0:
        return None
    step = STRIKE_STEP.get(symbol)
    if not step:
        return None
    today = today_ist()
    by_expiry: dict[date, list[dict]] = {}
    for row in (rows if rows is not None else _load_master()):
        if row.get("SEM_EXM_EXCH_ID") != exch_id or row.get("SEM_SEGMENT") != segment:
            continue
        if row.get("SEM_INSTRUMENT_NAME") != "OPTIDX":
            continue
        if not _matches_symbol(row, symbol):
            continue
        exp = _parse_expiry(row)
        if exp is None or exp < today:
            continue
        by_expiry.setdefault(exp, []).append(row)
    if not by_expiry:
        return None
    nearest_expiry = min(by_expiry)
    target_strike = round(spot / step) * step
    by_strike: dict[float, dict] = {}
    for row in by_expiry[nearest_expiry]:
        try:
            k = float(row["SEM_STRIKE_PRICE"])
        except (TypeError, ValueError):
            continue
        by_strike.setdefault(k, {})[row.get("SEM_OPTION_TYPE")] = row
    if not by_strike:
        return None
    actual_strike = min(by_strike, key=lambda k: abs(k - target_strike))
    pair = by_strike[actual_strike]
    ce, pe = pair.get("CE"), pair.get("PE")
    if not ce or not pe:
        return None
    return {
        "expiry": nearest_expiry.isoformat(), "strike": actual_strike,
        "ce": {"security_id": ce["SEM_SMST_SECURITY_ID"], "trading_symbol": ce.get("SEM_TRADING_SYMBOL")},
        "pe": {"security_id": pe["SEM_SMST_SECURITY_ID"], "trading_symbol": pe.get("SEM_TRADING_SYMBOL")},
    }


def resolve_daily_instruments(symbols: list[str], get_spot) -> tuple[list[dict], dict, list[str]]:
    """For each symbol: front-month future + ATM CE/PE, using get_spot(symbol)
    for the strike calculation. Returns ([{"exchange_segment", "security_id"}, ...],
    meta, incomplete) where meta maps security_id -> {"symbol", "kind", "expiry",
    "trading_symbol"} for logging/diagnostics, and `incomplete` lists symbols
    that didn't get BOTH a future and a full ATM CE/PE pair this call (e.g. a
    transient spot-lookup failure) -- the caller should keep retrying those
    rather than treating a partial result as done for the day. Never raises on
    a single symbol's failure -- skips it and keeps resolving the rest."""
    rows = _load_master()
    out: list[dict] = []
    meta: dict = {}
    incomplete: list[str] = []
    for sym in symbols:
        ok = True
        try:
            fut = resolve_front_month_future(sym, rows=rows)
            if fut:
                out.append({"exchange_segment": "NSE_FNO", "security_id": fut["security_id"]})
                meta[fut["security_id"]] = {"symbol": sym, "kind": "FUTURE", "expiry": fut["expiry"],
                                            "trading_symbol": fut["trading_symbol"]}
            else:
                _log.warning("dhan_instruments: no front-month future resolved for %s", sym)
                ok = False
            spot = get_spot(sym)
            atm = resolve_atm_options(sym, spot, rows=rows) if spot else None
            if atm:
                for leg in ("ce", "pe"):
                    r = atm[leg]
                    out.append({"exchange_segment": "NSE_FNO", "security_id": r["security_id"]})
                    meta[r["security_id"]] = {"symbol": sym, "kind": f"OPTION_{leg.upper()}",
                                              "expiry": atm["expiry"], "strike": atm["strike"],
                                              "trading_symbol": r["trading_symbol"]}
            else:
                _log.warning("dhan_instruments: no ATM options resolved for %s (spot=%s)", sym, spot)
                ok = False
        except Exception as e:
            _log.warning("dhan_instruments: resolving %s raised %s: %s", sym, type(e).__name__, e)
            ok = False
        if not ok:
            incomplete.append(sym)
    return out, meta, incomplete
