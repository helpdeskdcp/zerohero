"""
AngelOne SmartAPI connector — login (TOTP) + historical candle fetch.
Ported from AI-ANGELONE-CONNECTOR.json. Never logs or returns the JWT.
Credentials are read from environment variables only (see .env).
"""
import os
import time
import requests
import pyotp
from datetime import datetime, timezone

from .. import instruments

LOGIN_URL = "https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword"
POSITION_URL = "https://apiconnect.angelone.in/rest/secure/angelbroking/order/v1/getPosition"
CANDLE_URL = "https://apiconnect.angelone.in/rest/secure/angelbroking/historical/v1/getCandleData"

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
    stale_sec = None
    try:
        if isinstance(last_t, (int, float)):
            tms = last_t if last_t > 1e12 else last_t * 1000
        else:
            tms = datetime.fromisoformat(str(last_t).replace("Z", "+00:00")).timestamp() * 1000
        stale_sec = round(time.time() - tms / 1000) if False else round((time.time() * 1000 - tms) / 1000)
    except Exception:
        stale_sec = None

    from datetime import timedelta
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    mins_ist = now_ist.hour * 60 + now_ist.minute
    mkt = (market or "").upper()
    market_open = None
    if mkt == "NSE":
        market_open = (9 * 60 + 15) <= mins_ist <= (15 * 60 + 30)
    elif mkt == "MCX":
        market_open = (9 * 60) <= mins_ist <= (23 * 60 + 30)

    max_age = float(os.environ.get("CHANAKYA_MAX_DATA_AGE_SEC", "900"))
    data_status = "OK" if stale_sec is not None and stale_sec <= max_age else "STALE"
    market_status = "OPEN" if market_open is True else ("CLOSED" if market_open is False else "UNKNOWN")
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
