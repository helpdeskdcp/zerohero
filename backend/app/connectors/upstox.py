"""
Upstox connector -- READ-ONLY historical data (Expired Instruments API v2).

NOT an execution broker: it places no orders and implements no BrokerBase.
Mirrors app/connectors/angelone.py (a data connector, not app/execution/*).

Auth model (Upstox API v2): OAuth2 authorization-code flow. The daily access
token is obtained by the user via a browser login and stored in the existing
secure config (.env: UPSTOX_ACCESS_TOKEN). This module never sees the API
secret except when exchanging an auth code, never logs the token or the
Authorization header, and never returns credential/token values from any
function (auth_health() reports booleans only).

Env (existing <BROKER>_<FIELD> convention, cf. ANGEL_*):
  UPSTOX_API_KEY        -- client_id
  UPSTOX_API_SECRET     -- client_secret (used only in exchange_code())
  UPSTOX_ACCESS_TOKEN   -- daily OAuth access token (user-supplied; may be empty)
  UPSTOX_REDIRECT_URI   -- OAuth redirect URI registered with the app
"""
from __future__ import annotations

import logging
import os
import time
from urllib.parse import urlencode

import requests

_log = logging.getLogger(__name__)

BASE = "https://api.upstox.com"
AUTH_DIALOG = BASE + "/v2/login/authorization/dialog"
TOKEN_URL = BASE + "/v2/login/authorization/token"
PROFILE_URL = BASE + "/v2/user/profile"

_RETRIES = 2
_RETRY_BACKOFF = 0.4
_HEADERS_BASE = {"Accept": "application/json"}

# Upstox underlying instrument keys (segment|name)
UNDERLYING_KEYS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
}
INTERVALS = ("1minute", "3minute", "5minute", "15minute", "30minute", "day")


# ---------------------------------------------------------------- creds (never returned)
_ENV_LOADED = False


def _load_env_file():
    """Lazy, dependency-free load of backend/.env for standalone script use.
    No-op when the process already has UPSTOX_* in the environment (e.g. the app
    started via systemd EnvironmentFile). Never logs values."""
    global _ENV_LOADED
    if _ENV_LOADED or os.environ.get("UPSTOX_API_KEY"):
        _ENV_LOADED = True
        return
    p = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    try:
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                if k.startswith("UPSTOX_") and k not in os.environ:
                    os.environ[k] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    _ENV_LOADED = True


def _creds() -> dict:
    _load_env_file()
    return {
        "api_key": (os.environ.get("UPSTOX_API_KEY") or "").strip(),
        "api_secret": (os.environ.get("UPSTOX_API_SECRET") or "").strip(),
        "access_token": (os.environ.get("UPSTOX_ACCESS_TOKEN") or "").strip(),
        "redirect_uri": (os.environ.get("UPSTOX_REDIRECT_URI") or "").strip(),
    }


def _redact(headers: dict) -> dict:
    """Copy of headers with Authorization masked -- for any debug logging."""
    h = dict(headers or {})
    if "Authorization" in h:
        h["Authorization"] = "Bearer ***redacted***"
    return h


# ---------------------------------------------------------------- HTTP (retry, no secret logging)
def _http(method: str, url: str, **kw):
    last = None
    for attempt in range(_RETRIES + 1):
        try:
            resp = requests.request(method, url, timeout=kw.pop("timeout", 15), **kw)
            if resp.status_code >= 500 and attempt < _RETRIES:
                last = RuntimeError(f"HTTP {resp.status_code}")
                time.sleep(_RETRY_BACKOFF * (attempt + 1))
                continue
            return resp
        except requests.RequestException as e:
            last = e
            _log.warning("_http %s %s: %s (attempt %d)", method, url.split("?")[0],
                         type(e).__name__, attempt + 1)
            if attempt < _RETRIES:
                time.sleep(_RETRY_BACKOFF * (attempt + 1))
    raise last


def _authed_get(path: str, params: dict | None = None, *, token: str | None = None) -> requests.Response:
    tok = token if token is not None else _creds()["access_token"]
    if not tok:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN not set -- complete the OAuth login first (see login_url()).")
    headers = dict(_HEADERS_BASE)
    headers["Authorization"] = f"Bearer {tok}"
    url = BASE + path
    return _http("GET", url, headers=headers, params=params or {})


# ---------------------------------------------------------------- OAuth
def login_url(state: str = "zerohero") -> str:
    """Browser URL the user opens to authorise the app. Contains client_id +
    redirect_uri + state only -- NEVER the api_secret."""
    c = _creds()
    q = urlencode({
        "client_id": c["api_key"],
        "redirect_uri": c["redirect_uri"],
        "response_type": "code",
        "state": state,
    })
    return f"{AUTH_DIALOG}?{q}"


def exchange_code(auth_code: str) -> dict:
    """Exchange the OAuth `code` (from the redirect) for a daily access token.
    Returns {ok, expires_hint, error}. The token itself is NOT returned or
    logged -- on success the caller must write it to .env:UPSTOX_ACCESS_TOKEN
    (this module does not modify .env)."""
    c = _creds()
    if not (c["api_key"] and c["api_secret"] and c["redirect_uri"]):
        return {"ok": False, "error": "UPSTOX_API_KEY / UPSTOX_API_SECRET / UPSTOX_REDIRECT_URI not all configured"}
    body = {
        "code": auth_code,
        "client_id": c["api_key"],
        "client_secret": c["api_secret"],
        "redirect_uri": c["redirect_uri"],
        "grant_type": "authorization_code",
    }
    try:
        r = _http("POST", TOKEN_URL, data=body,
                  headers={"Accept": "application/json",
                           "Content-Type": "application/x-www-form-urlencoded"})
    except requests.RequestException as e:
        return {"ok": False, "error": f"network: {type(e).__name__}"}
    if r.status_code != 200:
        # do not echo the response body verbatim (may carry hints) -> status only
        return {"ok": False, "error": f"token endpoint HTTP {r.status_code}"}
    j = r.json()
    got = bool(j.get("access_token"))
    return {"ok": got,
            "error": None if got else "no access_token in response",
            "token_written": False,
            "note": "write the access_token to .env:UPSTOX_ACCESS_TOKEN (chmod 600); it expires ~03:30 IST next day"}


# ---------------------------------------------------------------- health (booleans only, no secrets)
def auth_health() -> dict:
    c = _creds()
    configured = bool(c["api_key"] and c["api_secret"])
    tok_present = bool(c["access_token"])
    out = {
        "broker": "upstox",
        "credentials_configured": configured,
        "access_token_present": tok_present,
        "access_token_valid": None,
        "api_reachable": None,
        "redirect_uri_configured": bool(c["redirect_uri"]),
        "note": "",
    }
    if not configured:
        out["note"] = "Set UPSTOX_API_KEY + UPSTOX_API_SECRET in .env."
        return out
    # reachability probe (unauthenticated GET to the API host) -- cheap, no creds
    try:
        r = _http("GET", BASE + "/v2/login/authorization/dialog", params={"client_id": c["api_key"]},
                  headers=_HEADERS_BASE, timeout=8)
        out["api_reachable"] = r.status_code < 500
    except requests.RequestException:
        out["api_reachable"] = False
    if not tok_present:
        out["note"] = ("No access token. Open login_url() in a browser, sign in, copy ?code= from the "
                       "redirect, run exchange_code(code), and put the token in .env:UPSTOX_ACCESS_TOKEN.")
        return out
    # validity probe: hit the ACTUAL target (expired-instruments) so plan / IP
    # gates are surfaced distinctly, not as a generic "invalid".
    try:
        r = _authed_get("/v2/expired-instruments/expiries",
                        {"instrument_key": UNDERLYING_KEYS["NIFTY"]})
        out["api_reachable"] = True
        j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        code = ((j.get("errors") or [{}])[0].get("errorCode") or "") if isinstance(j, dict) else ""
        if r.status_code == 200:
            out["access_token_valid"] = True
            out["expired_instruments_api"] = "OK"
            out["note"] = "OK"
        elif code == "UDAPI100050":
            out["access_token_valid"] = False
            out["note"] = "token rejected as invalid (UDAPI100050) -- superseded/revoked; generate a fresh token via OAuth."
        elif code == "UDAPI1149":
            out["access_token_valid"] = True            # token is fine; the plan isn't
            out["expired_instruments_api"] = "BLOCKED: requires Upstox Plus plan (UDAPI1149)"
            out["note"] = "Token valid but the Expired Instruments API needs an Upstox Plus subscription (UDAPI1149)."
        elif code == "UDAPI1221":
            out["access_token_valid"] = True
            out["expired_instruments_api"] = "BLOCKED: static-IP allowlist (UDAPI1221)"
            out["note"] = "Token valid but this server's IP must be added to the account's static-IP allowlist (UDAPI1221)."
        elif code == "UDAPI100067":
            out["access_token_valid"] = True
            out["expired_instruments_api"] = "BLOCKED: read-only token not permitted (UDAPI100067)"
            out["note"] = "Read-only token cannot call this endpoint (UDAPI100067)."
        else:
            out["access_token_valid"] = (r.status_code == 200)
            out["note"] = f"unexpected HTTP {r.status_code} / code {code or 'n/a'}."
    except requests.RequestException:
        out["api_reachable"] = False
        out["note"] = "API unreachable during token validation."
    return out


# ---------------------------------------------------------------- Expired Instruments API (read-only)
def get_expiries(instrument_key: str, *, token: str | None = None) -> dict:
    """GET /v2/expired-instruments/expiries -- historical expiry dates for an underlying."""
    r = _authed_get("/v2/expired-instruments/expiries",
                    {"instrument_key": instrument_key}, token=token)
    return {"http_status": r.status_code, "endpoint": "expired-instruments/expiries",
            "request": {"instrument_key": instrument_key},
            "json": (r.json() if r.headers.get("content-type", "").startswith("application/json") else None),
            "text_len": len(r.text or "")}


def get_expired_option_contracts(instrument_key: str, expiry_date: str, *, token: str | None = None) -> dict:
    """GET /v2/expired-instruments/option/contract -- expired CE/PE contracts for one expiry.
    `expiry_date` = YYYY-MM-DD."""
    r = _authed_get("/v2/expired-instruments/option/contract",
                    {"instrument_key": instrument_key, "expiry_date": expiry_date}, token=token)
    return {"http_status": r.status_code, "endpoint": "expired-instruments/option/contract",
            "request": {"instrument_key": instrument_key, "expiry_date": expiry_date},
            "json": (r.json() if r.headers.get("content-type", "").startswith("application/json") else None),
            "text_len": len(r.text or "")}


def get_expired_historical_candles(expired_instrument_key: str, interval: str,
                                   to_date: str, from_date: str, *, token: str | None = None) -> dict:
    """GET /v2/expired-instruments/historical-candle/:key/:interval/:to_date/:from_date
    Dates = YYYY-MM-DD. `interval` in INTERVALS (primary: '1minute')."""
    if interval not in INTERVALS:
        raise ValueError(f"interval must be one of {INTERVALS}")
    from urllib.parse import quote
    path = (f"/v2/expired-instruments/historical-candle/{quote(expired_instrument_key, safe='')}"
            f"/{interval}/{to_date}/{from_date}")
    r = _authed_get(path, token=token)
    return {"http_status": r.status_code,
            "endpoint": "expired-instruments/historical-candle",
            "request": {"expired_instrument_key": expired_instrument_key, "interval": interval,
                        "to_date": to_date, "from_date": from_date},
            "json": (r.json() if r.headers.get("content-type", "").startswith("application/json") else None),
            "text_len": len(r.text or "")}
