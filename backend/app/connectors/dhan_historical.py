"""
Dhan Historical Data API -- daily + intraday OHLCV backfill into the shared
market_candles table (app/histcap/store.py -- the SAME table AngelOne's own
capture already writes to; source="DHAN_HISTORICAL" distinguishes these rows
from source="ANGELONE_CANDLES").

RESEARCH DATA PATH ONLY: this module only reads and stores past candles, it
never places or touches an order and is never wired to app/execution/ (LIVE
order path).

Auth: app.dhan_client.get_access_token() (the App-OAuth consent flow's
stored token) as the access-token header.

Contract (https://dhanhq.co/docs/v2/historical-data/ + .../annexure/,
verified live 2026-09-28):
  * Daily:    POST https://api.dhan.co/v2/charts/historical
    body: {securityId, exchangeSegment, instrument, expiryCode?, oi?,
           fromDate: "YYYY-MM-DD", toDate: "YYYY-MM-DD"}
    -- no chunking limit documented; a single call can span years.
  * Intraday: POST https://api.dhan.co/v2/charts/intraday
    body: {securityId, exchangeSegment, instrument, interval (1|5|15|25|60),
           oi?, fromDate: "YYYY-MM-DD HH:MM:SS", toDate: "YYYY-MM-DD HH:MM:SS"}
    -- Dhan caps each call at 90 days; fetch_intraday_max() chunks a longer
    range into <=90-day windows automatically.
  * instrument enum: INDEX, FUTIDX, OPTIDX, EQUITY, FUTSTK, OPTSTK, FUTCOM,
    OPTFUT, FUTCUR, OPTCUR.

Both endpoints return PARALLEL ARRAYS -- {"open":[...], "high":[...],
"low":[...], "close":[...], "volume":[...], "timestamp":[epoch...],
"open_interest":[...]} -- NOT a list of row dicts. to_candle_rows() reshapes
that into the row-dict shape app.histcap.store.HistStore.write_candles
expects. The `timestamp` values ARE genuine, correct UTC epoch seconds --
verified live 2026-09-28 against a known session: NIFTY's 09:15 IST
market-open 1-minute bar's timestamp decodes to exactly 03:45:00 UTC
(09:15 - 5:30 = 03:45), no offset correction needed. (An earlier draft of
this module wrongly assumed IST-as-UTC mis-encoding and subtracted the
offset -- caught by this same live check before it shipped.)
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta, timezone

import requests

_log = logging.getLogger("dhan_historical")

_DAILY_URL = "https://api.dhan.co/v2/charts/historical"
_INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
_MAX_INTRADAY_DAYS = 90
_IST_OFFSET = timedelta(hours=5, minutes=30)


def _headers() -> dict:
    from .. import dhan_client
    token = dhan_client.get_access_token()
    if not token:
        return {}
    return {"Content-Type": "application/json", "access-token": token}


def fetch_daily(security_id: str, exchange_segment: str, instrument: str,
                from_date: str, to_date: str, *, expiry_code: int | None = None,
                oi: bool = False) -> dict:
    """fromDate/toDate: "YYYY-MM-DD". Returns {"status": "OK", **response}
    or {"status": "NOT_CONFIGURED"/"ERROR", ...}."""
    headers = _headers()
    if not headers:
        return {"status": "NOT_CONFIGURED"}
    body = {"securityId": str(security_id), "exchangeSegment": exchange_segment,
            "instrument": instrument, "fromDate": from_date, "toDate": to_date, "oi": oi}
    if expiry_code is not None:
        body["expiryCode"] = expiry_code
    resp = requests.post(_DAILY_URL, headers=headers, json=body, timeout=30)
    if resp.status_code != 200:
        return {"status": "ERROR", "http_status": resp.status_code, "detail": resp.text[:500]}
    return {"status": "OK", **resp.json()}


def fetch_intraday(security_id: str, exchange_segment: str, instrument: str, interval: int,
                   from_date: str, to_date: str, *, oi: bool = False) -> dict:
    """fromDate/toDate: "YYYY-MM-DD HH:MM:SS". Single call -- caller must
    respect Dhan's <=90-day window (see fetch_intraday_max for a chunked,
    longer-range version)."""
    headers = _headers()
    if not headers:
        return {"status": "NOT_CONFIGURED"}
    body = {"securityId": str(security_id), "exchangeSegment": exchange_segment,
            "instrument": instrument, "interval": interval, "oi": oi,
            "fromDate": from_date, "toDate": to_date}
    resp = requests.post(_INTRADAY_URL, headers=headers, json=body, timeout=30)
    if resp.status_code != 200:
        return {"status": "ERROR", "http_status": resp.status_code, "detail": resp.text[:500]}
    return {"status": "OK", **resp.json()}


def fetch_intraday_max(security_id: str, exchange_segment: str, instrument: str, interval: int,
                       from_date: date, to_date: date, *, oi: bool = False,
                       sleep_between_calls: float = 0.5) -> dict:
    """Chunks [from_date, to_date] into <=90-day windows (Dhan's documented
    intraday limit) and merges the parallel-array responses in chronological
    order. Stops and reports the first ERROR/NOT_CONFIGURED chunk rather than
    silently returning a partial merge."""
    merged: dict[str, list] = {"open": [], "high": [], "low": [], "close": [],
                               "volume": [], "timestamp": [], "open_interest": []}
    cursor = from_date
    chunks = 0
    while cursor <= to_date:
        chunk_end = min(cursor + timedelta(days=_MAX_INTRADAY_DAYS - 1), to_date)
        r = fetch_intraday(security_id, exchange_segment, instrument, interval,
                           f"{cursor.isoformat()} 00:00:00", f"{chunk_end.isoformat()} 23:59:59", oi=oi)
        if r.get("status") != "OK":
            return {**r, "chunks_completed": chunks}
        for k in merged:
            merged[k].extend(r.get(k) or [])
        chunks += 1
        cursor = chunk_end + timedelta(days=1)
        if cursor <= to_date and sleep_between_calls:
            time.sleep(sleep_between_calls)
    return {"status": "OK", "chunks_completed": chunks, **merged}


def _epoch_to_bar_start_utc(epoch: int) -> str:
    """Dhan's candle timestamp is a genuine UTC epoch (verified live
    2026-09-28 -- see module docstring) -- a plain, direct conversion,
    matching bar_start's UTC-ISO convention used by every other source
    already in market_candles."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def to_candle_rows(resp: dict, *, instrument_key: str, symbol: str, kind: str, exchange: str,
                   token: str, tf: str, expiry: str | None = None, strike: float | None = None,
                   option_type: str | None = None, source: str = "DHAN_HISTORICAL") -> list[dict]:
    """Reshapes one fetch_daily/fetch_intraday/fetch_intraday_max response's
    parallel arrays into row dicts matching
    app.histcap.store.HistStore.write_candles's expected shape."""
    if resp.get("status") != "OK":
        return []
    ts_list = resp.get("timestamp") or []
    n = len(ts_list)
    received_ts = datetime.now(timezone.utc).isoformat()
    rows = []
    for i in range(n):
        bar_start = _epoch_to_bar_start_utc(int(ts_list[i]))
        session_date_ist = (datetime.fromisoformat(bar_start) + _IST_OFFSET).date().isoformat()
        rows.append({
            "received_ts": received_ts, "instrument_key": instrument_key, "symbol": symbol,
            "kind": kind, "exchange": exchange, "token": token, "expiry": expiry,
            "strike": strike, "option_type": option_type, "tf": tf, "bar_start": bar_start,
            "session_date_ist": session_date_ist,
            "o": _at(resp, "open", i), "h": _at(resp, "high", i), "l": _at(resp, "low", i),
            "c": _at(resp, "close", i), "v": _at(resp, "volume", i),
            "oi": _at(resp, "open_interest", i), "oi_change": None,
            "source": source, "raw_id": None,
        })
    return rows


def _at(resp: dict, key: str, i: int):
    arr = resp.get(key)
    return arr[i] if arr and i < len(arr) else None


def backfill_daily(entries: list[dict], *, years_back: int = 5) -> dict:
    """Pulls the maximum available daily history (Dhan's docs: up to ~5yr,
    no chunking needed for the daily endpoint) for each entry and writes it
    into the shared market_candles table via app.histcap.store.hist_store().

    Each entry: {"symbol", "security_id", "exchange_segment" (WS string,
    e.g. "NSE_FNO"), "instrument" (FUTIDX/OPTIDX/FUTCOM/...), "kind"
    (FUTURE/OPTION/INDEX), "exchange" (NSE/BSE/MCX), "expiry"?, "strike"?,
    "option_type"?}. Never raises on one entry's failure -- records it and
    keeps going.

    Returns {"rows_written": int, "entries_ok": [...], "entries_failed": [...]}."""
    from ..histcap.store import hist_store
    store = hist_store()
    from_date = (date.today() - timedelta(days=365 * years_back)).isoformat()
    to_date = date.today().isoformat()
    rid = store.start_run("DHAN_HISTORICAL_BACKFILL", {"entries": len(entries)}, auth_ok=True)
    total_rows, ok, failed, integ = 0, [], [], {}
    for e in entries:
        sym = e["symbol"]
        try:
            resp = fetch_daily(e["security_id"], e["exchange_segment"], e["instrument"],
                               from_date, to_date)
            if resp.get("status") != "OK":
                failed.append({"symbol": sym, "reason": resp})
                continue
            rows = to_candle_rows(
                resp, instrument_key=f"{e['exchange']}:{e['security_id']}", symbol=sym,
                kind=e["kind"], exchange=e["exchange"], token=str(e["security_id"]), tf="1d",
                expiry=e.get("expiry"), strike=e.get("strike"), option_type=e.get("option_type"))
            with store.transaction() as conn:
                n = store.write_candles(conn, rows, rid, integ)
            total_rows += n
            ok.append({"symbol": sym, "rows": n})
        except Exception as ex:
            failed.append({"symbol": sym, "reason": f"{type(ex).__name__}: {ex}"})
    store.finish_run(rid, instruments_n=len(entries), counts={"candles": total_rows},
                     integrity=integ, errors=[f["symbol"] for f in failed])
    return {"rows_written": total_rows, "entries_ok": ok, "entries_failed": failed}


def backfill_intraday(entries: list[dict], *, interval: int = 1, days_back: int = 90,
                      sleep_between_calls: float = 1.0) -> dict:
    """Same shape/contract as backfill_daily, but for intraday bars via
    fetch_intraday_max (chunked <=90-day windows). `days_back` is
    deliberately NOT "maximum available" (~5yr) by default -- at 1-minute
    resolution that's ~470k bars/instrument for a long-lived INDEX and would
    mean dozens of chunked calls per instrument, which the rate limit
    observed live (Dhan's DH-904, "Too many requests... breaching rate
    limits") makes slow and failure-prone. Callers wanting more history call
    this again with a larger days_back / an explicit date range on
    fetch_intraday_max directly; `sleep_between_calls` (1s default, higher
    than fetch_intraday_max's own 0.5s default) gives more headroom across
    MANY entries in one run, not just within one entry's own chunks."""
    from ..histcap.store import hist_store
    store = hist_store()
    to_date = date.today()
    from_date = to_date - timedelta(days=days_back)
    tf = {1: "1m", 5: "5m", 15: "15m", 25: "25m", 60: "60m"}.get(interval, f"{interval}m")
    rid = store.start_run("DHAN_HISTORICAL_BACKFILL_INTRADAY",
                          {"entries": len(entries), "interval": interval, "days_back": days_back},
                          auth_ok=True)
    total_rows, ok, failed, integ = 0, [], [], {}
    for e in entries:
        sym = e["symbol"]
        try:
            resp = fetch_intraday_max(e["security_id"], e["exchange_segment"], e["instrument"],
                                      interval, from_date, to_date, sleep_between_calls=sleep_between_calls)
            if resp.get("status") != "OK":
                failed.append({"symbol": sym, "reason": resp})
                continue
            rows = to_candle_rows(
                resp, instrument_key=f"{e['exchange']}:{e['security_id']}", symbol=sym,
                kind=e["kind"], exchange=e["exchange"], token=str(e["security_id"]), tf=tf,
                expiry=e.get("expiry"), strike=e.get("strike"), option_type=e.get("option_type"))
            with store.transaction() as conn:
                n = store.write_candles(conn, rows, rid, integ)
            total_rows += n
            ok.append({"symbol": sym, "rows": n, "chunks": resp.get("chunks_completed")})
        except Exception as ex:
            failed.append({"symbol": sym, "reason": f"{type(ex).__name__}: {ex}"})
        if sleep_between_calls:
            time.sleep(sleep_between_calls)
    store.finish_run(rid, instruments_n=len(entries), counts={"candles": total_rows},
                     integrity=integ, errors=[f["symbol"] for f in failed])
    return {"rows_written": total_rows, "entries_ok": ok, "entries_failed": failed}
