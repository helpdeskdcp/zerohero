"""
Chanakya AI — FastAPI backend.
Serves the REST API + the responsive web dashboard (auto-detects
mobile/desktop client-side, single codebase).
"""
import os
import json
import asyncio
import math
import base64
import hmac
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import db
from . import instruments
from . import market_data
from . import combos
from .reversal import detect_reversal
from .orchestrator import run_pipeline
from .scalp_pipeline import run_scalp_pipeline
from .scalper import ScalpRunner
from .engines.signal_engine import run_signal_engine
from .engines.scalp_engine import run_scalp_engine
from .engines.oi_options_engine import run_oi_options_engine
from .engines.risk_engine import run_risk_engine
from .engines.paper_trading import open_trade, close_trade, update_trade_price
from .research import aggregate_research
from .connectors.angel_ws import LTP_MAX_AGE_SEC, is_ltp_fresh

APP_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = APP_ROOT / "frontend"

_log = logging.getLogger("chanakya.api")

app = FastAPI(title="Chanakya AI", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Opt-in bearer auth. If CHANAKYA_API_TOKEN is set, every /api/* call (except
# /api/health) and the /ws upgrade must present it (Authorization: Bearer <t>
# or ?token=<t>). If unset, this is a no-op and the app behaves as before.
API_TOKEN = (os.environ.get("CHANAKYA_API_TOKEN") or "").strip()
ADMIN_USERNAME = os.environ.get("CHANAKYA_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("CHANAKYA_ADMIN_PASSWORD", "admin@1234")


def _token_from(request) -> str:
    a = request.headers.get("authorization", "")
    if a[:7].lower() == "bearer ":
        return a[7:].strip()
    return request.query_params.get("token", "")


def _basic_ok(value: str) -> bool:
    try:
        raw = base64.b64decode(value[6:].strip(), validate=True).decode("utf-8")
        user, password = raw.split(":", 1)
    except Exception:
        return False
    return hmac.compare_digest(user, ADMIN_USERNAME) and hmac.compare_digest(password, ADMIN_PASSWORD)


@app.middleware("http")
async def _auth_gate(request, call_next):
    p = request.url.path
    if p != "/api/health":
        basic = request.headers.get("authorization", "")
        token_ok = API_TOKEN and _token_from(request) == API_TOKEN
        if not token_ok and not (basic.lower().startswith("basic ") and _basic_ok(basic)):
            return JSONResponse({"detail": "unauthorized"}, status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="Chanakya AI"'})
    return await call_next(request)


@app.on_event("startup")
def _startup():
    db.init_db()


# ---------------------------------------------------------------- WebSocket
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()
scalp_runner = ScalpRunner(broadcast=manager.broadcast)

# --- Autonomous PAPER scalper (P7). Shares the ScalpRunner's WS feed; a live
# chain snapshot comes from the read-only market-data SDK. LIVE order routing is
# never reached from here. ---
from .autoscalp.runner import AutoScalpRunner


def _autoscalp_chain(symbol, atm, window, market="NSE", expiry_mode="AUTO"):
    """Canonical ATM+/-window chain from the read-only quote SDK (best effort).
    `market` is NSE for index options, MCX for NATURALGAS/CRUDEOIL options-on-
    futures — selection_snapshot + the SDK are exchange-aware. `expiry_mode`
    is AUTO or AUTO_ROLL (skip the 0-DTE contract on expiry day)."""
    try:
        from .connectors.angelone import _market_sdk
        sdk = _market_sdk(require_auth=False)
        if not sdk:
            return []
        mkt = str(market or "NSE").upper()
        et = 5 if mkt == "MCX" else 2                       # WS exchange type for the legs
        snap = market_data.selection_snapshot(sdk, mkt, symbol, expiry=str(expiry_mode or "AUTO"),
                                              option_type="BOTH", window=window,
                                              instrument="OPTION" if mkt == "MCX" else None)
        expiry = snap.get("expiry")
        out = []
        for r in snap.get("chain") or []:
            strike = r.get("strike")
            out.append({
                "strike": strike,
                "ce": {"ltp": r.get("ce_ltp"), "oi": r.get("ce_oi"), "oi_chg": r.get("ce_oi_change"),
                       "vol_delta": r.get("ce_volume"), "token": r.get("ce_token"), "exchange_type": et,
                       "iv": None, "delta": None, "gamma": None, "theta": None, "vega": None,
                       "tradingsymbol": _opt_tradingsymbol(symbol, expiry, strike, "CE"), "expiry": expiry},
                "pe": {"ltp": r.get("pe_ltp"), "oi": r.get("pe_oi"), "oi_chg": r.get("pe_oi_change"),
                       "vol_delta": r.get("pe_volume"), "token": r.get("pe_token"), "exchange_type": et,
                       "iv": None, "delta": None, "gamma": None, "theta": None, "vega": None,
                       "tradingsymbol": _opt_tradingsymbol(symbol, expiry, strike, "PE"), "expiry": expiry},
            })
        return out
    except Exception:
        return []


def _opt_tradingsymbol(symbol, expiry, strike, opt_type):
    """Best-effort AngelOne NFO trading symbol, e.g. NIFTY01SEP2624200CE.
    Returns None if the expiry string is not the expected DDMMMYYYY form —
    the token is the authoritative contract lock, this is only for display/audit."""
    try:
        e = str(expiry or "").strip().upper()
        if len(e) == 9 and e[:2].isdigit() and e[5:].isdigit():   # 01SEP2026
            return f"{str(symbol).upper()}{e[:5]}{e[7:9]}{int(round(float(strike)))}{opt_type}"
    except Exception:
        pass
    return None


def _autoscalp_tg(text):
    try:
        from .connectors import telegram
        telegram._send(text, os.environ.get("TELEGRAM_CHAT_ID"))
    except Exception:
        pass


autoscalp = AutoScalpRunner(feed=scalp_runner.feed, chain_provider=_autoscalp_chain,
                            broadcast=manager.broadcast, telegram_fn=_autoscalp_tg,
                            owner=f"{os.uname().nodename}:{os.getpid()}")


@app.on_event("startup")
async def _start_scalp_runner():
    scalp_runner.start()
    autoscalp.start()


@app.on_event("shutdown")
async def _stop_scalp_runner():
    await scalp_runner.stop()
    await autoscalp.stop()


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    auth = websocket.headers.get("authorization", "")
    if not ((API_TOKEN and websocket.query_params.get("token") == API_TOKEN)
            or (auth.lower().startswith("basic ") and _basic_ok(auth))):
        await websocket.close(code=1008)
        return
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keepalive ping/pong from client
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ---------------------------------------------------------------- Schemas
class SignalRequest(BaseModel):
    market: Optional[str] = None
    symbol: Optional[str] = None
    instrument: Optional[str] = None
    exchange: Optional[str] = None
    symboltoken: Optional[str] = None
    interval: Optional[str] = None
    fromdate: Optional[str] = None
    todate: Optional[str] = None
    timeframe: Optional[str] = None
    expiry: Optional[str] = None
    strike: Optional[float] = None
    underlying: Optional[str] = None
    spot: Optional[float] = None
    chain: Optional[list] = None
    candles: Optional[list] = None
    signal_config: Optional[dict] = None
    oi_config: Optional[dict] = None
    account: Optional[dict] = None
    risk_instrument: Optional[dict] = None
    state: Optional[dict] = None
    limits: Optional[dict] = None


class CloseTradeRequest(BaseModel):
    trade_id: str
    exit_price: float


class MarkPriceRequest(BaseModel):
    trade_id: str
    ltp: float


# ---------------------------------------------------------------- Engine-level (raw) endpoints
@app.post("/api/engine/signal")
def api_signal_engine(payload: dict):
    return run_signal_engine(payload)


@app.post("/api/engine/oi")
def api_oi_engine(payload: dict):
    return run_oi_options_engine(payload)


@app.post("/api/engine/risk")
def api_risk_engine(payload: dict):
    return run_risk_engine(payload)


# ---------------------------------------------------------------- Full pipeline
@app.post("/api/run")
async def api_run_pipeline(req: SignalRequest):
    try:
        # run_pipeline() makes blocking broker / network calls; run it off the
        # event loop so /ws broadcasts and the in-process ScalpRunner keep
        # ticking while it is in flight.
        result = await asyncio.to_thread(run_pipeline, req.model_dump(exclude_none=True))
        await manager.broadcast({"type": "signal", "data": result.get("contract") or {}})
        if result.get("trade"):
            await manager.broadcast({"type": "trade_open", "data": result["trade"]})
        return result
    except Exception as exc:
        # A pipeline failure (broker outage, malformed payload, or an unexpected
        # bug) must never become an opaque HTTP 500, leak broker credentials or
        # internals to the client, or drop the fail-closed NO_TRADE contract.
        # Log the full traceback server-side; return only a coarse error class.
        _log.exception("api_run_pipeline failed")
        expected = isinstance(exc, (ConnectionError, TimeoutError, OSError))
        return {"contract": {"decision": "NO_TRADE", "approved": False,
                             "final_decision": "NO_TRADE",
                             "data_status": "DATA_UNAVAILABLE",
                             "reason": "MARKET_DATA_UNAVAILABLE"},
                "trade": None, "error": "DATA_UNAVAILABLE",
                "error_class": "UPSTREAM_DATA_UNAVAILABLE" if expected else "INTERNAL_ERROR"}


# ---------------------------------------------------------------- Instrument registry
class InstrumentRequest(BaseModel):
    name: str
    exchange: str
    symboltoken: str
    market: Optional[str] = None
    aliases: Optional[list] = None


@app.get("/api/instruments")
def api_instruments():
    """What the connector can resolve by name (seeds + user additions)."""
    reg = instruments.registry()
    return {
        "instruments": [
            {"name": k, "exchange": v.get("exchange"), "symboltoken": v.get("symboltoken"),
             "market": v.get("market"), "aliases": v.get("aliases") or []}
            for k, v in sorted(reg.items())
        ],
        "timeframes": ["1m", "3m", "5m", "15m", "1h"],
    }


@app.get("/api/market-instruments")
def api_market_instruments(market: str = Query("NSE")):
    """Current valid symbols from the official AngelOne master (read-only)."""
    market = str(market or "NSE").upper()
    if market not in ("NSE", "MCX"):
        return {"market": market, "instruments": [], "data_status": "DATA_UNAVAILABLE"}
    try:
        from .connectors.angelone import _market_sdk
        sdk = _market_sdk(require_auth=False)
        if not sdk:
            return {"market": market, "instruments": [], "data_status": "DATA_UNAVAILABLE"}
        return {"market": market, "instruments": market_data.available_symbols(sdk, market),
                "data_status": "OK", "source": "ANGELONE_SDK"}
    except Exception:
        return {"market": market, "instruments": [], "data_status": "DATA_UNAVAILABLE"}


@app.get("/api/market-selection")
def api_market_selection(market: str = Query("NSE"), symbol: str = Query(...),
                         expiry: str = Query("AUTO"), option_type: str = Query("BOTH"),
                         instrument: Optional[str] = Query(None),
                         window: int = Query(5, ge=0, le=20)):
    """Read-only resolved contract and display snapshot for the Run form."""
    try:
        from .connectors.angelone import _market_sdk
        sdk = _market_sdk(require_auth=False)
        if not sdk:
            return {"status": "DATA_UNAVAILABLE", "data_status": "DATA_UNAVAILABLE",
                    "market": market, "symbol": symbol, "reason": "SDK unavailable"}
        return market_data.selection_snapshot(sdk, market, symbol, expiry=expiry,
                                              option_type=option_type, window=window, instrument=instrument)
    except Exception:
        return {"status": "DATA_UNAVAILABLE", "data_status": "DATA_UNAVAILABLE",
                "market": market, "symbol": symbol, "reason": "market data unavailable"}


@app.post("/api/instruments")
def api_add_instrument(req: InstrumentRequest):
    try:
        added = instruments.add_instrument(
            req.name, req.exchange, req.symboltoken, req.market, req.aliases)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"added": added, "registry": instruments.registry()}


# ---------------------------------------------------------------- Scalping
@app.post("/api/scalp/signal")
def api_scalp_signal(payload: dict):
    """Scalp engine only — raw candles in, scalp decision out."""
    return run_scalp_engine(payload)


@app.post("/api/scalp/run")
async def api_scalp_run(payload: dict):
    """One-shot scalp pipeline: data -> scalp engine -> risk -> gate -> paper trade."""
    result = await asyncio.to_thread(run_scalp_pipeline, payload or {})
    await manager.broadcast({"type": "scalp_signal", "data": result["contract"]})
    if result.get("trade"):
        await manager.broadcast({"type": "scalp_open", "data": result["trade"]})
    return result


def _label_marks(status: dict) -> dict:
    """Add a human-readable `label` to each feed mark (99919000 -> SENSEX).
    Accepts either a runner status (has `feed`) or a bare feed status (has
    `marks`)."""
    fs = status.get("feed") if isinstance(status, dict) and isinstance(status.get("feed"), dict) else status
    marks = (fs or {}).get("marks") if isinstance(fs, dict) else None
    if isinstance(marks, dict):
        for tok, mk in marks.items():
            if isinstance(mk, dict):
                mk.setdefault("label", instruments.label_for_token(tok))
    return status


def _compact(status: dict) -> dict:
    """Shrink a status payload for low-token consumers: collapse per-mark dicts
    to `label: ltp`, replace token arrays with counts, drop the nested config."""
    s = dict(status or {})
    fs = s.get("feed") if isinstance(s.get("feed"), dict) else s
    if isinstance(fs, dict) and isinstance(fs.get("marks"), dict):
        stale = sum(1 for m in fs["marks"].values() if isinstance(m, dict) and not m.get("fresh"))
        fs2 = {k: v for k, v in fs.items() if k not in ("desired_tokens", "active_tokens")}
        fs2["marks"] = {(instruments.label_for_token(t) if str(t).isdigit() else t):
                        round((m or {}).get("ltp", 0), 2) if isinstance(m, dict) else m
                        for t, m in fs["marks"].items()}
        fs2["n_desired"] = len(fs.get("desired_tokens") or [])
        fs2["n_active"] = len(fs.get("active_tokens") or [])
        fs2["stale_marks"] = stale
        if s.get("feed") is fs:
            s["feed"] = fs2
        else:
            s = fs2
    for k in ("config",):
        s.pop(k, None)
    if isinstance(s.get("safeguards"), dict):
        s["safeguards"].pop("config", None)
    return s


@app.get("/api/scalp/status")
def api_scalp_status(compact: bool = False):
    st = scalp_runner.status()
    return _compact(st) if compact else _label_marks(st)


@app.get("/api/scalp/feed")
def api_scalp_feed(compact: bool = False):
    """Angel One WebSocket market-data feed: connection + per-token live marks."""
    st = scalp_runner.feed.status()
    return _compact({"feed": st})["feed"] if compact else _label_marks(st)


@app.get("/api/reversal")
def api_reversal(symbol: str, timeframe: str = "15m"):
    """Resistance→support / support→resistance reversal read for a symbol,
    with a concrete CE/PE + entry / stop / target if a turn is firing."""
    from .connectors import angelone as _a
    conn = _a.fetch_candles(market=None, symbol=symbol, exchange=None, symboltoken=None,
                            interval=None, fromdate=None, todate=None, timeframe=timeframe,
                            instrument="FUT")
    if conn.get("data_status") != "OK":
        return {"symbol": symbol, "data_status": conn.get("data_status"),
                "reason": conn.get("reason"), "reversal": None}
    r = detect_reversal(conn["candles"])
    r["symbol"] = symbol
    r["timeframe"] = timeframe
    return r


@app.get("/api/turning-point")
def api_turning_point(symbol: str, timeframe: str = "5m"):
    """Deterministic turning-point read for a symbol: direction, up/down turn
    zones, next High/Low + Swing zones with probabilities, confidence, expected
    move, and a Risk-Engine-ready trade_ref. Predicts ZONES, not prices."""
    from .connectors import angelone as _a
    from .engines.turning_point_engine import run_turning_point_engine
    from .engines.signal_engine import run_signal_engine
    from . import tp_calibration
    conn = _a.fetch_candles(market=None, symbol=symbol, exchange=None, symboltoken=None,
                            interval=None, fromdate=None, todate=None, timeframe=timeframe,
                            instrument="FUT")
    if conn.get("data_status") != "OK":
        return {"symbol": symbol, "data_status": conn.get("data_status"),
                "reason": conn.get("reason"), "direction": "NO_TURN"}
    sig = run_signal_engine({"symbol": symbol, "timeframe": timeframe, "source": "ANGELONE",
                             "data_status": "OK", "candles": conn["candles"], "config": {}})
    tp = run_turning_point_engine({"candles": conn["candles"], "signal_calc": sig.get("calculations"),
                                   "calibration": tp_calibration.load()})
    tp["symbol"] = symbol
    tp["timeframe"] = timeframe
    return tp


@app.get("/api/turning-point/calibration")
def api_tp_calibration():
    """Current learned sigmoid (k, b) + feature weights + resolved-prediction
    stats. Deterministic: same tp_predictions rows -> same numbers."""
    from . import tp_calibration
    cal = tp_calibration.load()
    with db.db() as conn:
        by_outcome = {r["outcome"] or "UNRESOLVED": r["c"] for r in conn.execute(
            "SELECT outcome, COUNT(*) AS c FROM tp_predictions GROUP BY outcome")}
        recent = [dict(r) for r in conn.execute(
            "SELECT ts,symbol,timeframe,direction,confidence,p_up,outcome,signed_outcome,err_pts "
            "FROM tp_predictions WHERE resolved=1 ORDER BY id DESC LIMIT 25")]
        total = conn.execute("SELECT COUNT(*) AS c FROM tp_predictions").fetchone()["c"]
    hit = sum(v for k, v in by_outcome.items() if k in ("DIRECTION_HIT", "ZONE_HIT", "BOTH"))
    graded = sum(v for k, v in by_outcome.items() if k not in ("UNRESOLVED", "TIMEOUT", None))
    return {"calibration": cal, "predictions_total": total,
            "by_outcome": by_outcome,
            "hit_rate": round(hit / graded, 3) if graded else None,
            "recent_resolved": recent}


@app.get("/api/execution/status")
def api_execution_status():
    """Order-adapter health: mode, kill switch, frozen state, open intents.
    Read-only — there is NO endpoint to submit a live order from the API."""
    from .execution import killswitch
    st = scalp_runner.status()
    ex = st.get("execution") or {}
    counts = {}
    with db.db() as conn:
        for r in conn.execute("SELECT status, COUNT(*) c FROM broker_orders GROUP BY status"):
            counts[r["status"]] = r["c"]
    return {"execution": ex, "kill_switch": killswitch.state(),
            "order_counts": counts, "runner_is_leader": st.get("is_leader")}


@app.get("/api/execution/orders")
def api_execution_orders(trade_id: Optional[str] = None, status: Optional[str] = None,
                         limit: int = Query(200, le=2000)):
    from .execution import audit as _audit
    rows = db.list_broker_orders(trade_id=trade_id, status=status, limit=limit)
    out = {"orders": rows}
    if trade_id:
        out["audit"] = _audit.snapshot(trade_id)
    return out


@app.get("/api/execution/events")
def api_execution_events(trade_id: Optional[str] = None, limit: int = Query(300, le=3000)):
    return {"events": db.list_order_events(trade_id=trade_id, limit=limit)}


class KillSwitchRequest(BaseModel):
    active: bool
    policy: Optional[str] = None       # MONITOR | FLATTEN
    reason: Optional[str] = "api"


@app.post("/api/execution/kill")
def api_execution_kill(req: KillSwitchRequest):
    """Global emergency kill switch. active=true blocks all new entries / auto
    re-entry; existing positions stay monitored. `policy` sets the explicit
    emergency-exit behaviour (MONITOR = alert only, FLATTEN = allow auto exits
    on confirmed LIVE positions)."""
    from .execution import killswitch
    if req.policy:
        killswitch.set_policy(req.policy)
    state = killswitch.activate(req.reason or "api") if req.active else killswitch.deactivate(req.reason or "api")
    return {"kill_switch": state}


@app.get("/api/monitor")
def api_monitor():
    """One-shot snapshot for the Live Monitor page: runner health, live feed
    marks, open positions/scalps with live P&L + distance-to-target/stop, and
    the most recent signals. Deltas thereafter arrive over the WebSocket."""
    st = scalp_runner.status()
    feed_marks = (st.get("feed") or {}).get("marks") or {}

    def enrich(rows):
        out = []
        for r in rows:
            m = feed_marks.get(str(r.get("symboltoken") or ""))
            entry = r.get("entry") or 0
            qty = r.get("quantity") or 0
            direction = r.get("direction")
            direction_valid = direction in ("BUY", "SELL")
            sign = 1 if direction == "BUY" else -1
            age = m.get("age_sec") if isinstance(m, dict) else None
            fresh = is_ltp_fresh(age)
            raw_mark = m.get("ltp") if isinstance(m, dict) else None
            try:
                mark_is_valid = math.isfinite(float(raw_mark)) and float(raw_mark) > 0
            except (TypeError, ValueError):
                mark_is_valid = False
            # A cached or malformed quote must not be re-labelled as a live
            # REST mark.  There is no timestamp on persisted P&L, so it is
            # deliberately unavailable for live-monitor calculations.
            mark = float(raw_mark) if fresh and mark_is_valid else None
            mark_src = "ws" if mark is not None else None
            freshness = "FRESH" if mark is not None else ("STALE" if m else "UNAVAILABLE")
            live_pnl = round(sign * (mark - entry) * qty, 2) if (direction_valid and mark is not None and entry) else None
            t1, sl = r.get("target_1"), r.get("stop_loss")
            hit = None
            if direction_valid and mark is not None:
                if t1 and ((sign > 0 and mark >= t1) or (sign < 0 and mark <= t1)):
                    hit = "TARGET"
                elif sl and ((sign > 0 and mark <= sl) or (sign < 0 and mark >= sl)):
                    hit = "STOP"
            if r.get("status") != "OPEN":
                monitor_status = r.get("status") or "UNKNOWN"
            elif not direction_valid:
                monitor_status = "INVALID_DATA"
            elif freshness != "FRESH":
                monitor_status = "STALE_DATA"
            elif hit:
                monitor_status = f"{hit}_HIT"
            else:
                monitor_status = "OPEN"
            if not direction_valid:
                target_distance = stop_distance = None
            elif direction == "BUY":
                target_distance = t1 - mark if (t1 and mark is not None) else None
                stop_distance = mark - sl if (sl and mark is not None) else None
            else:
                target_distance = mark - t1 if (t1 and mark is not None) else None
                stop_distance = sl - mark if (sl and mark is not None) else None
            out.append({
                **r,
                "mark": mark,
                "mark_source": mark_src,
                "mark_age_sec": age,
                "stale_age_sec": age if freshness == "STALE" else None,
                "freshness": freshness,
                "live_pnl": live_pnl,
                "hit": hit,
                "monitor_status": monitor_status,
                "dist_to_target": round(target_distance, 2) if target_distance is not None else None,
                "dist_to_stop": round(stop_distance, 2) if stop_distance is not None else None,
            })
        return out

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "runner": {k: st.get(k) for k in (
            "armed", "running", "is_leader", "runner_owner", "auto_arm", "fast_mode",
            "manage_latency_ms", "broker_sync", "broker_sync_status",
            "last_tick_ts", "last_error", "session_open", "session_note",
            "cooldown_sec_remaining", "open_scalps", "max_concurrent",
            "traded_today", "daily_cap", "poll_sec")},
        "feed": st.get("feed"),
        "ltp_max_age_sec": LTP_MAX_AGE_SEC,
        "positions": enrich(db.list_trades(strategy="MANUAL", limit=100)),
        "scalps": enrich(db.list_trades(strategy="SCALP", limit=100)),
        "combos": combos.snapshot(),
        "reversals": [v for v in (scalp_runner.reversals or {}).values() if v.get("reversal")],
        "turning_points": [v for v in (scalp_runner.turning_points or {}).values()
                           if v.get("direction") != "NO_TURN"],
        "execution": {**(st.get("execution") or {}),
                      "kill_switch": _ks_state(),
                      "orders": db.list_broker_orders(limit=25)},
        "recent_signals": db.list_signals(limit=20),
    }


def _ks_state():
    try:
        from .execution import killswitch
        return killswitch.state()
    except Exception:
        return {"active": False, "policy": "MONITOR"}


@app.post("/api/scalp/arm")
def api_scalp_arm():
    scalp_runner.start()
    scalp_runner.arm()
    return scalp_runner.status()


@app.post("/api/scalp/disarm")
def api_scalp_disarm():
    scalp_runner.disarm()
    return scalp_runner.status()


@app.get("/api/scalp/config")
def api_scalp_get_config():
    return scalp_runner.get_config()


@app.post("/api/scalp/config")
def api_scalp_set_config(payload: dict):
    try:
        return scalp_runner.set_config(payload or {})
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/scalp/trades")
def api_scalp_trades(status: Optional[str] = None, limit: int = Query(200, le=2000)):
    return db.list_trades(status=status, limit=limit, strategy="SCALP")


# ---------------------------------------------------------------- Autonomous scalper (P7, PAPER)
@app.get("/api/autoscalp/status")
def api_autoscalp_status(compact: bool = False):
    st = autoscalp.status()
    return _compact(st) if compact else _label_marks(st)


@app.get("/api/autoscalp/signals")
def api_autoscalp_signals(status: Optional[str] = None, symbol: Optional[str] = None,
                          limit: int = Query(200, le=2000)):
    return db.list_scalp_signals(source="LIVE", status=status, symbol=symbol, limit=limit)


@app.get("/api/autoscalp/snapshots")
def api_autoscalp_snapshots(symbol: Optional[str] = None, limit: int = Query(200, le=2000)):
    return db.list_live_snapshots(symbol=symbol, limit=limit)


@app.get("/api/autoscalp/report")
def api_autoscalp_report(day: Optional[str] = None):
    """Per-symbol rollup of an IST trading day (default today): trades, W/L,
    net points, avg R, exit-reason / decision / regime distribution, ZTH legs,
    and why entries were refused."""
    from .autoscalp import report as _report
    try:
        return _report.session_report(day)
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")


@app.get("/api/autoscalp/selfcheck")
def api_autoscalp_selfcheck():
    """One-glance operational readiness of the autonomous engine."""
    from .autoscalp import report as _report
    return _report.self_check(autoscalp)


@app.get("/api/autoscalp/universe")
def api_autoscalp_universe():
    """Grouped, searchable symbol universe for the dashboard picker.
    `watchlist` = symbols the runner actually trades; the groups are for
    viewing S/R / VWAP / regime of any symbol."""
    reg = instruments.registry()
    nse_idx, mcx = [], []
    for k, v in sorted(reg.items()):
        ex = str(v.get("exchange") or "").upper()
        (mcx if ex == "MCX" else nse_idx).append(k)
    equity_fno = []
    try:
        seen = set()
        for r in instruments.master_rows():
            if r.get("exch_seg") == "NFO" and r.get("instrumenttype") == "OPTSTK":
                n = str(r.get("name") or "").upper()
                if n and n not in seen:
                    seen.add(n)
                    equity_fno.append(n)
        equity_fno.sort()
    except Exception:
        equity_fno = []
    wl = []
    try:
        wl = list(autoscalp.get_config().get("symbols") or [])
    except Exception:
        pass
    return {
        "watchlist": wl,
        "groups": {
            "NSE Index": [s for s in nse_idx if s not in mcx],
            "MCX": mcx,
            "Equity (F&O)": equity_fno,
        },
    }


@app.get("/api/autoscalp/config")
def api_autoscalp_get_config():
    return autoscalp.get_config()


@app.post("/api/autoscalp/config")
def api_autoscalp_set_config(payload: dict):
    try:
        return autoscalp.set_config(payload or {})
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/autoscalp/watchlist")
def api_autoscalp_watchlist(payload: dict):
    """Add or remove one symbol from the trading watchlist.
    {"symbol": "RELIANCE", "action": "add" | "remove"}"""
    sym = str((payload or {}).get("symbol") or "").strip().upper()
    action = str((payload or {}).get("action") or "").lower()
    if not sym or action not in ("add", "remove"):
        raise HTTPException(422, "symbol and action ('add'|'remove') required")
    cur = list(autoscalp.get_config().get("symbols") or [])
    if action == "add" and sym not in cur:
        cur.append(sym)
    elif action == "remove":
        cur = [s for s in cur if s != sym]
    if not cur:
        raise HTTPException(422, "watchlist cannot be empty")
    autoscalp.set_config({"symbols": cur})
    return {"symbols": cur}


@app.post("/api/autoscalp/arm")
def api_autoscalp_arm():
    autoscalp.start()
    autoscalp.arm()
    return autoscalp.status()


@app.post("/api/autoscalp/disarm")
def api_autoscalp_disarm():
    autoscalp.disarm()
    return autoscalp.status()


@app.post("/api/autoscalp/kill")
def api_autoscalp_kill(req: KillSwitchRequest):
    """Reuses the global execution kill switch — blocks all new autoscalp
    entries. Open PAPER positions keep being monitored."""
    from .execution import killswitch
    state = (killswitch.activate(req.reason or "autoscalp-api") if req.active
             else killswitch.deactivate(req.reason or "autoscalp-api"))
    return {"kill_switch": state, "autoscalp": autoscalp.status()}


# ---------------------------------------------------------------- External position tracker
class TrackPositionRequest(BaseModel):
    symbol: str                         # e.g. "NATGASMINI" or a display name
    symboltoken: Optional[str] = None   # Angel One token; resolved from registry if omitted
    exchange: Optional[str] = None
    option_type: Optional[str] = None   # CE | PE | "" for futures/equity
    strike: Optional[float] = None
    expiry: Optional[str] = None
    direction: str
    entry: float
    target: float
    stop: float
    lots: float = 1
    lot_size: float = 1
    trailing_stop: Optional[float] = 0   # 0 = honour the literal stop, no ratchet


def _positive_finite(value, name: str, *, required: bool = False):
    if value is None:
        if required:
            raise HTTPException(422, f"{name} is required")
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(422, f"{name} must be a finite positive number")
    if not math.isfinite(number) or number <= 0:
        raise HTTPException(422, f"{name} must be a finite positive number")
    return number


def _nonnegative_finite(value, name: str):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(422, f"{name} must be a finite non-negative number")
    if not math.isfinite(number) or number < 0:
        raise HTTPException(422, f"{name} must be a finite non-negative number")
    return number


def _validate_monitor_levels(direction, entry, target=None, stop=None):
    """Validate monitor-only levels without inventing optional missing levels."""
    direction = str(direction or "").upper()
    if direction not in ("BUY", "SELL"):
        raise HTTPException(422, "direction must be BUY or SELL")
    entry = _positive_finite(entry, "entry", required=True)
    target = _positive_finite(target, "target")
    stop = _positive_finite(stop, "stop")
    if target is not None and stop is not None:
        valid = (stop < entry < target) if direction == "BUY" else (target < entry < stop)
        if not valid:
            rule = "stop < entry < target" if direction == "BUY" else "target < entry < stop"
            raise HTTPException(422, f"invalid {direction} levels: require {rule}")
    return direction, entry, target, stop


@app.post("/api/positions/track")
async def api_track_position(req: TrackPositionRequest):
    """Register a real broker position for MONITOR-ONLY tracking. The app marks
    it to the live WebSocket feed and alerts (WS + Telegram) on target / stop.
    It NEVER places a broker order."""
    direction, entry, target, stop = _validate_monitor_levels(req.direction, req.entry, req.target, req.stop)
    trailing_stop = _nonnegative_finite(req.trailing_stop, "trailing_stop")
    tok = req.symboltoken
    exch = req.exchange
    if not tok:
        meta = instruments.resolve(req.symbol)
        if meta:
            tok, exch = meta.get("symboltoken"), exch or meta.get("exchange")
    if not tok:
        raise HTTPException(400, f"no symboltoken for '{req.symbol}' — pass symboltoken or add it via /api/instruments")

    # de-dup: if a mirror for this contract already exists (manual or auto-synced),
    # update its levels instead of creating a second OPEN row.
    existing = db.find_open_by_token(str(tok), strategy="MANUAL")
    if existing:
        db.update_trade(existing["trade_id"], {
            "target_1": target, "stop_loss": stop,
            "trailing_stop": trailing_stop or 0})
        row = db.get_trade(existing["trade_id"])
        await manager.broadcast({"type": "position_update", "data": row})
        return row

    row = open_trade({
        "signal_id": None,
        "market": exch or "", "underlying": req.symbol.upper(),
        "instrument": "OPTION" if req.option_type else "FUT",
        "expiry": req.expiry or "", "strike": req.strike or 0,
        "option_type": (req.option_type or "").upper(),
        "direction": direction, "timeframe": "",
        "entry": entry, "target_1": target, "target_2": None,
        "stop_loss": stop, "trailing_stop": trailing_stop or 0,
        "quantity": (req.lots or 1) * (req.lot_size or 1),
        "probability": None, "confidence": None, "market_regime": "",
        "oi_evidence": "", "reason": "external broker position — monitor only",
        "strategy": "MANUAL", "setup": None, "atr_pct": None,
        "max_hold_sec": None, "symboltoken": str(tok),
    })
    # the active runner picks up the new token on its next tick (it rebuilds the
    # feed subscription from list_open_managed()); don't touch the feed here —
    # a non-leader worker must never start a second WebSocket connection.
    await manager.broadcast({"type": "position_open", "data": row})
    return row


@app.get("/api/positions")
def api_positions(status: Optional[str] = None, limit: int = Query(200, le=2000)):
    return db.list_trades(status=status, limit=limit, strategy="MANUAL")


@app.get("/api/broker/positions")
def api_broker_positions():
    """Live net positions straight from Angel One (getPosition). The runner also
    auto-registers any of these that aren't tracked yet."""
    from .connectors import angelone as _a
    return _a.fetch_positions()


class LevelsRequest(BaseModel):
    trade_id: str
    target: Optional[float] = None
    stop: Optional[float] = None
    trailing_stop: Optional[float] = None


@app.post("/api/positions/levels")
async def api_position_levels(req: LevelsRequest):
    """Set / update target, stop, trailing on a tracked (or auto-synced) position."""
    t = db.get_trade(req.trade_id)
    if not t or t.get("status") != "OPEN":
        raise HTTPException(404, "open position not found")
    direction, _entry, target, stop = _validate_monitor_levels(
        t.get("direction"), t.get("entry"),
        req.target if req.target is not None else t.get("target_1"),
        req.stop if req.stop is not None else t.get("stop_loss"),
    )
    fields = {}
    if req.target is not None:
        fields["target_1"] = target
    if req.stop is not None:
        fields["stop_loss"] = stop
    if req.trailing_stop is not None:
        fields["trailing_stop"] = _nonnegative_finite(req.trailing_stop, "trailing_stop")
    if not fields:
        raise HTTPException(400, "nothing to set")
    db.update_trade(req.trade_id, fields)
    updated = db.get_trade(req.trade_id)
    await manager.broadcast({"type": "position_update", "data": updated})
    return updated


class ComboRequest(BaseModel):
    legs: list[str]
    kind: Optional[str] = "STRANGLE"
    target: Optional[float] = None
    stop: Optional[float] = None
    trail: Optional[float] = None


class ComboLevelsRequest(BaseModel):
    combo_id: str
    target: Optional[float] = None
    stop: Optional[float] = None
    trail: Optional[float] = None


@app.get("/api/positions/combos")
def api_combos():
    """Live combined figures for every strangle/combo: combined debit vs mark,
    pair P&L, expiry break-evens, distance to combined target / stop."""
    return combos.snapshot()


@app.post("/api/positions/combo")
def api_create_combo(req: ComboRequest):
    try:
        return combos.create(req.legs, kind=(req.kind or "STRANGLE"),
                             target_combined=req.target, stop_combined=req.stop,
                             trail_combined=req.trail)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/positions/combo/levels")
def api_combo_levels(req: ComboLevelsRequest):
    try:
        return combos.set_levels(req.combo_id, target_combined=req.target,
                                 stop_combined=req.stop, trail_combined=req.trail)
    except KeyError:
        raise HTTPException(404, "combo not found")


@app.post("/api/positions/untrack")
async def api_untrack_position(req: CloseTradeRequest):
    updated = close_trade(req.trade_id, req.exit_price, exit_reason="UNTRACKED")
    if not updated:
        raise HTTPException(404, "position not found")
    await manager.broadcast({"type": "position_exit", "data": updated})
    return updated


# ---------------------------------------------------------------- Data endpoints
@app.get("/api/signals")
def api_signals(limit: int = Query(200, le=2000)):
    return db.list_signals(limit=limit)


@app.get("/api/trades")
def api_trades(status: Optional[str] = None, limit: int = Query(200, le=2000)):
    return db.list_trades(status=status, limit=limit)


@app.get("/api/research")
def api_research():
    return aggregate_research()


@app.post("/api/trades/mark")
async def api_mark_price(req: MarkPriceRequest):
    updated = update_trade_price(req.trade_id, req.ltp)
    if not updated:
        raise HTTPException(404, "trade not found")
    if updated.get("status") == "CLOSED":
        await manager.broadcast({"type": "trade_closed", "data": updated})
    else:
        await manager.broadcast({"type": "trade_update", "data": updated})
    return updated


@app.post("/api/trades/close")
async def api_close_trade(req: CloseTradeRequest):
    updated = close_trade(req.trade_id, req.exit_price)
    if not updated:
        raise HTTPException(404, "trade not found")
    await manager.broadcast({"type": "trade_closed", "data": updated})
    return updated


@app.get("/api/health")
def api_health():
    return {"status": "ok", "live_trading": False, "paper_mode": True}


@app.get("/api/market/calendar")
def api_market_calendar():
    """NSE / MCX / BSE segment status + whether a restart is currently allowed."""
    from . import market_calendar
    return market_calendar.status_all()


@app.get("/api/env-check")
def api_env_check():
    """Reports which credentials are configured WITHOUT ever revealing values."""
    keys = [
        "ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_SIGNALS_CHANNEL_ID",
    ]
    return {k: bool(os.environ.get(k)) for k in keys}


# ---------------------------------------------------------------- Frontend (static SPA)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")


@app.get("/")
def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/{full_path:path}")
def spa_catchall(full_path: str):
    # Let real API/static paths 404 normally; everything else serves the SPA
    if full_path.startswith("api/") or full_path.startswith("static/") or full_path == "ws":
        raise HTTPException(404)
    return FileResponse(str(FRONTEND_DIR / "index.html"))
