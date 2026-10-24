"""/api/chartink/*: the public webhook (own secret in the path, fail-closed) + read-only views."""
from __future__ import annotations

import hmac
import os
import time

from fastapi import APIRouter, HTTPException, Request

from . import parse, store, validate

router = APIRouter(prefix="/api/chartink", tags=["chartink"])
PUBLIC_PREFIX = "/api/chartink/webhook/"
_recent: list[float] = []


def is_public_path(path: str) -> bool:
    return path.startswith(PUBLIC_PREFIX)


def _token() -> str: return os.environ.get("CHARTINK_WEBHOOK_TOKEN", "")


@router.post("/webhook/{token}")
async def webhook(token: str, request: Request):
    secret = _token()
    if not secret or len(secret) < 24: raise HTTPException(503, "webhook not configured")                       # fail closed
    if not hmac.compare_digest(token.encode(), secret.encode()): raise HTTPException(404, "not found")           # same answer as an unknown path
    now = time.time(); _recent[:] = [t for t in _recent if now - t < 60]
    if len(_recent) >= 60: raise HTTPException(429, "too many alerts")
    _recent.append(now); body = await request.body()
    if len(body) > 200_000: raise HTTPException(413, "payload too large")
    p = parse.parse_payload(body, request.headers.get("content-type", ""))
    if "error" in p: raise HTTPException(400, p["error"])
    if not p["symbols"]: return {"ok": True, "stored": False, "reason": "no symbols"}
    return {"ok": True, "stored": True, **store.add_alert(p, body, now)}


@router.get("/alerts")
def alerts(limit: int = 50, scan: str | None = None):
    with store.conn() as c:
        q = "SELECT id, received_iso, session_date, scan_name, alert_name, triggered_at_text, n_symbols FROM alerts" + (" WHERE scan_name=?" if scan else "") + " ORDER BY id DESC LIMIT ?"
        rows = [dict(r) for r in c.execute(q, ((scan, min(limit, 500)) if scan else (min(limit, 500),))).fetchall()]
        for r in rows: r["symbols"] = [dict(x) for x in c.execute("SELECT symbol, trigger_price, status FROM pairs WHERE alert_id=?", (r["id"],)).fetchall()]
    return {"alerts": rows}


@router.get("/scans")
def scans():
    with store.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT scan_name, COUNT(*) alerts, COUNT(DISTINCT session_date) days, SUM(n_symbols) symbols, MAX(received_iso) last FROM alerts GROUP BY scan_name ORDER BY last DESC").fetchall()]
    sides = store.load_scan_sides()
    for r in rows: r["side"] = sides.get(r["scan_name"], {}).get("side", "LONG (default)")
    return {"scans": rows}


@router.post("/scans/side")
async def set_side(request: Request):
    d = await request.json()
    try: return store.set_scan_side(str(d.get("scan_name", "")), str(d.get("side", "")))
    except ValueError as e: raise HTTPException(400, str(e))


@router.get("/validation")
def validation(): return validate.report()


@router.get("/status")
def status():
    with store.conn() as c:
        a = c.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]; last = c.execute("SELECT MAX(received_iso) FROM alerts").fetchone()[0]
        m = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM marks GROUP BY status").fetchall()}
    return {"configured": len(_token()) >= 24, "alerts": a, "last_alert": last, "marks": m}
