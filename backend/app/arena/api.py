"""Public (token-protected) arena API for the institution-side participant. NOT behind the dashboard login, so it carries its own secret (env ARENA_TOKEN_HUMAN, header X-Arena-Token),
a rate limit, a body-size limit, server-side participant naming and an append-only ledger. Reads market data read-only; no order path. Kill switch: ARENA_PUBLIC=0."""
from __future__ import annotations

import hmac
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request

from . import compare as CMP, contracts as CT, run as R, submit as SU

router = APIRouter(prefix="/api/arena/public", tags=["arena-public"])
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IST = timezone(timedelta(hours=5, minutes=30))
EXCH = {"NIFTY": "NSE", "BANKNIFTY": "NSE", "SENSEX": "BSE", "NATURALGAS": "MCX", "CRUDEOIL": "MCX"}
PUBLIC_STATIC = ("/static/arena.html", "/static/js/arena.js")
_hits: dict = defaultdict(deque); _lock = threading.Lock(); _cache = {"t": 0.0, "v": None}
MAX_BODY, RATE_PER_MIN = 4096, 60


def enabled() -> bool: return os.environ.get("ARENA_PUBLIC", "1").strip().lower() not in ("0", "false", "off", "no")


def is_public_path(path: str) -> bool:
    return enabled() and (path.startswith("/api/arena/public/") or path in PUBLIC_STATIC)


def cfg() -> dict:
    return {"token": (os.environ.get("ARENA_TOKEN_HUMAN") or "").strip(), "name": (os.environ.get("ARENA_HUMAN_NAME") or "INSTITUTION_TRADER").strip(),
            "start": os.environ.get("ARENA_START", "2026-10-26"), "end": os.environ.get("ARENA_END", "2026-11-25"),
            "exclude": {x.strip() for x in (os.environ.get("ARENA_EXCLUDE_IDS") or "").split(",") if x.strip()}}


def _auth(request: Request) -> dict:
    if not enabled(): raise HTTPException(404, "not found")
    c = cfg()
    if not c["token"]: raise HTTPException(503, "arena not configured")
    ip = request.client.host if request.client else "?"; now = time.time()
    with _lock:
        q = _hits[ip]
        while q and now - q[0] > 60: q.popleft()
        if len(q) >= RATE_PER_MIN: raise HTTPException(429, "rate limit")
        q.append(now)
    if not hmac.compare_digest(request.headers.get("x-arena-token", "").encode(), c["token"].encode()): raise HTTPException(401, "bad arena token")
    return c


def mode(c: dict, now: datetime | None = None) -> str:
    d = (now or datetime.now(IST)).strftime("%Y-%m-%d")
    return "PRACTICE" if d < c["start"] else ("ARENA_LIVE" if d <= c["end"] else "ENDED")


_warm = {"t": 0.0}


@router.get("/state")
def state(request: Request):
    c = _auth(request)
    if time.time() - _warm["t"] > 120:                                                  # keep the quote pages hot so contract/strike pickers answer instantly
        _warm["t"] = time.time(); threading.Thread(target=CT.warm, daemon=True).start()
    return {"mode": mode(c), "start": c["start"], "end": c["end"], "participant": c["name"], "now_ist": datetime.now(IST).isoformat(timespec="seconds"), "allowed_underlyings": list(SU.ALLOWED),
            "entry_modes": list(SU.MODES), "capital": R.CAPITAL, "notes": ["PRACTICE entries (before the start date) are stored but never scored.", "Submit BEFORE you enter the trade; the server stamps the time; entries cannot be edited."]}


@router.get("/contracts")
def contracts(request: Request, underlying: str):
    _auth(request); u = underlying.upper()
    if u not in SU.ALLOWED: raise HTTPException(422, f"underlying must be one of {SU.ALLOWED}")
    cap = CT.captured(u)
    return {"underlying": u, "session_day": cap["day"], "expiries": [{"expiry": e, "raw": v["raw"], "strike_min": v["strikes"][0], "strike_max": v["strikes"][-1], "n_strikes": len(v["strikes"])} for e, v in cap["expiries"].items()]}


@router.get("/chain")
def chain(request: Request, underlying: str, option_type: str = "CE", expiry: str | None = None, n: int = 7):
    """Auto-detected ATM / ITM / OTM strikes with last quotes. Without `expiry` the CURRENT (nearest captured) expiry is used; the response lists all captured expiries (current, next, ...)."""
    _auth(request); u = underlying.upper(); ot = option_type.upper()
    if u not in SU.ALLOWED or ot not in ("CE", "PE"): raise HTTPException(422, "bad underlying / option_type")
    cap = CT.captured(u); exps = list(cap["expiries"])
    if not exps: return {"underlying": u, "expiries": [], "problems": [f"no captured quotes for {u} in the latest session"]}
    try: e = SU.norm_expiry(expiry) if expiry else exps[0]
    except SU.SubmissionError as ex: raise HTTPException(422, str(ex))
    out = CT.chain(u, e, ot, max(1, min(int(n), 15)))
    out["expiries"] = [{"expiry": x, "raw": cap["expiries"][x]["raw"], "role": ("current" if i == 0 else "next" if i == 1 else f"+{i}")} for i, x in enumerate(exps)]
    return out


@router.get("/quote")
def quote(request: Request, underlying: str, expiry: str, strike: float, option_type: str):
    _auth(request); u = underlying.upper()
    if u not in SU.ALLOWED: raise HTTPException(422, "unsupported underlying")
    try: e = SU.norm_expiry(expiry)
    except SU.SubmissionError as ex: raise HTTPException(422, str(ex))
    return {"expiry": e, "last": CT.last_quote(u, e, float(strike), option_type.upper())}


@router.post("/submit")
async def submit(request: Request):
    c = _auth(request)
    if int(request.headers.get("content-length") or 0) > MAX_BODY: raise HTTPException(413, "body too large")
    try: body = await request.json()
    except Exception: raise HTTPException(400, "invalid JSON")
    if not isinstance(body, dict): raise HTTPException(400, "object expected")
    m = mode(c)
    if m == "ENDED": raise HTTPException(409, "arena has ended")
    u = str(body.get("underlying", "")).upper()
    if m == "ARENA_LIVE":
        try:
            from .. import market_calendar as mc
            if u in EXCH and not mc.is_trading(EXCH[u]): raise HTTPException(409, f"{EXCH[u]} is not open: scored signals can only be submitted while the market is trading")
        except HTTPException: raise
        except Exception: pass
    chk = None
    try:
        v = SU.validate({**body, "participant": c["name"]})
        chk = CT.check(v["underlying"], v["expiry"], v["strike"], v["option_type"], v["entry"], v["entry_mode"], sl=v["sl"])
    except SU.SubmissionError as e: raise HTTPException(422, str(e))
    except Exception: chk = None                                                       # data unavailable: do not block, report nothing
    if chk and chk["problems"] and m == "ARENA_LIVE": raise HTTPException(422, "unscorable contract: " + "; ".join(chk["problems"]))
    try: rec = SU.submit({**body, "participant": c["name"]}, practice=(m == "PRACTICE"))
    except SU.SubmissionError as e: raise HTTPException(422, str(e))
    _cache["v"] = None
    out = {k: rec[k] for k in ("id", "server_iso", "practice", "underlying", "expiry", "strike", "option_type", "entry_mode", "entry", "sl", "targets", "hash")}
    if chk: out["warnings"] = chk["problems"] + chk["warnings"]; out["last_quote"] = chk["last"]; out["available_expiries"] = chk["available_expiries"]
    return out


@router.post("/amend")
async def amend(request: Request):
    """Management event for an already submitted signal: AMEND_SL | AMEND_TARGETS | TRAIL_SL | EXIT_NOW | CANCEL. Effective from the server timestamp + execution delay, never retroactive."""
    c = _auth(request)
    if int(request.headers.get("content-length") or 0) > MAX_BODY: raise HTTPException(413, "body too large")
    try: body = await request.json()
    except Exception: raise HTTPException(400, "invalid JSON")
    if not isinstance(body, dict): raise HTTPException(400, "object expected")
    m = mode(c)
    if m == "ENDED": raise HTTPException(409, "arena has ended")
    if m == "ARENA_LIVE":
        sig = next((r for r in SU.load() if r.get("type") == "HUMAN_SIGNAL" and r["id"] == str(body.get("signal_id", ""))), None)
        try:
            from .. import market_calendar as mc
            if sig and sig["underlying"] in EXCH and not mc.is_trading(EXCH[sig["underlying"]]): raise HTTPException(409, f"{EXCH[sig['underlying']]} is not open")
        except HTTPException: raise
        except Exception: pass
    try: rec = SU.submit_event(body, c["name"])
    except SU.SubmissionError as e: raise HTTPException(422, str(e))
    _cache["v"] = None
    return {k: rec.get(k) for k in ("id", "signal_id", "action", "sl", "targets", "trail_points", "trail_pct", "server_iso", "practice", "hash")}


@router.get("/ingest/status")
def ingest_status(request: Request):
    """Health of the read-only channel reader (no message contents, no ids)."""
    _auth(request)
    from . import tg_reader
    return tg_reader.public_status()


@router.get("/signals")
def signals(request: Request):
    c = _auth(request)
    recs = SU.load(); evs: dict[str, list] = {}
    day = lambda r: datetime.fromtimestamp(r["server_ts"] + 19800, tz=timezone.utc).strftime("%Y-%m-%d")
    for r in recs:
        if r.get("type") == "HUMAN_EVENT": evs.setdefault(r["signal_id"], []).append({k: r.get(k) for k in ("id", "server_iso", "action", "sl", "targets", "trail_points", "trail_pct", "hash")})
    return {"signals": [{**{k: r.get(k) for k in ("id", "server_iso", "practice", "underlying", "expiry", "strike", "option_type", "entry_mode", "entry", "sl", "targets", "note", "hash")}, "events": evs.get(r["id"], []),
                         "scored": c["start"] <= day(r) <= c["end"] and r["id"] not in c["exclude"], "excluded": r["id"] in c["exclude"]}
                        for r in recs if r.get("type", "HUMAN_SIGNAL") == "HUMAN_SIGNAL"]}


@router.get("/standings")
def standings(request: Request):
    c = _auth(request)
    if time.time() - _cache["t"] > 120 or _cache["v"] is None:
        sigs = R.ai_signals(os.path.join(BASE, "data", "chanakya.db"), c["start"], c["end"]) + R.human_signals(start=c["start"], end=c["end"], exclude_ids=c["exclude"])
        out = R.run(sigs, market_db=os.path.join(BASE, "data", "market_history.db"))
        ai, hu = out.get("ZEROHERO_AI", {"results": [], "summary": {}}), out.get(c["name"], {"results": [], "summary": {}})
        try:
            from ..active_arm import run as _arm
            lo = datetime.fromisoformat(c["start"] + "T00:00:00+05:30").timestamp(); hi = datetime.fromisoformat(c["end"] + "T23:59:59+05:30").timestamp()
            a = _arm.run(market_db=os.path.join(BASE, "data", "market_history.db"), since_ts=lo, until_ts=hi)
            active = {"summary": a["summary"]["all"], "groups": a["summary"]["groups"], "trades_per_day": a["summary"]["trades_per_day"], "verdict": a["summary"]["verdict"], "results": a["results"][-200:]}
        except Exception as e:
            active = {"summary": {}, "groups": {}, "trades_per_day": 0.0, "verdict": "UNAVAILABLE", "results": [], "error": f"{type(e).__name__}"}
        _cache.update(t=time.time(), v={"mode": mode(c), "ai": ai["summary"], "human": hu["summary"], "human_name": c["name"], "active": active, "ai_results": ai["results"][-200:], "human_results": hu["results"][-200:],
                                        "comparison": CMP.compare(ai["results"], hu["results"]), "spread_models": {k: v["source"] for k, v in out.get("_spread_models", {}).items()},
                                        "caveats": ["Identical settlement for both sides (ask in / bid out, 20 s delay, SL/T1, session-end exit, brokerage+STT+charges).", "Snapshots are ~1-3 min apart; SENSEX/MCX may use a modelled spread.",
                                                    "Win rate alone does not decide; primary = net return after costs; differences inside the noise are a DRAW.", "Not a profitability claim."]})
    return _cache["v"]


@router.get("/guide")
def guide(request: Request):
    _auth(request)
    p = os.path.join(BASE, "INSTITUTION_ARENA_GUIDE.md")
    return {"markdown": open(p, encoding="utf-8").read() if os.path.exists(p) else "guide missing"}
