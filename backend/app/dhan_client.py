"""Dhan Partner OAuth consent flow (https://dhanhq.co/docs/v2/authentication/,
verified live 2026-09-28) -- generates a login consent, and exchanges the
tokenId Dhan's redirect sends back for a real access token.

DHAN_PARTNER_ID / DHAN_PARTNER_SECRET are read fresh from the environment on
every call (not cached at import time) so a .env update takes effect on the
next call, no restart needed for this specific piece. Never logged, never
included in an API response body.

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

import requests

from . import db

_SETTINGS_KEY = "dhan_access_token"
_CONSENT_URL = "https://auth.dhan.co/partner/generate-consent"
_LOGIN_URL_FMT = "https://auth.dhan.co/consent-login?consentId={consent_id}"
_CONSUME_URL_FMT = "https://auth.dhan.co/partner/consume-consent?tokenId={token_id}"


def _partner_headers() -> dict:
    partner_id = (os.environ.get("DHAN_PARTNER_ID") or "").strip()
    partner_secret = (os.environ.get("DHAN_PARTNER_SECRET") or "").strip()
    if not partner_id or not partner_secret:
        return {}
    return {"partner_id": partner_id, "partner_secret": partner_secret}


def generate_consent() -> dict:
    """Step 1. Returns {"status": "NOT_CONFIGURED"} if DHAN_PARTNER_ID/
    DHAN_PARTNER_SECRET aren't set -- fails closed, never guesses."""
    headers = _partner_headers()
    if not headers:
        return {"status": "NOT_CONFIGURED",
                "note": "DHAN_PARTNER_ID / DHAN_PARTNER_SECRET not set in .env"}
    resp = requests.post(_CONSENT_URL, headers=headers, timeout=10)
    if resp.status_code != 200:
        return {"status": "ERROR", "http_status": resp.status_code, "detail": resp.text[:500]}
    body = resp.json()
    consent_id = body.get("consentId")
    return {"status": "OK", "consent_id": consent_id,
            "login_url": _LOGIN_URL_FMT.format(consent_id=consent_id)}


def consume_consent(token_id: str) -> dict:
    """Step 3, called by the /api/dhan/redirect callback. Stores the
    resulting access token (app_settings) on success -- never returns the
    raw accessToken value to a casual status-check caller (see
    connection_status() below), only this function's own direct return."""
    headers = _partner_headers()
    if not headers:
        return {"status": "NOT_CONFIGURED"}
    resp = requests.post(_CONSUME_URL_FMT.format(token_id=token_id), headers=headers, timeout=10)
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
    return {"connected": True, "dhan_client_id": record.get("dhanClientId"),
            "dhan_client_name": record.get("dhanClientName"),
            "expiry_time": record.get("expiryTime"), "stored_at": record.get("stored_at")}


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
