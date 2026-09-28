"""
Dhan broker integration -- postback receiver (READ-ONLY LOGGING) + Partner
OAuth consent flow (app/dhan_client.py does the actual Dhan API calls).

Dhan's postback mechanism has NO signature/secret verification of its own
(confirmed against the official v2 API docs, 2026-09-28: the JSON payload is
just a raw POST body, no HMAC header, no shared-secret field). Every
incoming request here is therefore UNTRUSTED external input -- this module
never parses it into a trading decision, never opens/closes/modifies a
paper trade, and is never wired to app/execution/ (LIVE order path) in any
way. It exists purely so real Dhan order-update postbacks are captured and
visible (dhan_postback_log, app.db.list_dhan_postbacks) for the user's own
review, matching this codebase's PAPER-only, "never fabricate, never
silently trust external input" discipline.

Two routes are exempted from _AuthGateMiddleware (app/main.py) -- Dhan's own
servers/redirects can't send our Basic/Bearer credentials:
  - POST /postback/{secret}: compensating control is the long random secret
    in the path itself (DHAN_POSTBACK_SECRET), checked with
    hmac.compare_digest, fails closed if unconfigured.
  - GET /redirect: Dhan's browser-redirect callback (Partner OAuth step 2)
    carries a one-time tokenId that's useless without OUR
    DHAN_PARTNER_ID/SECRET to exchange it (app/dhan_client.py) -- an
    attacker hitting this path with a guessed/fake tokenId gets nothing
    without those, which live only in .env, never in this URL.
"""
from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import db, dhan_client

router = APIRouter(prefix="/api/dhan", tags=["dhan"])

POSTBACK_SECRET = (os.environ.get("DHAN_POSTBACK_SECRET") or "").strip()


def is_auth_exempt_path(path: str) -> bool:
    """Used by app/main.py's _AuthGateMiddleware to recognize the two
    Dhan-facing routes that can't carry our credentials, by path shape
    alone -- kept here, next to the routes they protect, not duplicated in
    main.py."""
    return path.startswith("/api/dhan/postback/") or path == "/api/dhan/redirect"


@router.post("/postback/{secret}")
async def api_dhan_postback(secret: str, request: Request):
    if not POSTBACK_SECRET or not hmac.compare_digest(secret, POSTBACK_SECRET):
        raise HTTPException(status_code=401, detail="invalid postback secret")
    try:
        payload = await request.json()
    except Exception:
        payload = {"_raw_body_unparseable": True}
    if not isinstance(payload, dict):
        payload = {"_raw_body": payload}
    row_id = db.insert_dhan_postback(payload)
    return {"received": True, "id": row_id}


@router.get("/postbacks")
def api_dhan_postback_log(limit: int = 200):
    """Protected by the normal auth gate. Deliberately named "/postbacks"
    (plural), NOT under "/postback/..." -- is_auth_exempt_path() does a plain
    startswith("/api/dhan/postback/") match, and a same-prefix path here
    (e.g. "/postback/log") would have been silently swept into the same
    auth-exempt bypass meant only for Dhan's own webhook call."""
    return {"rows": db.list_dhan_postbacks(limit=limit)}


@router.post("/generate-consent")
def api_dhan_generate_consent():
    """Protected by the normal auth gate -- step 1 of the Partner OAuth
    flow. Returns a login_url for the USER to open in their own browser and
    log into Dhan directly (their credentials go to Dhan, never through
    this app)."""
    return dhan_client.generate_consent()


@router.get("/redirect")
async def api_dhan_redirect(tokenId: str | None = None):
    """Dhan's Partner OAuth step-2 callback -- the exact URL to register as
    the app's "Redirect URL" in Dhan's developer console. Exchanges tokenId
    for a real access token (step 3) and shows a plain human-readable
    result; never returns the raw access token in this response."""
    if not tokenId:
        return HTMLResponse("<p>No tokenId received from Dhan.</p>", status_code=400)
    result = dhan_client.consume_consent(tokenId)
    if result.get("status") != "OK":
        return HTMLResponse(f"<p>Dhan connection failed: {result}</p>", status_code=502)
    return HTMLResponse(
        f"<p>Dhan connected: {result.get('dhan_client_name')} "
        f"(client {result.get('dhan_client_id')}), token valid until {result.get('expiry_time')}. "
        f"You can close this tab.</p>")


@router.get("/connection-status")
def api_dhan_connection_status():
    return dhan_client.connection_status()
