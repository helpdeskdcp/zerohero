"""Dhan App-Based OAuth consent flow (https://dhanhq.co/docs/v2/authentication/,
verified live 2026-09-28) -- for an individual trader's self-generated
app_id/app_secret (Dhan web interface "Access DhanHQ APIs" page), NOT the
separate Partner flow (partner_id/partner_secret, requires Dhan's direct
approval as a third-party platform). The user's credentials here are the
App-scheme kind -- confirmed after /partner/generate-consent returned a real
401 against Dhan's server, then verifying via Dhan's own docs that App and
Partner are two independent schemes with different endpoints and headers.

DHAN_APP_ID / DHAN_APP_SECRET / DHAN_CLIENT_ID are read fresh from the
environment on every call (not cached at import time) so a .env update takes
effect on the next call, no restart needed for this specific piece. Never
logged, never included in an API response body. DHAN_CLIENT_ID is the user's
OWN Dhan account client ID / UCC (required as a query param on step 1 --
Dhan needs to know which account the app is requesting consent for).

The resulting access token IS a real credential (can place real orders on
Dhan) -- stored via app.db.get_setting/set_setting like everything else in
this codebase, but this module itself never calls any order-placing
endpoint. Wiring an actual order path to it is a separate, explicit,
future decision -- same PAPER-only discipline as the rest of this project.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from . import db

_SETTINGS_KEY = "dhan_access_token"
_CONSENT_URL_FMT = "https://auth.dhan.co/app/generate-consent?client_id={client_id}"
_LOGIN_URL_FMT = "https://auth.dhan.co/login/consentApp-login?consentAppId={consent_app_id}"
_CONSUME_URL_FMT = "https://auth.dhan.co/app/consumeApp-consent?tokenId={token_id}"


def _app_headers() -> dict:
    app_id = (os.environ.get("DHAN_APP_ID") or "").strip()
    app_secret = (os.environ.get("DHAN_APP_SECRET") or "").strip()
    if not app_id or not app_secret:
        return {}
    return {"app_id": app_id, "app_secret": app_secret}


def _client_id() -> str:
    return (os.environ.get("DHAN_CLIENT_ID") or "").strip()


def generate_consent() -> dict:
    """Step 1. Returns {"status": "NOT_CONFIGURED"} if DHAN_APP_ID/
    DHAN_APP_SECRET/DHAN_CLIENT_ID aren't all set -- fails closed, never
    guesses. DHAN_CLIENT_ID is the user's own Dhan account client ID (UCC),
    required as a query param so Dhan knows which account to ask consent
    for."""
    headers = _app_headers()
    client_id = _client_id()
    if not headers or not client_id:
        return {"status": "NOT_CONFIGURED",
                "note": "DHAN_APP_ID / DHAN_APP_SECRET / DHAN_CLIENT_ID not all set in .env"}
    url = _CONSENT_URL_FMT.format(client_id=quote(client_id, safe=""))
    resp = requests.post(url, headers=headers, timeout=10)
    if resp.status_code != 200:
        return {"status": "ERROR", "http_status": resp.status_code, "detail": resp.text[:500]}
    body = resp.json()
    consent_app_id = body.get("consentAppId")
    return {"status": "OK", "consent_app_id": consent_app_id,
            "login_url": _LOGIN_URL_FMT.format(consent_app_id=quote(consent_app_id or "", safe=""))}


def consume_consent(token_id: str) -> dict:
    """Step 3, called by the /api/dhan/redirect callback. Stores the
    resulting access token (app_settings) on success -- never returns the
    raw accessToken value to a casual status-check caller (see
    connection_status() below), only this function's own direct return."""
    headers = _app_headers()
    if not headers:
        return {"status": "NOT_CONFIGURED"}
    resp = requests.post(_CONSUME_URL_FMT.format(token_id=quote(token_id, safe="")),
                         headers=headers, timeout=10)
    if resp.status_code != 200:
        return {"status": "ERROR", "http_status": resp.status_code, "detail": resp.text[:500]}
    body = resp.json()
    record = {**body, "stored_at": datetime.now(timezone.utc).isoformat()}
    db.set_setting(_SETTINGS_KEY, json.dumps(record))
    return {"status": "OK", "dhan_client_id": body.get("dhanClientId"),
            "dhan_client_name": body.get("dhanClientName"), "expiry_time": body.get("expiryTime")}


def connection_status() -> dict:
    """Read-only status -- deliberately omits the raw accessToken from the
    response even though it's stored, so a casual GET /status call (which
    might be screen-shared, logged by a proxy, etc.) never leaks it."""
    raw = db.get_setting(_SETTINGS_KEY)
    if not raw:
        return {"connected": False}
    try:
        record = json.loads(raw)
    except Exception:
        return {"connected": False, "note": "stored record is corrupt"}
    ts = token_state(record=record)
    # `connected` keeps its historical meaning ("a token record is stored") because the
    # dashboard keys its NOT CONNECTED vs EXPIRED pill off it. It is NOT a health signal:
    # use `authenticated` / `token_state` (a stored but expired token is never "healthy").
    return {"connected": True, "dhan_client_id": record.get("dhanClientId"),
            "dhan_client_name": record.get("dhanClientName"),
            "expiry_time": record.get("expiryTime"), "stored_at": record.get("stored_at"),
            "token_state": ts["state"], "authenticated": ts["state"] == "VALID"}


def token_state(now=None, record: dict | None = None) -> dict:
    """VALID / EXPIRED / UNCERTAIN / UNKNOWN / MISSING from the stored expiryTime.

    Dhan's expiryTime is a naive timestamp whose timezone is not documented in the
    response (the dashboard assumes UTC). Both readings are evaluated: past BOTH => EXPIRED
    (certain), before BOTH => VALID (by clock only: a 401 from Dhan always overrides),
    in between => UNCERTAIN. Never raises."""
    from datetime import datetime, timedelta, timezone
    if record is None:
        raw = db.get_setting(_SETTINGS_KEY)
        if not raw:
            return {"state": "MISSING"}
        try:
            record = json.loads(raw)
        except Exception:
            return {"state": "MISSING", "note": "stored record is corrupt"}
    if not record.get("accessToken"):
        return {"state": "MISSING"}
    exp = record.get("expiryTime")
    try:
        naive = datetime.fromisoformat(str(exp).replace("Z", ""))
    except Exception:
        return {"state": "UNKNOWN", "expiry_time": exp}
    now = now or datetime.now(timezone.utc)
    as_utc = naive.replace(tzinfo=timezone.utc)
    as_ist = naive.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
    if now >= max(as_utc, as_ist):
        state = "EXPIRED"
    elif now < min(as_utc, as_ist):
        state = "VALID"
    else:
        state = "UNCERTAIN"
    return {"state": state, "expiry_time": exp,
            "seconds_left_if_utc": int((as_utc - now).total_seconds()),
            "seconds_left_if_ist": int((as_ist - now).total_seconds())}


def get_access_token() -> str | None:
    """For internal use by a FUTURE data/order module -- never exposed via
    an API route. Returns None if never connected."""
    raw = db.get_setting(_SETTINGS_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw).get("accessToken")
    except Exception:
        return None
