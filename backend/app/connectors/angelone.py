"""
AngelOne SmartAPI connector — login (TOTP) + historical candle fetch.
Ported from AI-ANGELONE-CONNECTOR.json. Never logs or returns the JWT.
Credentials are read from environment variables only (see .env).
"""
import os
import time
import math
import threading
import requests
import pyotp
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

from .. import instruments

LOGIN_URL = "https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword"
POSITION_URL = "https://apiconnect.angelone.in/rest/secure/angelbroking/order/v1/getPosition"
CANDLE_URL = "https://apiconnect.angelone.in/rest/secure/angelbroking/historical/v1/getCandleData"
QUOTE_URL = "https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/quote/"

_HEADERS_BASE = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "X-UserType": "USER",
    "X-SourceID": "WEB",
    "X-ClientLocalIP": "127.0.0.1",
    "X-ClientPublicIP": "127.0.0.1",
    "X-MACAddress": "00:00:00:00:00:00",
}

_session_cache = {"jwt": None, "feed_token": None, "ts": 0}
SESSION_TTL_SEC = 3600  # AngelOne session tokens are valid several hours; be conservative

_RETRIES = 2          # extra attempts on a transient network/5xx error
_RETRY_BACKOFF = 0.4  # seconds, linear

# Reuse the repository-level read-only SDK for market data.  The legacy
# connector remains available for position/order compatibility, but quote and
# candle reads share the same authenticated SDK session when importable.
_sdk_client = None
_sdk_lock = threading.Lock()


def _market_sdk(require_auth=True):
    """Return the shared read-only market-data SDK client with a current
    session, or None when auth is required but unavailable.

    `_sdk_lock` serialises the singleton construction and the JWT / feed-token /
    login-timestamp copy onto it: FastAPI runs sync endpoints in a threadpool,
    so two concurrent requests would otherwise race here and could build the
    client twice or observe a half-updated session triple.
    """
    global _sdk_client
    with _sdk_lock:
        if _sdk_client is None:
            root = str(Path(__file__).resolve().parents[3])
            if root not in sys.path:
                sys.path.insert(0, root)
            from broker.angelone import AngelOneClient
            _sdk_client = AngelOneClient()
        status, jwt, _ = _get_jwt() if require_auth else ("OK", _session_cache.get("jwt"), "")
        if status == "OK":
            _sdk_client.jwt = jwt
            _sdk_client.feed_token = _session_cache.get("feed_token")
            _sdk_client.login_ts = _session_cache.get("ts", 0)
        return _sdk_client if status == "OK" else None


def _freshness_meta(last_t, market):
    """Derive (stale_seconds, market_open, market_status, data_status) from the
    newest candle timestamp.  Shared by the SDK candle path and the legacy
    candle path so both return an identical, fail-closed schema: an unparseable
    or missing timestamp, or an age beyond CHANAKYA_MAX_DATA_AGE_SEC, is
    reported STALE and never silently upgraded to OK.
    """
    stale_sec = None
    try:
        if isinstance(last_t, (int, float)):
            tms = last_t if last_t > 1e12 else last_t * 1000
        else:
            tms = datetime.fromisoformat(str(last_t).replace("Z", "+00:00")).timestamp() * 1000
        if math.isfinite(tms):
            stale_sec = round((time.time() * 1000 - tms) / 1000)
    except Exception:
        stale_sec = None

    from .. import market_calendar
    market_open = market_calendar.market_open_flag(market)

    max_age = float(os.environ.get("CHANAKYA_MAX_DATA_AGE_SEC", "900"))
    data_status = "OK" if (stale_sec is not None and stale_sec <= max_age) else "STALE"
    market_status = "OPEN" if market_open is True else ("CLOSED" if market_open is False else "UNKNOWN")
    return stale_sec, market_open, market_status, data_status


def _http(method: str, url: str, **kw):
    """requests.{get,post} with a small retry on connection errors / 5xx.
    Raises the last exception if all attempts fail (callers already catch)."""
    last = None
    for attempt in range(_RETRIES + 1):
        try:
            resp = requests.request(method, url, timeout=kw.pop("timeout", 10), **kw)
            if resp.status_code >= 500 and attempt < _RETRIES:
                last = RuntimeError(f"HTTP {resp.status_code}")
                time.sleep(_RETRY_BACKOFF * (attempt + 1))
                continue
            return resp
        except requests.RequestException as e:
            last = e
            if attempt < _RETRIES:
                time.sleep(_RETRY_BACKOFF * (attempt + 1))
    raise last


def _creds():
    return {
        "api_key": os.environ.get("ANGEL_API_KEY"),
        "client_id": os.environ.get("ANGEL_CLIENT_ID"),
        "password": os.environ.get("ANGEL_PASSWORD"),
        "totp_secret": os.environ.get("ANGEL_TOTP_SECRET"),
    }


def _login():
    """Returns (status, jwt_or_None, error_code)."""
    creds = _creds()
    if not all(creds.values()):
        return "CONFIG_REQUIRED", None, ""
    try:
        totp = pyotp.TOTP(creds["totp_secret"]).now()
    except Exception:
        return "CONFIG_REQUIRED", None, "bad_totp_secret"

    headers = dict(_HEADERS_BASE)
    headers["X-PrivateKey"] = creds["api_key"]
    body = {
        "clientcode": creds["client_id"],
        "password": creds["password"],
        "totp": totp,
    }
    try:
        resp = _http("POST", LOGIN_URL, json=body, headers=headers)
        data = resp.json() if resp.content else {}
    except Exception:
        return "AUTH_FAILED", None, "network_error"

    if data.get("status") is True and data.get("data", {}).get("jwtToken"):
        jwt = data["data"]["jwtToken"]
        _session_cache["jwt"] = jwt
        _session_cache["feed_token"] = data["data"].get("feedToken")
        _session_cache["ts"] = time.time()
        return "OK", jwt, ""

    code = data.get("errorcode") or data.get("error_type") or ""
    missing = not data or "status" not in data
    return ("CONFIG_REQUIRED" if missing else "AUTH_FAILED"), None, str(code)[:40]


def _get_jwt():
    if _session_cache["jwt"] and (time.time() - _session_cache["ts"]) < SESSION_TTL_SEC:
        return "OK", _session_cache["jwt"], ""
    return _login()


def get_stream_credentials():
    """(status, {jwt, feed_token, api_key, client_code}) for the WebSocket feed.
    Forces a login if the feed token isn't cached yet."""
    status, jwt, err = _get_jwt()
    if status != "OK":
        return status, None
    if not _session_cache.get("feed_token"):
        s2, _, _ = _login()
        if s2 != "OK":
            return s2, None
    c = _creds()
    return "OK", {
        "jwt": _session_cache["jwt"],
        "feed_token": _session_cache.get("feed_token"),
        "api_key": c["api_key"],
        "client_code": c["client_id"],
    }


def fetch_candles(market, symbol, exchange, symboltoken, interval, fromdate, todate, timeframe=None, instrument=None):
    """
    Fetch historical candles from AngelOne SmartAPI.
    Returns a normalized dict matching the n8n 'Normalize Candles' contract.
    """
    # --- auto-resolve missing broker params from the instrument registry ---
    resolved_from = None
    if not symboltoken:
        meta = instruments.resolve(symbol)
        if meta:
            symboltoken = meta.get("symboltoken")
            exchange = exchange or meta.get("exchange")
            market = market or meta.get("market")
            resolved_from = "registry"
    if not interval:
        interval, _ = instruments.interval_for(timeframe)
    if not fromdate or not todate:
        fromdate, todate = instruments.lookback_window(timeframe, bars=150)

    # Canonical SDK market-data path.  Keep the established response shape for
    # downstream engines while sourcing values from the shared adapter —
    # INCLUDING the staleness / market-hours metadata the orchestrator's
    # DATA_VALID / DATA_FRESH gate requires.  A stale SDK read is surfaced as
    # STALE and never silently upgraded to OK (fail-closed).
    try:
        sdk = _market_sdk()
        if sdk and symboltoken and exchange:
            q = sdk.get_candles(exchange, symboltoken, interval or "FIVE_MINUTE", fromdate, todate)
            if q.get("status") in ("OK", "DATA_UNAVAILABLE"):
                mapped = [{"t": c.get("timestamp"), "o": float(c["open"]), "h": float(c["high"]),
                           "l": float(c["low"]), "c": float(c["close"]), "v": float(c["volume"])}
                          for c in (q.get("candles") or []) if all(c.get(k) is not None for k in ("open","high","low","close","volume"))]
                if mapped:
                    last_t = mapped[-1]["t"]
                    stale_sec, market_open, market_status, data_status = _freshness_meta(last_t, market)
                    now_iso = datetime.now(timezone.utc).isoformat()
                    return {"market": market, "symbol": symbol, "instrument": instrument,
                            "timeframe": timeframe, "exchange": exchange, "symboltoken": symboltoken,
                            "interval": interval, "fromdate": fromdate, "todate": todate,
                            "resolved_from": resolved_from,
                            "source": "ANGELONE_SDK", "data_status": data_status, "candles": mapped,
                            "candle_count": len(mapped), "data_timestamp": last_t,
                            "stale_seconds": stale_sec, "data_age_seconds": stale_sec,
                            "fetched_at": now_iso, "server_timestamp": now_iso,
                            "snapshot_id": f"{(market or 'UNKNOWN').upper()}-{str(symbol or '').upper()}-{int(time.time()*1000)}",
                            "market_status": market_status, "market_open": market_open}
    except Exception:
        # Fall through to the existing guarded connector path; no strategy or
        # execution behavior is changed by an SDK read failure.
        pass

    def out(data_status, extra=None):
        base = {
            "market": market, "symbol": symbol, "instrument": instrument,
            "timeframe": timeframe, "exchange": exchange, "symboltoken": symboltoken,
            "interval": interval, "fromdate": fromdate, "todate": todate,
            "resolved_from": resolved_from,
            "source": "ANGELONE",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "data_status": data_status,
            "candles": [],
        }
        if extra:
            base.update(extra)
        return base

    if not symboltoken:
        return out("DATA_UNAVAILABLE", {
            "reason": (f"FACT: '{symbol}' is not in the instrument registry and no symboltoken "
                       f"was supplied. Add it via POST /api/instruments "
                       f"(name, exchange, symboltoken) or pass symboltoken explicitly.")
        })

    status, jwt, err = _get_jwt()
    if status != "OK":
        reason = (
            f"FACT: Angel One authentication rejected (code {err or 'n/a'}). Check credentials."
            if status == "AUTH_FAILED"
            else "FACT: Angel One credentials not configured. Set up the Angel One SmartAPI + TOTP credentials to enable live data."
        )
        return out(status, {"reason": reason})

    creds = _creds()
    headers = dict(_HEADERS_BASE)
    headers["X-PrivateKey"] = creds["api_key"]
    headers["Authorization"] = f"Bearer {jwt}"
    body = {"exchange": exchange, "symboltoken": symboltoken, "interval": interval,
             "fromdate": fromdate, "todate": todate}

    try:
        resp = _http("POST", CANDLE_URL, json=body, headers=headers)
        res = resp.json() if resp.content else {}
    except Exception:
        return out("DATA_UNAVAILABLE", {"reason": "FACT: network error contacting broker"})

    if not isinstance(res, dict):
        return out("DATA_UNAVAILABLE", {"reason": "FACT: non-object broker response"})
    if res.get("status") is False:
        return out("DATA_UNAVAILABLE", {"reason": f"FACT: broker status=false ({str(res.get('errorcode',''))[:40]})"})
    data = res.get("data")
    if not isinstance(data, list):
        return out("DATA_UNAVAILABLE", {"reason": "FACT: broker data is not an array"})
    if len(data) == 0:
        return out("DATA_UNAVAILABLE", {"reason": "FACT: broker returned zero candles"})

    mapped = []
    for row in data:
        if not isinstance(row, list) or len(row) < 6:
            continue
        try:
            o, h, l, c, v = float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
        except (TypeError, ValueError):
            continue
        mapped.append({"t": row[0], "o": o, "h": h, "l": l, "c": c, "v": v})

    if not mapped:
        return out("DATA_UNAVAILABLE", {"reason": "FACT: all candle rows malformed"})

    last_t = mapped[-1]["t"]
    stale_sec, market_open, market_status, data_status = _freshness_meta(last_t, market)
    return out(data_status, {
        "candles": mapped,
        "candle_count": len(mapped),
        "stale_seconds": stale_sec,
        "data_timestamp": last_t,
        "data_age_seconds": stale_sec,
        "server_timestamp": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": f"{(market or 'UNKNOWN').upper()}-{str(symbol or '').upper()}-{int(time.time()*1000)}",
        "market_status": market_status,
        "market_open": market_open,
    })


def fetch_positions() -> dict:
    """Live net positions from Angel One SmartAPI (getPosition).

    Returns {"status": "OK"|..., "positions": [ {normalised} ], "raw_count": n}.
    Each normalised position: symbol, symboltoken, exchange, option_type, strike,
    expiry, net_qty (signed), direction, avg_price, ltp, lot_size, product.
    Never raises — a failure returns status != "OK" and an empty list.
    """
    status, jwt, err = _get_jwt()
    if status != "OK":
        return {"status": status, "error": err, "positions": []}
    creds = _creds()
    headers = dict(_HEADERS_BASE)
    headers["X-PrivateKey"] = creds["api_key"]
    headers["Authorization"] = f"Bearer {jwt}"
    try:
        resp = _http("GET", POSITION_URL, headers=headers)
        res = resp.json() if resp.content else {}
    except Exception as e:
        return {"status": "DATA_UNAVAILABLE", "error": str(e)[:60], "positions": []}

    if not isinstance(res, dict) or res.get("status") is False:
        return {"status": "DATA_UNAVAILABLE",
                "error": str(res.get("errorcode", ""))[:40] if isinstance(res, dict) else "bad_response",
                "positions": []}
    data = res.get("data") or []
    if not isinstance(data, list):
        data = []

    def _f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    positions, closed = [], []
    for r in data:
        if not isinstance(r, dict):
            continue
        netqty = _f(r.get("netqty")) or 0
        buyqty = _f(r.get("buyqty")) or 0
        sellqty = _f(r.get("sellqty")) or 0
        realised = _f(r.get("pnl")) or _f(r.get("realised"))
        base = {
            "symbol": (r.get("symbolname") or r.get("tradingsymbol") or "").upper(),
            "tradingsymbol": r.get("tradingsymbol"),
            "symboltoken": str(r.get("symboltoken") or ""),
            "exchange": (r.get("exchange") or "").upper(),
            "option_type": (r.get("optiontype") or "").upper(),
            "strike": _f(r.get("strikeprice")),
            "expiry": r.get("expirydate") or "",
            "lot_size": _f(r.get("lotsize")) or 1,
            "product": r.get("producttype") or "",
            "ltp": _f(r.get("ltp")),
        }
        if netqty != 0:
            positions.append({
                **base, "net_qty": netqty,
                "direction": "BUY" if netqty > 0 else "SELL",
                "avg_price": _f(r.get("avgnetprice")) or _f(r.get("totalbuyavgprice")) or _f(r.get("netprice")),
            })
        elif (buyqty and sellqty) and realised is not None:
            # round-turned intraday position — realised P&L worth recording
            closed.append({
                **base, "traded_qty": max(buyqty, sellqty),
                "buy_avg": _f(r.get("buyavgprice")), "sell_avg": _f(r.get("sellavgprice")),
                "realised_pnl": round(realised, 2),
            })
    return {"status": "OK", "positions": positions, "closed": closed, "raw_count": len(data)}


def fetch_market_quote(exchange: str, symboltoken: str, mode: str = "FULL") -> dict:
    """Read-only SmartAPI quote/OI snapshot; never calls an order endpoint."""
    exchange, token = str(exchange or "").upper(), str(symboltoken or "")
    if exchange not in ("NSE", "NFO", "MCX", "BSE") or not token:
        return {"status": "INSTRUMENT_INVALID", "data_status": "DATA_UNAVAILABLE"}
    try:
        sdk = _market_sdk()
        if sdk:
            return {**sdk.get_quote(exchange, token), "exchange": exchange, "symboltoken": token,
                    "source": "ANGELONE_SDK"}
    except Exception:
        pass
    status, jwt, err = _get_jwt()
    if status != "OK":
        return {"status": status, "data_status": status, "reason": err or "authentication required"}
    c = _creds(); headers = dict(_HEADERS_BASE)
    headers["X-PrivateKey"] = c["api_key"]; headers["Authorization"] = f"Bearer {jwt}"
    try:
        resp = _http("POST", QUOTE_URL, json={"mode": mode, "exchangeTokens": {exchange: [token]}}, headers=headers)
        body = resp.json() if resp.content else {}
    except Exception:
        return {"status": "API_ERROR", "data_status": "API_ERROR"}
    if not isinstance(body, dict) or body.get("status") is False:
        return {"status": "API_ERROR", "data_status": "API_ERROR"}
    data = body.get("data") or {}
    rows = data.get("fetched") if isinstance(data, dict) else None
    row = rows[0] if isinstance(rows, list) and rows else {}
    if not row:
        return {"status": "DATA_UNAVAILABLE", "data_status": "DATA_UNAVAILABLE"}
    return {**row, "status": "OK", "exchange": exchange, "symboltoken": token,
            "data_status": "OK", "server_timestamp": datetime.now(timezone.utc).isoformat()}


def fetch_mcx_quote(symbol: str, symboltoken: str, expiry: str | None = None) -> dict:
    q = fetch_market_quote("MCX", symboltoken)
    q.update({"symbol": symbol, "market": "MCX", "expiry": expiry})
    q["oi_status"] = "AVAILABLE" if any(q.get(k) is not None for k in ("opnInterest", "openInterest", "oi")) else "DATA_UNAVAILABLE"
    return q


def fetch_nse_option_chain(symbol: str, contracts: list[dict] | None = None) -> dict:
    """Fetch quotes for instrument-master option tokens; no token discovery/fabrication."""
    rows = []
    for c in contracts or []:
        if not isinstance(c, dict) or not c.get("symboltoken"):
            continue
        q = fetch_market_quote(c.get("exchange") or "NFO", c["symboltoken"])
        if q.get("status") != "OK":
            continue
        rows.append({"strike": c.get("strike"), "expiry": c.get("expiry"),
                     "option_type": c.get("option_type"), "symboltoken": c["symboltoken"],
                     "ltp": q.get("ltp") or q.get("lastTradedPrice"),
                     "oi": q.get("opnInterest") or q.get("openInterest"),
                     "oi_change": q.get("changeinOpenInterest"), "volume": q.get("tradeVolume"),
                     "iv": q.get("iv"), "greeks_source": "BROKER" if q.get("iv") is not None else "UNAVAILABLE"})
    return {"symbol": symbol, "underlying": symbol, "rows": rows,
            "data_status": "OK" if rows else "DATA_UNAVAILABLE",
            "option_chain_status": "OK" if rows else "NOT_SUPPORTED_OR_UNAVAILABLE",
            "snapshot_timestamp": datetime.now(timezone.utc).isoformat()}
