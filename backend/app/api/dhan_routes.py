"""
Dhan postback receiver -- READ-ONLY LOGGING, nothing else.

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

Since Dhan can't send our HTTP-Basic/Bearer credentials, this ONE route is
the only one in the app exempted from _AuthGateMiddleware (app/main.py) --
compensating control: the path itself carries a long random secret
(DHAN_POSTBACK_SECRET) that must match exactly, fails closed (401) if that
env var isn't configured, and a wrong/missing secret is logged nowhere
(no payload is read/stored on a secret mismatch).
"""
from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, HTTPException, Request

from .. import db

router = APIRouter(prefix="/api/dhan", tags=["dhan"])

POSTBACK_SECRET = (os.environ.get("DHAN_POSTBACK_SECRET") or "").strip()


def is_postback_path(path: str) -> bool:
    """Used by app/main.py's _AuthGateMiddleware to recognize this one
    exempted route by path shape alone (before any DB/route-matching) --
    kept here, next to the route it protects, not duplicated in main.py."""
    return path.startswith("/api/dhan/postback/")


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
    (plural), NOT under "/postback/..." -- is_postback_path() does a plain
    startswith("/api/dhan/postback/") match, and a same-prefix path here
    (e.g. "/postback/log") would have been silently swept into the same
    auth-exempt bypass meant only for Dhan's own webhook call."""
    return {"rows": db.list_dhan_postbacks(limit=limit)}
