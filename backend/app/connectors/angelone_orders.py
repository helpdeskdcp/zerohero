"""
Angel One SmartAPI — low-level order HTTP.

This module is the ONLY place that knows the SmartAPI order URLs and payload
shape. It reuses the auth/session + retry helpers already in `angelone.py`
(TOTP login, JWT cache, `_http` with 5xx/connection retry) and adds nothing
new to requirements.

Every function returns a normalised dict and NEVER raises:

    {"status": "OK", ...}                       success
    {"status": "AUTH_FAILED"|"CONFIG_REQUIRED", ...}   session problem
    {"status": "ERROR", "error": "...", "network": bool}   call failed

`network=True` on an ERROR means the request may or may not have reached the
broker — the caller MUST reconcile (never blind-retry an order).

No call here decides anything. No rate limiting here (that belongs to the
broker adapter). No order is placed unless the caller passes a real payload.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import angelone

_BASE = "https://apiconnect.angelone.in/rest/secure/angelbroking/order/v1"
PLACE_URL = f"{_BASE}/placeOrder"
MODIFY_URL = f"{_BASE}/modifyOrder"
CANCEL_URL = f"{_BASE}/cancelOrder"
ORDERBOOK_URL = f"{_BASE}/getOrderBook"
TRADEBOOK_URL = f"{_BASE}/getTradeBook"
DETAILS_URL = f"{_BASE}/details"          # + /{uniqueorderid}
LOGOUT_URL = "https://apiconnect.angelone.in/rest/secure/angelbroking/user/v1/logout"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _auth_headers():
    """(status, headers_or_None). Forces a login if the JWT isn't cached."""
    status, jwt, err = angelone._get_jwt()
    if status != "OK":
        return status, None
    creds = angelone._creds()
    headers = dict(angelone._HEADERS_BASE)
    headers["X-PrivateKey"] = creds["api_key"]
    headers["Authorization"] = f"Bearer {jwt}"
    return "OK", headers


def _post(url: str, body: dict, *, timeout: int = 7) -> dict:
    st, headers = _auth_headers()
    if st != "OK":
        return {"status": st, "error": "session unavailable", "network": False, "ts": _now()}
    try:
        resp = angelone._http("POST", url, json=body, headers=headers, timeout=timeout)
    except Exception as e:                       # connection error / all retries exhausted
        return {"status": "ERROR", "error": f"{type(e).__name__}: {str(e)[:80]}",
                "network": True, "ts": _now()}
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        return {"status": "ERROR", "error": f"non-JSON response (HTTP {resp.status_code})",
                "network": True, "ts": _now()}
    if resp.status_code >= 500:
        return {"status": "ERROR", "error": f"HTTP {resp.status_code}", "network": True,
                "raw": data, "ts": _now()}
    if not isinstance(data, dict):
        return {"status": "ERROR", "error": "non-object body", "network": True, "ts": _now()}
    if data.get("status") is True:
        return {"status": "OK", "data": data.get("data") or {}, "message": data.get("message"),
                "raw": data, "ts": _now()}
    return {"status": "REJECTED", "error": str(data.get("message") or data.get("errorcode") or "")[:120],
            "errorcode": data.get("errorcode"), "raw": data, "network": False, "ts": _now()}


def _get(url: str, *, timeout: int = 7) -> dict:
    st, headers = _auth_headers()
    if st != "OK":
        return {"status": st, "error": "session unavailable", "network": False, "ts": _now()}
    try:
        resp = angelone._http("GET", url, headers=headers, timeout=timeout)
        data = resp.json() if resp.content else {}
    except Exception as e:
        return {"status": "ERROR", "error": f"{type(e).__name__}: {str(e)[:80]}",
                "network": True, "ts": _now()}
    if resp.status_code >= 500:
        return {"status": "ERROR", "error": f"HTTP {resp.status_code}", "network": True, "ts": _now()}
    if not isinstance(data, dict) or data.get("status") is not True:
        return {"status": "ERROR", "error": str((data or {}).get("message") or "bad response")[:120],
                "network": False, "raw": data, "ts": _now()}
    return {"status": "OK", "data": data.get("data"), "raw": data, "ts": _now()}


# ---------------------------------------------------------------- order actions
def place_order(params: dict) -> dict:
    """params is the SmartAPI placeOrder body, e.g.:
        {"variety":"NORMAL","tradingsymbol":"NATURALGAS25AUGFUT","symboltoken":"...",
         "transactiontype":"BUY","exchange":"MCX","ordertype":"MARKET",
         "producttype":"INTRADAY","duration":"DAY","quantity":"1",
         "price":"0","triggerprice":"0"}
    On OK, data carries {orderid, uniqueorderid, script?}."""
    return _post(PLACE_URL, params)


def modify_order(params: dict) -> dict:
    """Requires at least {variety, orderid} plus the fields being changed."""
    return _post(MODIFY_URL, params)


def cancel_order(orderid: str, variety: str = "NORMAL") -> dict:
    return _post(CANCEL_URL, {"variety": variety, "orderid": str(orderid)})


def order_book() -> dict:
    """All orders for the day. data is a list of order dicts."""
    return _get(ORDERBOOK_URL)


def trade_book() -> dict:
    return _get(TRADEBOOK_URL)


def order_details(unique_order_id: str) -> dict:
    """Single-order status by uniqueorderid (preferred — survives across the day
    and is unambiguous). data carries orderstatus / filledshares / averageprice."""
    return _get(f"{DETAILS_URL}/{unique_order_id}")


def find_in_book(orderid: str = "", unique_order_id: str = "", ordertag: str = "") -> dict:
    """Fallback status lookup: pull the order book and match by id or, when a
    submit was ambiguous and returned no id, by the order tag we sent."""
    ob = order_book()
    if ob.get("status") != "OK":
        return ob
    tag = (ordertag or "")[:20]
    for row in (ob.get("data") or []):
        if (unique_order_id and str(row.get("uniqueorderid")) == str(unique_order_id)) or \
           (orderid and str(row.get("orderid")) == str(orderid)) or \
           (tag and str(row.get("ordertag") or "") == tag):
            return {"status": "OK", "data": row, "ts": _now()}
    return {"status": "NOT_FOUND", "ts": _now()}


def logout() -> dict:
    creds = angelone._creds()
    st, headers = _auth_headers()
    if st != "OK":
        return {"status": st}
    try:
        resp = angelone._http("POST", LOGOUT_URL, json={"clientcode": creds["client_id"]},
                              headers=headers, timeout=6)
        ok = bool(resp.content) and (resp.json() or {}).get("status") is True
    except Exception as e:
        return {"status": "ERROR", "error": str(e)[:80]}
    finally:
        # drop the local session cache regardless — the caller wants to be logged out
        angelone._session_cache.update({"jwt": None, "feed_token": None, "ts": 0})
    return {"status": "OK" if ok else "ERROR"}
