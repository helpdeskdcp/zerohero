"""Public (token-protected) arena API for the institution-side participant. NOT behind the dashboard login, so it carries its own secret (env ARENA_TOKEN_HUMAN, header X-Arena-Token),
a rate limit, a body-size limit, server-side participant naming and an append-only ledger. Reads market data read-only; no order path. Kill switch: ARENA_PUBLIC=0."""
from __future__ import annotations

import hashlib
import hmac
import json
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
EXCH = {"NIFTY": "NSE", "BANKNIFTY": "NSE", "SENSEX": "BSE", "NATURALGAS": "MCX", "CRUDEOIL": "MCX", "GOLDM": "MCX"}
PUBLIC_STATIC = ("/static/arena.html", "/static/js/arena.js", "/static/leaderboard.html", "/static/js/arena_terminal.js", "/static/vendor/lightweight-charts/lightweight-charts.standalone.production.js")
_hits: dict = defaultdict(deque); _lock = threading.Lock(); _cache = {"t": 0.0, "v": None, "by": {}}
MAX_BODY, RATE_PER_MIN = 4096, 900                                   # per IP; many participants may share one office address
_sf: dict = defaultdict(threading.Lock)                                  # single-flight: only ONE request recomputes a stale entry, the others get the previous value (or wait if there is none)
RESULT_CACHE: dict = {}                                                  # final settlement results, reused across refreshes (see run.run)
TTL_SEC = 20                                                             # standings / leaderboard are recomputed at most every 20 s (only OPEN signals are re-settled)


def _stale_all() -> None:
    for k in ("by", "lb", "live"):
        _cache[k] = {n: (0.0, v[1]) for n, v in _cache.get(k, {}).items()}


def enabled() -> bool: return os.environ.get("ARENA_PUBLIC", "1").strip().lower() not in ("0", "false", "off", "no")


def is_public_path(path: str) -> bool:
    return enabled() and (path.startswith("/api/arena/public/") or path in PUBLIC_STATIC)


def _latency_now() -> float:
    """The contest's execution delay right now (20 s, or the shorter broker-like value once rule_effective.json holds it)."""
    from . import settle as _s
    return _s.current_latency()


PARTICIPANTS_FILE = os.path.join(BASE, "data", "arena", "participants.json")
CONTEST_FILE = os.path.join(BASE, "data", "arena", "contest.json")                      # runtime contest dates (start / end / official_start): changed without a restart


def _participants_file() -> dict:
    try: return json.load(open(PARTICIPANTS_FILE, encoding="utf-8"))
    except Exception: return {}


def _contest_file() -> dict:
    try: return json.load(open(CONTEST_FILE, encoding="utf-8"))
    except Exception: return {}


def cfg() -> dict:
    name = (os.environ.get("ARENA_HUMAN_NAME") or "INSTITUTION_TRADER").strip(); tok = (os.environ.get("ARENA_TOKEN_HUMAN") or "").strip()
    parts = {name: tok}
    for x in (os.environ.get("ARENA_EXTRA_PARTICIPANTS") or "").split(","):
        x = x.strip().upper()
        if x and x != name: parts[x] = (os.environ.get("ARENA_TOKEN_" + x) or "").strip()
    pf = _participants_file(); hashes = {k.upper(): v["token_sha256"] for k, v in pf.items() if isinstance(v, dict) and v.get("token_sha256") and k.upper() not in parts}
    display = {k.upper(): (v.get("display") or k) for k, v in pf.items() if isinstance(v, dict)}
    flags = {k.upper(): {"risk_module": bool(v.get("risk_module")), "hidden": bool(v.get("hidden")), "default_lots": (int(v.get("default_lots", 1)) if 1 <= int(v.get("default_lots", 1)) <= SU.MAX_LOTS else 1)} for k, v in pf.items() if isinstance(v, dict)}
    return {"participants": parts, "hashes": hashes, "display": display, "flags": flags, "token": tok, "name": name,
            "start": _contest_file().get("start") or os.environ.get("ARENA_START", "2026-10-26"), "end": _contest_file().get("end") or os.environ.get("ARENA_END", "2026-11-25"),
            "official_start": _contest_file().get("official_start"),
            "exclude": {x.strip() for x in (os.environ.get("ARENA_EXCLUDE_IDS") or "").split(",") if x.strip()}}


def _auth(request: Request) -> dict:
    if not enabled(): raise HTTPException(404, "not found")
    c = cfg()
    if not any(c["participants"].values()) and not c["hashes"]: raise HTTPException(503, "arena not configured")
    ip = request.client.host if request.client else "?"; now = time.time()
    with _lock:
        q = _hits[ip]
        while q and now - q[0] > 60: q.popleft()
        if len(q) >= RATE_PER_MIN: raise HTTPException(429, "rate limit")
        q.append(now)
    given = request.headers.get("x-arena-token", "").encode(); who = None
    for nm, tk in c["participants"].items():
        if tk and hmac.compare_digest(given, tk.encode()): who = nm
    if who is None and given:
        h = hashlib.sha256(given).hexdigest()
        for nm, hv in c["hashes"].items():
            if hmac.compare_digest(h.encode(), hv.encode()): who = nm
    if who is None: raise HTTPException(401, "bad arena token")
    return {**c, "name": who}


def mode(c: dict, now: datetime | None = None) -> str:
    d = (now or datetime.now(IST)).strftime("%Y-%m-%d")
    return "PRACTICE" if d < c["start"] else ("ARENA_LIVE" if d <= c["end"] else "ENDED")


_warm = {"t": 0.0}


@router.get("/state")
def state(request: Request):
    c = _auth(request)
    if time.time() - _warm["t"] > 120:                                                  # keep the quote pages hot so contract/strike pickers answer instantly
        _warm["t"] = time.time(); threading.Thread(target=CT.warm, daemon=True).start()
    return {"mode": mode(c), "start": c["start"], "end": c["end"], "official_start": c.get("official_start"), "phase": ("REHEARSAL" if c.get("official_start") and datetime.now(IST).strftime("%Y-%m-%d") < c["official_start"] else "OFFICIAL"), "participant": c["name"], "risk_module": c["flags"].get(c["name"], {}).get("risk_module", False), "default_lots": c["flags"].get(c["name"], {}).get("default_lots", 1), "max_lots": SU.MAX_LOTS, "now_ist": datetime.now(IST).isoformat(timespec="seconds"), "allowed_underlyings": list(SU.ALLOWED),
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
    try:                                                                                          # someone is looking at this ladder: keep the near-the-money prices ~3-8 s fresh while it is on screen (5 min after the last view)
        from ..histcap import priority
        near = sorted(out.get("rows") or [], key=lambda r: abs((r.get("strike") or 0) - (out.get("atm") or 0)))[:7]
        for r in near: priority.request_watch(u, e, r["strike"], ot, ttl_sec=300)
    except Exception:
        pass
    out["expiries"] = [{"expiry": x, "raw": cap["expiries"][x]["raw"], "role": ("current" if i == 0 else "next" if i == 1 else f"+{i}")} for i, x in enumerate(exps)]
    return out


@router.get("/risk-preview")
def risk_preview(request: Request, underlying: str, option_type: str, strike: float, entry: float, expiry: str | None = None):
    """What ZeroHero's rule-based risk module would set for an entry-only signal: stop from the contract's own recent swing low, targets at 1R / 2R / 3R. Read-only; nothing is recorded."""
    _auth(request); u = underlying.upper(); ot = option_type.upper()
    if u not in SU.ALLOWED or ot not in ("CE", "PE") or not (entry > 0 and strike > 0): raise HTTPException(422, "bad underlying / option_type / strike / entry")
    try: cap = CT.captured(u)["expiries"]
    except Exception: cap = {}                                                                 # quote store unavailable: still answer for an explicit expiry (stop falls back to the no-quote rule)
    exps = list(cap)
    try: e = SU.norm_expiry(expiry) if expiry else (exps[0] if exps else None)
    except SU.SubmissionError as ex: raise HTTPException(422, str(ex))
    if e is None: raise HTTPException(404, "no captured expiry for this underlying")
    from .. import sl_detector
    out = sl_detector.plan_for(u, e, strike, ot, entry, time.time())
    out.update(underlying=u, expiry=e, strike=strike, option_type=ot, entry=entry, contract_captured=bool(strike in (cap.get(e, {}).get("strikes") or [])),
               rule="stop = clip(0.97 x lowest price of this contract in the last 60 min, 55%..85% of entry); targets = entry + 1R, 2R, 3R; no quote -> stop 70% of entry")
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
    if c["flags"].get(c["name"], {}).get("risk_module") and (not body.get("sl") or not body.get("targets")):          # entry-only profile: ZeroHero's rule-based risk module sets what is missing
        try:
            from .. import sl_detector
            e_iso = SU.norm_expiry(str(body.get("expiry", ""))); entry = float(body.get("entry"))
            pl = sl_detector.plan_for(str(body.get("underlying", "")).upper(), e_iso, float(body.get("strike")), str(body.get("option_type", "")).upper(), entry, time.time())
            if not body.get("sl"): body["sl"] = pl["sl"]
            if not body.get("targets"): body["targets"] = [round(round((entry + k * (entry - float(body["sl"]))) / 0.05) * 0.05, 2) for k in (1, 2, 3)]
            body["note"] = (str(body.get("note", "")) + " | stop/targets set by the ZeroHero risk module (1R/2R/3R)").strip(" |")[:300]; body.setdefault("valid_sec", 900)
        except Exception as ex:
            raise HTTPException(422, f"risk module could not set the stop/targets: {ex}")
    if body.get("lots") in (None, ""): body["lots"] = c["flags"].get(c["name"], {}).get("default_lots", 1)             # the profile's default quantity (1 lot unless the profile says otherwise)
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
    _stale_all()                                                                              # a new signal/event: refresh on the next request, but keep serving the previous value meanwhile
    try:
        from ..histcap import priority
        priority.request_watch(rec["underlying"], rec["expiry"], rec["strike"], rec["option_type"])         # dense quotes for this contract until the exchange closes
    except Exception:
        pass
    out = {k: rec[k] for k in ("id", "server_iso", "practice", "underlying", "expiry", "strike", "option_type", "entry_mode", "entry", "sl", "targets", "lots", "hash")}
    if chk: out["warnings"] = chk["problems"] + chk["warnings"]; out["last_quote"] = chk["last"]; out["available_expiries"] = chk["available_expiries"]
    return out


@router.post("/amend")
async def amend(request: Request):
    """Management event for an already submitted signal: AMEND_ENTRY (waiting ABOVE/LIMIT only) | AMEND_SL | AMEND_TARGETS | TRAIL_SL | AMEND_LOTS | EXIT_NOW | CANCEL. Effective from the server timestamp + execution delay, never retroactive."""
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
    if str(body.get("action", "")).upper() in ("AMEND_LOTS", "AMEND_ENTRY"):                              # the quantity / the entry can change only while the trade is NOT active
        sid = str(body.get("signal_id", ""))
        sg = [x for x in R.human_signals(include_practice=True) if x["id"] == sid and x["participant"] == c["name"]]
        if sg:
            rr = R.run(sg, market_db=os.path.join(BASE, "data", "market_history.db"), cache=RESULT_CACHE).get(c["name"], {"results": []})["results"]
            if rr and (rr[0].get("fill") is not None or rr[0].get("status") in ("SETTLED", "CANCELLED")): raise HTTPException(409, "the trade is already active (or closed): the quantity / entry cannot be changed any more")
            if str(body.get("action", "")).upper() == "AMEND_ENTRY" and sg[0].get("entry_mode") not in ("ABOVE", "LIMIT"): raise HTTPException(409, "only a waiting ABOVE / LIMIT order has an entry price to change")
    try: rec = SU.submit_event(body, c["name"])
    except SU.SubmissionError as e: raise HTTPException(422, str(e))
    _stale_all()                                                                              # a new signal/event: refresh on the next request, but keep serving the previous value meanwhile
    return {k: rec.get(k) for k in ("id", "signal_id", "action", "sl", "targets", "trail_points", "trail_pct", "lots", "server_iso", "practice", "hash")}


@router.get("/ingest/status")
def ingest_status(request: Request):
    """Health of the read-only channel reader (no message contents, no ids)."""
    _auth(request)
    from . import tg_reader
    out = tg_reader.public_status()
    try:
        from ..histcap import priority
        out["priority_capture"] = priority._instance.status() if priority._instance else {"enabled": priority.enabled(), "running": False}
    except Exception:
        out["priority_capture"] = {"running": False}
    return out


@router.get("/signals")
def signals(request: Request):
    c = _auth(request)
    recs = SU.load(); evs: dict[str, list] = {}; pm = SU.participant_map(recs)
    day = lambda r: datetime.fromtimestamp(r["server_ts"] + 19800, tz=timezone.utc).strftime("%Y-%m-%d")
    for r in recs:
        if r.get("type") == "HUMAN_EVENT": evs.setdefault(r["signal_id"], []).append({k: r.get(k) for k in ("id", "server_iso", "action", "sl", "targets", "trail_points", "trail_pct", "lots", "hash")})
    return {"signals": [{**{k: r.get(k) for k in ("id", "server_iso", "practice", "underlying", "expiry", "strike", "option_type", "entry_mode", "entry", "sl", "targets", "lots", "note", "hash")}, "events": evs.get(r["id"], []),
                         "scored": c["start"] <= day(r) <= c["end"] and r["id"] not in c["exclude"], "excluded": r["id"] in c["exclude"]}
                        for r in recs if r.get("type", "HUMAN_SIGNAL") == "HUMAN_SIGNAL" and pm.get(r["id"]) == c["name"]]}


@router.get("/standings")
def standings(request: Request):
    c = _auth(request)
    ent = _cache.setdefault("by", {}).get(c["name"])
    if ent is None or time.time() - ent[0] > TTL_SEC:
        lk = _sf[("by", c["name"])]
        if lk.acquire(blocking=False):
            try:
                    sigs = R.ai_signals(os.path.join(BASE, "data", "chanakya.db"), c["start"], c["end"]) + R.human_signals(start=c["start"], end=c["end"], exclude_ids=c["exclude"])
                    out = R.run(sigs, market_db=os.path.join(BASE, "data", "market_history.db"), cache=RESULT_CACHE)
                    ai, hu = out.get("ZEROHERO_AI", {"results": [], "summary": {}}), out.get(c["name"], {"results": [], "summary": {}})
                    try:
                        from ..active_arm import run as _arm
                        lo = datetime.fromisoformat(c["start"] + "T00:00:00+05:30").timestamp(); hi = datetime.fromisoformat(c["end"] + "T23:59:59+05:30").timestamp()
                        a = _arm.run(market_db=os.path.join(BASE, "data", "market_history.db"), since_ts=lo, until_ts=hi)
                        active = {"summary": a["summary"]["all"], "groups": a["summary"]["groups"], "trades_per_day": a["summary"]["trades_per_day"], "verdict": a["summary"]["verdict"], "results": a["results"][-200:]}
                    except Exception as e:
                        active = {"summary": {}, "groups": {}, "trades_per_day": 0.0, "verdict": "UNAVAILABLE", "results": [], "error": f"{type(e).__name__}"}
                    _cache["by"][c["name"]] = (time.time(), {"mode": mode(c), "ai": ai["summary"], "human": hu["summary"], "human_name": c["name"], "active": active, "ai_results": ai["results"][-200:], "human_results": hu["results"][-200:],
                                                    "comparison": CMP.compare(ai["results"], hu["results"]), "spread_models": {k: v["source"] for k, v in out.get("_spread_models", {}).items()},
                                                    "caveats": [f"Identical settlement for both sides (ask in / bid out, {int(_latency_now())} s delay, SL/T1, session-end exit, brokerage+STT+charges).", "Contracts with a signal are captured every few seconds, all others every 1-3 minutes; a missing bid/ask uses a modelled spread.",
                                                                "Win rate alone does not decide; primary = net return after costs; differences inside the noise are a DRAW.", "Not a profitability claim."]})
            finally:
                lk.release()
        elif ent is None:
            with lk: pass                                                                       # first request ever: wait for the in-flight computation
    return _cache["by"][c["name"]][1]


@router.get("/chart")
def chart(request: Request, underlying: str, expiry: str, strike: float, option_type: str = "CE", tf: int = 1, since: float | None = None):
    """Terminal tab: candles (tf minutes) of one option contract from the captured quotes of today's session; `since` = UTC epoch of the newest candle the client already has (incremental refresh)."""
    _auth(request); u = underlying.upper(); ot = option_type.upper()
    if u not in SU.ALLOWED or ot not in ("CE", "PE") or tf not in (1, 3, 5, 15): raise HTTPException(422, "bad underlying / option_type / tf")
    try: e = SU.norm_expiry(expiry)
    except SU.SubmissionError as ex: raise HTTPException(422, str(ex))
    from . import terminal as TM
    try:                                                                                          # the contract is on someone's screen: keep its quotes ~3 s fresh (same mechanism as the chain ladder), 5 min after the last look
        from ..histcap import priority
        priority.request_watch(u, e, float(strike), ot, ttl_sec=300)
    except Exception:
        pass
    return TM.premium(u, e, float(strike), ot, int(tf), since)


@router.get("/chart/underlying")
def chart_underlying(request: Request, underlying: str, tf: int = 15):
    """Terminal tab: the underlying's candles with the Winners Scalper (Pine) indicator (trailing stop, BUY/SELL markers) and the live spot."""
    _auth(request); u = underlying.upper()
    if u not in SU.ALLOWED or tf not in (5, 15): raise HTTPException(422, "bad underlying / tf")
    from . import terminal as TM
    return TM.underlying_view(u, int(tf))


@router.get("/live")
def live(request: Request):
    """Broker-style watch of the caller's signals of TODAY: WAITING (not filled yet) / ACTIVE (live ltp, bid/ask, open P&L) / CLOSED (final) / EXPIRED / CANCELLED. The settlement part is cached 5 s, the quote overlay is ~1 s fresh."""
    c = _auth(request); name = c["name"]; today = datetime.now(IST).strftime("%Y-%m-%d"); now = time.time()
    ent = _cache.setdefault("live", {}).get(name)
    if ent is None or now - ent[0] > 5:
        lk = _sf[("live", name)]
        if lk.acquire(blocking=False):
            try:
                day = lambda s: datetime.fromtimestamp(s["t"], tz=IST).strftime("%Y-%m-%d")
                sigs = [s for s in R.human_signals(start=c["start"], end=c["end"], exclude_ids=c["exclude"]) if s["participant"] == name and day(s) == today]
                res = {r["id"]: r for r in R.run(sigs, market_db=os.path.join(BASE, "data", "market_history.db"), cache=RESULT_CACHE).get(name, {"results": []})["results"]} if sigs else {}
                _cache["live"][name] = (time.time(), [(s, res.get(s["id"], {})) for s in sigs]); ent = _cache["live"][name]
            finally:
                lk.release()
        elif ent is None:
            with lk: pass
            ent = _cache["live"].get(name)
    base = ent[1] if ent else []
    from . import settle as _S
    now = time.time(); rows = []
    try:                                                                                             # contracts under dense capture are polled every ~3 s: "checked N s ago" is what matters on a quiet market
        from ..histcap import priority as _pr
        chk = (now - _pr._instance.last_cycle) if (_pr._instance and _pr._instance.last_cycle) else None
    except Exception:
        chk = None
    for s, r in base:
        q = CT.latest_quote(s["underlying"], s["expiry"], s["strike"], s["option_type"]) if s.get("expiry") else None
        st = str(r.get("status", "")); label = f"{s['underlying']} {s['strike']:g} {s['option_type']} ({s['expiry'][8:10]}/{s['expiry'][5:7]})"
        row = {"id": s["id"], "underlying": s["underlying"], "expiry": s["expiry"], "strike": s["strike"], "option_type": s["option_type"], "posted_ts": s["t"], "lots": r.get("lots") or s.get("lots") or 1, "label": label, "entry_mode": s["entry_mode"], "entry": s["entry"], "sl": s["sl"], "targets": s["targets"], "ltp": q["ltp"] if q else None, "bid": q["bid"] if q else None, "ask": q["ask"] if q else None,
               "quote_age_s": (round(min(now - q["t"], chk) if chk is not None else now - q["t"], 1) if q else None), "posted_ist": datetime.fromtimestamp(s["t"], tz=IST).strftime("%H:%M:%S")}
        if st == "NOT_TRIGGERED":                                                                     # a waiting order shows its levels AFTER the amendments already in force
            row.update(entry=r.get("entry_now", row["entry"]), sl=r.get("sl_now", row["sl"]), targets=r.get("targets_now", row["targets"]), lots=r.get("lots", row["lots"]))
        if st == "SETTLED":
            row.update(state="CLOSED", fill=r["fill"], exit=r["exit"], why=r["exit_reason"], pnl_rs=round(r["net_rs"], 1), ltp=None, bid=None, ask=None, quote_age_s=None)
        elif st == "CANCELLED": row.update(state="CANCELLED")
        elif st.startswith("NO_DATA:no_exit_snapshot"):
            lot = r.get("lot") or 1; bid = (q or {}).get("bid") or (r.get("last") or {}).get("bid"); fill = r["fill"]
            lots = r.get("lots") or s.get("lots") or 1; pnl = None if not bid else round((bid - fill) * lot * lots - (_S.cost_total_rs(s["underlying"], fill, bid, lot, lots) or 0), 1)
            row.update(state="ACTIVE", fill=fill, sl=r.get("sl_now", s["sl"]), targets=r.get("targets_now", s["targets"]), pnl_rs=pnl, pnl_pct=(round(100 * (bid - fill) / fill, 2) if bid else None), lot=lot,
                       to_sl=(round(bid - r.get("sl_now", s["sl"]), 2) if (bid and r.get("sl_now", s["sl"]) is not None) else None), to_t1=(round((r.get("targets_now") or s["targets"])[0] - bid, 2) if (bid and (r.get("targets_now") or s["targets"])) else None), pnl_note="open P&L = (bid - fill) x lot - estimated costs; the final result follows the contest rules at exit")
        elif st == "NOT_TRIGGERED":
            vu = r.get("valid_until"); still = (now < vu) if vu else True
            try:
                from .. import market_calendar as mc
                still = still and mc.is_trading(EXCH.get(s["underlying"], "NSE"))
            except Exception:
                pass
            need = None
            if q and s["entry_mode"] == "LIMIT" and q.get("ask"): need = f"waiting for the ask to come down to {s['entry']:g} (ask now {q['ask']:g})"
            elif q and s["entry_mode"] == "ABOVE" and q.get("ltp"): need = f"waiting for the price to trade up to {s['entry']:g} (ltp now {q['ltp']:g})"
            row.update(state="WAITING" if still else "EXPIRED", waiting_note=need if still else "not filled inside its validity: expired", valid_until_ist=(datetime.fromtimestamp(vu, tz=IST).strftime("%H:%M:%S") if vu else None))
        elif st == "MISSED": row.update(state="MISSED")
        elif st in ("DATA_GAP", "INVALID_LEVELS"): row.update(state="VOID", why=st)
        elif st.startswith("NO_DATA:no_snapshot_after_signal"): row.update(state="WAITING", waiting_note=f"order is being placed: it reaches the market after the {int(_latency_now())} s execution delay, then fills at the next quote", valid_until_ist=None)
        else: row.update(state="NO DATA", why=st)
        rows.append(row)
    order = {"ACTIVE": 0, "WAITING": 1, "CLOSED": 2, "EXPIRED": 3, "CANCELLED": 3, "MISSED": 3, "VOID": 3}
    rows.sort(key=lambda x: (order.get(x["state"], 4), x["posted_ist"]), reverse=False)
    return {"now_ist": datetime.now(IST).strftime("%H:%M:%S"), "date": today, "rows": rows, "total_open_pnl_rs": round(sum(x.get("pnl_rs") or 0 for x in rows if x["state"] == "ACTIVE"), 1), "realised_today_rs": round(sum(x.get("pnl_rs") or 0 for x in rows if x["state"] == "CLOSED"), 1)}


@router.get("/leaderboard")
def leaderboard(request: Request, scope: str = "today"):
    """Ranking of every participant (the AI included) by net profit after costs. Shows totals only: never another participant's signals. scope = today | all."""
    c = _auth(request)
    if scope not in ("today", "all"): raise HTTPException(422, "scope must be today or all")
    ent = _cache.setdefault("lb", {}).get(scope)
    if ent is None or time.time() - ent[0] > TTL_SEC:
        lk = _sf[("lb", scope)]
        if lk.acquire(blocking=False):
            try:
                    from . import leaderboard as LB
                    sigs = R.ai_signals(os.path.join(BASE, "data", "chanakya.db"), c["start"], c["end"]) + R.human_signals(start=c["start"], end=c["end"], exclude_ids=c["exclude"])
                    try:
                        from ..energy_event import shadow as _ev
                        sigs += _ev.scoring_signals(c["start"], c["end"])                                       # the NG event rule: a separate, disclosed AI line
                    except Exception:
                        pass
                    out = R.run(sigs, market_db=os.path.join(BASE, "data", "market_history.db"), cache=RESULT_CACHE)
                    today = datetime.now(IST).strftime("%Y-%m-%d")
                    names = [n for n in dict.fromkeys(["ZEROHERO_AI", "ZEROHERO_AI_EVENT", *c["participants"], *c["hashes"]]) if not c["flags"].get(n, {}).get("hidden")]       # hidden = test profiles
                    v = LB.build({k: v["results"] for k, v in out.items() if not k.startswith("_")}, names, c["display"], scope=scope, today=today, capital=R.CAPITAL)
                    v.update(mode=mode(c), generated_at=datetime.now(IST).isoformat(timespec="seconds"), you=None)
                    _cache["lb"][scope] = (time.time(), v)
            finally:
                lk.release()
        elif ent is None:
            with lk: pass                                                                       # first request ever: wait for the in-flight computation
    return {**_cache["lb"][scope][1], "you": c["name"]}


@router.get("/guide")
def guide(request: Request):
    _auth(request)
    p = os.path.join(BASE, "INSTITUTION_ARENA_GUIDE.md")
    return {"markdown": open(p, encoding="utf-8").read() if os.path.exists(p) else "guide missing"}


def prewarm(delay: float = 15.0) -> None:
    """After a restart: settle every signal once in the background so the first participant request finds the final results, the measured spreads and the quote pages already warm."""
    time.sleep(delay)
    try:
        c = cfg(); sigs = R.ai_signals(os.path.join(BASE, "data", "chanakya.db"), c["start"], c["end"]) + R.human_signals(start=c["start"], end=c["end"], exclude_ids=c["exclude"])
        try:
            from ..energy_event import shadow as _ev
            sigs += _ev.scoring_signals(c["start"], c["end"])
        except Exception:
            pass
        R.run(sigs, market_db=os.path.join(BASE, "data", "market_history.db"), cache=RESULT_CACHE)
        for u in SU.ALLOWED: CT.captured(u)
    except Exception:
        pass
