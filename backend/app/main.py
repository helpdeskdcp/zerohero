"""
Chanakya AI — FastAPI backend.
Serves the REST API + the responsive web dashboard (auto-detects
mobile/desktop client-side, single codebase).

Route handlers live under app/api/ (one module per logical group) and read
the shared runtime singletons (ScalpRunner, AutoScalpRunner, the WebSocket
ConnectionManager) from app/runtime.py. This file is the composition root:
middleware, auth, startup/shutdown wiring, the /ws endpoint itself, the
already-modular subsystem routers (histcap/greeks_engine/mathematical_confluence/
smart_index_scalper), and the SPA static-file serving.
"""
import base64
import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, runtime
from .ws_auth import query_token_ok as _ws_query_token_ok   # constant-time token comparison (HTTP gate and both WebSockets)

APP_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = APP_ROOT / "frontend"

# Every business-logic module below (execution/, autoscalp/, market_hub, the
# broker connector, ...) has historically caught its own exceptions and fallen
# back to a safe default with NO log line at all -- convenient for "never let
# one bad symbol 500 the whole request" fail-open behaviour, but it also hid
# the root cause of real incidents this app has already had (a stale broker
# session silently returning DATA_INSUFFICIENT, a latent None.get() bug that
# only ever hit the except branch). uvicorn configures its OWN loggers
# separately (uvicorn.error/uvicorn.access) and this basicConfig only touches
# the root logger, so it does not fight that -- it just makes every module's
# `logging.getLogger(__name__)` call actually go somewhere (stderr -> journal,
# since the systemd unit sets StandardError=journal).
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

_log = logging.getLogger("chanakya.api")

app = FastAPI(title="Chanakya AI", version="1.0.0")

# The frontend is served by this same app (StaticFiles mount below), so
# same-origin browser calls never need CORS headers at all. This is only
# for a genuine cross-origin caller (e.g. a separate admin tool / local
# frontend dev server on another port). Wildcard "*" would let ANY website's
# JS probe every /api/* endpoint from a visitor's browser -- pin it to real
# origins instead. Comma-separated; defaults to the one production host.
_CORS_ORIGINS = [o.strip() for o in
                 os.environ.get("CHANAKYA_CORS_ORIGINS",
                                "https://chanakya.datacarepoint.com").split(",")
                 if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Opt-in bearer auth. If CHANAKYA_API_TOKEN is set, every /api/* call (except
# /api/health) and the /ws upgrade must present it (Authorization: Bearer <t>
# or ?token=<t>). If unset, this is a no-op and the app behaves as before.
API_TOKEN = (os.environ.get("CHANAKYA_API_TOKEN") or "").strip()
# No hardcoded fallback: an unset/blank credential must fail CLOSED (deny
# every Basic-Auth attempt), never silently accept a well-known default.
# Both env vars must be explicitly configured for Basic-Auth to work at all;
# CHANAKYA_API_TOKEN remains a fully independent way in if Basic-Auth is
# left unconfigured.
ADMIN_USERNAME = (os.environ.get("CHANAKYA_ADMIN_USERNAME") or "").strip()
ADMIN_PASSWORD = (os.environ.get("CHANAKYA_ADMIN_PASSWORD") or "").strip()
if not ADMIN_USERNAME or not ADMIN_PASSWORD:
    _log.warning(
        "CHANAKYA_ADMIN_USERNAME / CHANAKYA_ADMIN_PASSWORD not set -- HTTP "
        "Basic auth is disabled (fails closed, no default credential). Set "
        "both in .env, or rely on CHANAKYA_API_TOKEN instead."
    )


def _token_from(request) -> str:
    a = request.headers.get("authorization", "")
    if a[:7].lower() == "bearer ":
        return a[7:].strip()
    return request.query_params.get("token", "")


def _basic_ok(value: str) -> bool:
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        return False        # fail closed: no configured credential, no access
    try:
        raw = base64.b64decode(value[6:].strip(), validate=True).decode("utf-8")
        user, password = raw.split(":", 1)
    except Exception:
        return False
    return hmac.compare_digest(user, ADMIN_USERNAME) and hmac.compare_digest(password, ADMIN_PASSWORD)


class _AuthGateMiddleware:
    """Pure ASGI middleware -- deliberately NOT @app.middleware("http")/
    BaseHTTPMiddleware. BaseHTTPMiddleware reconstructs the downstream
    response as a new StreamingResponse and does not carry over the
    original response's `.background` attribute, so any route's
    `background_tasks.add_task(...)` is silently dropped -- no error, no
    log. Confirmed live 2026-09-24: POST /api/hedging/scan-now returned 200
    but its background scan never ran under the old decorator-based version
    until routed around it via asyncio.create_task(...) instead. A plain
    ASGI middleware never touches the response object at all -- it can't
    have this failure mode -- so this replaces the old version for every
    route, current and future, instead of relying on each new route
    remembering to avoid FastAPI's BackgroundTasks.

    /api/health and Dhan's postback + OAuth-redirect paths
    (app/api/dhan_routes.py -- is_auth_exempt_path()) are the exemptions:
    Dhan's own servers/browser-redirects can't send our Basic/Bearer
    credentials, so those routes carry their own secret (the postback path)
    or a one-time Dhan-issued tokenId useless without our own partner
    secret (the redirect callback) instead, checked inside the route
    itself, fail-closed."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        path = request.url.path
        from .api.dhan_routes import is_auth_exempt_path  # local: avoids reordering the file's route-module imports
        if path != "/api/health" and not is_auth_exempt_path(path):
            basic = request.headers.get("authorization", "")
            token_ok = _ws_query_token_ok(_token_from(request), API_TOKEN)      # constant-time comparison (was a plain ==)
            if not token_ok and not (basic.lower().startswith("basic ") and _basic_ok(basic)):
                response = JSONResponse({"detail": "unauthorized"}, status_code=401,
                                        headers={"WWW-Authenticate": 'Basic realm="Chanakya AI"'})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


app.add_middleware(_AuthGateMiddleware)


class _ArenaNoCacheMiddleware:
    """Pure ASGI: the arena pages/script are always revalidated (a participant must never see a stale page after a wording or rule change)."""
    _PATHS = ("/static/arena.html", "/static/js/arena.js", "/static/js/arena_terminal.js", "/static/leaderboard.html")

    def __init__(self, app): self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") not in self._PATHS: return await self.app(scope, receive, send)

        async def _send(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [h for h in message.get("headers", []) if h[0].lower() != b"cache-control"] + [(b"cache-control", b"no-cache, must-revalidate")]}
            await send(message)
        await self.app(scope, receive, _send)


app.add_middleware(_ArenaNoCacheMiddleware)


# ---------------------------------------------------------------- route modules
from .api import (
    analysis_routes,
    autoscalp_routes,
    data_routes,
    dhan_routes,
    engines_routes,
    execution_routes,
    hedging_routes,
    instruments_routes,
    monitor_routes,
    positions_routes,
    scalp_routes,
    system_routes,
)

for _mod in (engines_routes, instruments_routes, analysis_routes, scalp_routes,
            execution_routes, monitor_routes, autoscalp_routes, positions_routes,
            data_routes, system_routes, hedging_routes, dhan_routes):
    app.include_router(_mod.router)

# Smart Chart (Vela + PineTS + Upstox candles, paper-only): self-contained package, own DB, no order path.
from .smart_chart import routes as _smart_chart_routes  # noqa: E402

app.include_router(_smart_chart_routes.router)

# Re-exports for backward compatibility: a handful of tests call these route
# handlers / helpers directly as `main.X` (unit-testing the function rather
# than going through HTTP). Keep them reachable under their old home so those
# tests need no changes -- the definitions themselves now live in app/runtime
# and app/api/*.
from .api.schemas import (
    LevelsRequest,
    SignalRequest,
    TrackPositionRequest,
)

_label_marks = runtime._label_marks
_compact = runtime._compact
_merge_broker_greeks = runtime._merge_broker_greeks
# Same singleton objects as app.runtime, not copies -- monkeypatching a
# method on main.scalp_runner / main.autoscalp (rather than rebinding the
# name) still affects every route handler that reads app.runtime.scalp_runner
# / app.runtime.autoscalp, since it's the identical instance.
scalp_runner = runtime.scalp_runner
autoscalp = runtime.autoscalp
api_run_pipeline = engines_routes.api_run_pipeline
api_market_instruments = instruments_routes.api_market_instruments
api_monitor = monitor_routes.api_monitor
api_position_levels = positions_routes.api_position_levels
api_track_position = positions_routes.api_track_position
api_market_calendar = system_routes.api_market_calendar
api_autoscalp_status = autoscalp_routes.api_autoscalp_status
api_autoscalp_signals = autoscalp_routes.api_autoscalp_signals
api_autoscalp_snapshots = autoscalp_routes.api_autoscalp_snapshots
api_autoscalp_universe = autoscalp_routes.api_autoscalp_universe
api_autoscalp_get_config = autoscalp_routes.api_autoscalp_get_config
api_autoscalp_set_config = autoscalp_routes.api_autoscalp_set_config
api_autoscalp_watchlist = autoscalp_routes.api_autoscalp_watchlist
api_autoscalp_disarm = autoscalp_routes.api_autoscalp_disarm


# ---- historical market-data capture (standalone; own DB, no WS, no trading logic) ----
try:
    from .histcap import api as _histcap_api
    from .histcap.worker import CaptureWorker as _CaptureWorker
    histcap_worker = _CaptureWorker()
    _histcap_api.bind_worker(histcap_worker)
    app.include_router(_histcap_api.router)
except Exception as _e:  # never let capture wiring break the app
    histcap_worker = None
    print(f"[histcap] disabled: {type(_e).__name__}: {_e}")

# ---- L2 SnapQuote capture (Angel WS mode 3; own socket + own DB; research data
# capture only; OFF unless L2_CAPTURE_ENABLED=1; no trading / signal path) ----
try:
    from .l2capture.worker import L2CaptureWorker as _L2CaptureWorker
    l2_capture_worker = _L2CaptureWorker()
except Exception as _e:  # never let capture wiring break the app
    l2_capture_worker = None
    print(f"[l2capture] disabled: {type(_e).__name__}: {_e}")

# ---- Dhan orderflow feed (own WS + in-memory footprint/CVD engine; research
# data path only; OFF unless DHAN_ORDERFLOW_ENABLED=1; no trading / signal
# path -- see app/connectors/dhan_ws.py + app/engines/orderflow_engine.py) ----
try:
    from .connectors.dhan_ws import DhanOrderflowWorker as _DhanOrderflowWorker
    from .engines.orderflow_engine import OrderflowEngine as _OrderflowEngine
    from .engines.orderflow_store import TickStore as _TickStore
    dhan_orderflow_worker = _DhanOrderflowWorker()
    dhan_orderflow_engine = _OrderflowEngine(
        dhan_orderflow_worker.queue, tick_store=_TickStore(),
        meta_provider=lambda: dhan_orderflow_worker._instrument_meta)
except Exception as _e:  # never let orderflow wiring break the app
    dhan_orderflow_worker = None
    dhan_orderflow_engine = None
    print(f"[dhan_orderflow] disabled: {type(_e).__name__}: {_e}")

# ---- Hybrid OrderFlow SHADOW runner: SIGNAL-ONLY / PAPER-ONLY, no order path. Reads the Dhan ticks read-only, scores closed 5m bars with the
# policy-driven engine, stores into its OWN sqlite file, exposes /api/hybrid_orderflow/status. Kill switch: HYBRID_ORDERFLOW_SHADOW=0 ----
hybrid_orderflow_shadow = None
try:
    from . import hybrid_orderflow_api as _hof_api
    from .hybrid_orderflow import shadow as _hof_shadow
    app.include_router(_hof_api.router)
    if _hof_shadow.enabled() and dhan_orderflow_worker is not None:
        hybrid_orderflow_shadow = _hof_shadow.ShadowRunner(lambda: dhan_orderflow_worker._instrument_meta)     # PolicyError => not created (fail closed)
        _hof_api.set_runner(hybrid_orderflow_shadow)
    else:
        print("[hybrid_orderflow] shadow runner not started (HYBRID_ORDERFLOW_SHADOW=0 or Dhan worker unavailable)")
except Exception as _e:  # never let it break the app
    hybrid_orderflow_shadow = None
    print(f"[hybrid_orderflow] shadow disabled: {type(_e).__name__}: {_e}")

# ---- Option Greeks Engine (derived exposure over captured broker Greeks; read-only) ----
try:
    from .greeks_engine import api as _greeks_api
    app.include_router(_greeks_api.router)
except Exception as _e:
    print(f"[greeks_engine] router disabled: {type(_e).__name__}: {_e}")

# ---- Mathematical Confluence Engine V1 (pivots + Gann + OI confluence; read-only) ----
try:
    from .mathematical_confluence import api as _mathconf_api
    app.include_router(_mathconf_api.router)
except Exception as _e:
    print(f"[mathematical_confluence] router disabled: {type(_e).__name__}: {_e}")

# ---- Smart Index Scalper (ranks the index universe over the confluence engine; read-only) ----
try:
    from .smart_index_scalper import api as _smartscalp_api
    app.include_router(_smartscalp_api.router)
except Exception as _e:
    print(f"[smart_index_scalper] router disabled: {type(_e).__name__}: {_e}")

# ---- Order-flow module, Phase 1: Volume Profile + Market Profile / TPO
# (read-only over captured OHLCV bars; OHLCV-range approximation, clearly
# labelled; no tick data, no broker calls, no trading path) ----
try:
    from .orderflow import api as _orderflow_api
    app.include_router(_orderflow_api.router)
except Exception as _e:
    print(f"[orderflow] router disabled: {type(_e).__name__}: {_e}")

# ---- Research strategy panels (read-only backtests + scans; NOT VALIDATED;
# no order path, no live-signal emission, no frozen/trading logic touched) ----
try:
    from .research_strategy import api as _research_strat_api
    app.include_router(_research_strat_api.router)
except Exception as _e:
    print(f"[research_strategy] router disabled: {type(_e).__name__}: {_e}")

# ---- HCS (High-Confidence Signal) meta-engine (SHADOW; re-scores the live
# engine's own persisted decision with a hard-filter veto layer; read-only;
# not imported by autoscalp; no order path) ----
try:
    from .hcs import api as _hcs_api
    app.include_router(_hcs_api.router)
except Exception as _e:
    print(f"[hcs] router disabled: {type(_e).__name__}: {_e}")

# ---- Hybrid MP v13 (fixed) research dashboard: READ-ONLY, runs the research engine on the latest 1m bars; no order path ----
try:
    if os.environ.get("HYBRID_MP_ENABLED", "1").strip().lower() in ("0", "false", "off", "no"):
        print("[hybrid_mp] disabled by HYBRID_MP_ENABLED=0")           # feature flag / kill switch (default ON, read-only research API)
    else:
        from . import hybrid_mp_api as _hybrid_mp_api
        app.include_router(_hybrid_mp_api.router)
except Exception as _e:
    print(f"[hybrid_mp] router disabled: {type(_e).__name__}: {_e}")

# ---- PA/OF research lab chart API: READ-ONLY (sqlite mode=ro), no order path. Kill switch: PA_OF_LAB=0 ----
try:
    if os.environ.get("PA_OF_LAB", "1").strip().lower() not in ("0", "false", "off", "no"):
        from .pa_of_lab import api as _pa_of_lab_api
        app.include_router(_pa_of_lab_api.router)
except Exception as _e:
    print(f"[pa_of_lab] router disabled: {type(_e).__name__}: {_e}")

# ---- Rules registry: READ-ONLY inventory of every trading rule/gate (scan of the code vs rules.yaml). Kill switch: RULES_REGISTRY=0 ----
try:
    if os.environ.get("RULES_REGISTRY", "1").strip().lower() not in ("0", "false", "off", "no"):
        from .rules_registry import api as _rules_registry_api
        app.include_router(_rules_registry_api.router)
except Exception as _e:
    print(f"[rules_registry] router disabled: {type(_e).__name__}: {_e}")

# ---- Arena (paper head-to-head, Institution-side participants): own token-protected public API; append-only submission ledger; no order path. Kill switch: ARENA_PUBLIC=0 ----
try:
    if os.environ.get("ARENA_PUBLIC", "1").strip().lower() not in ("0", "false", "off", "no"):
        from .arena import api as _arena_api
        app.include_router(_arena_api.router)
except Exception as _e:
    print(f"[arena] router disabled: {type(_e).__name__}: {_e}")

# ---- Chartink scanner alerts: webhook receiver + forward validation (observation only, no order path) ----
try:
    from .chartink import api as _chartink_api
    app.include_router(_chartink_api.router)
except Exception as _e:
    print(f"[chartink] router disabled: {type(_e).__name__}: {_e}")

# ---- In-house daily scanner (read-only views; observation only, no order path) ----
try:
    from .scanner import api as _scanner_api
    app.include_router(_scanner_api.router)
except Exception as _e:
    print(f"[scanner] router disabled: {type(_e).__name__}: {_e}")

# ---- NIFTY 50 constituents direction engine (paper only, read-only views; no order path) ----
try:
    from .nifty_direction import api as _nd_api
    app.include_router(_nd_api.router)
except Exception as _e:
    print(f"[nifty_direction] router disabled: {type(_e).__name__}: {_e}")

# ---- Zone Breakout paper bot (owner's Angel One bot, all six indices; read-only views, observation only) ----
try:
    from .zone_breakout import api as _zb_api
    app.include_router(_zb_api.router)
except Exception as _e:
    print(f"[zone_breakout] router disabled: {type(_e).__name__}: {_e}")

# ---- Unified Signal Dashboard (read-only aggregator: every engine's read
# side-by-side per symbol; no blending, no order path) ----
try:
    from . import signals_dashboard as _signals_dash
    app.include_router(_signals_dash.router)
except Exception as _e:
    print(f"[signals_dashboard] router disabled: {type(_e).__name__}: {_e}")

# ---- Option-Chain data layer + structure engine (read-only: captured chain +
# keyless Upstox/NSE fallback -> OI/IV/greeks/PCR/max-pain/GEX -> structure state
# -> QUALIFIED|WATCH|NO_TRADE gate; no order path, no live_trading, no API keys) ----
try:
    from .optionchain import api as _optionchain_api
    app.include_router(_optionchain_api.router)
except Exception as _e:
    print(f"[optionchain] router disabled: {type(_e).__name__}: {_e}")

# ---- Atomic Market Snapshot (read-only: spot + chain + India VIX tied to one
# snapshot_id, MCP 3.3 section 4/48; no new capture path, composes existing reads) ----
try:
    from .api import market_snapshot_routes as _market_snapshot_api
    app.include_router(_market_snapshot_api.router)
except Exception as _e:
    print(f"[market_snapshot] router disabled: {type(_e).__name__}: {_e}")

# ---- Premium-vs-Spot Decoupling (read-only, shadow: classifies observed
# spot-move vs ATM CE/PE-premium-move sign relationship over a window, e.g.
# "index up but CE premium also falling"; computed from already-captured
# quote_snapshots, logged to its own db; no order path, no gating, nothing
# else in the app consults this) ----
try:
    from .api import premium_decoupling_routes as _decoupling_api
    app.include_router(_decoupling_api.router)
except Exception as _e:
    print(f"[premium_decoupling] router disabled: {type(_e).__name__}: {_e}")

# ---- Stop-Loss Hit Analysis (read-only, shadow: groups already-closed
# ai_paper_trades by underlying/regime/strategy/hour, checks mae-vs-planned-
# risk overshoot and stated-probability-vs-actual-stop-rate; descriptive
# only, no order path, no gating) ----
try:
    from .api import sl_hit_analysis_routes as _sl_hit_api
    app.include_router(_sl_hit_api.router)
except Exception as _e:
    print(f"[sl_hit_analysis] router disabled: {type(_e).__name__}: {_e}")

# ---- Structural Break / Adaptive Model layer (read-only: on-demand
# performance/drift re-scoring + durable transition audit log; no order
# path, no live_trading, never auto-wired into Safeguards from here) ----
try:
    from .structural_break import api as _structural_break_api
    app.include_router(_structural_break_api.router)
except Exception as _e:
    print(f"[structural_break] router disabled: {type(_e).__name__}: {_e}")

# ---- Institutional Edge layer (read-only: conditional edge / EV-with-real-
# costs analysis over already-captured scalp_signals; no order path, no
# live_trading, never wired into signal generation) ----
try:
    from .institutional_edge import api as _institutional_edge_api
    app.include_router(_institutional_edge_api.router)
except Exception as _e:
    print(f"[institutional_edge] router disabled: {type(_e).__name__}: {_e}")

# ---- Microstructure / Operator-Behaviour Proxy layer (read-only: translates
# app.orderflow.h1h7_state's existing price-action classification into
# BUYING_PRESSURE/SELLING_PRESSURE/ABSORPTION/REJECTION/BREAKOUT/
# FAILED_BREAKOUT/BALANCED/UNKNOWN; no order path, no live_trading) ----
try:
    from .microstructure import api as _microstructure_api
    app.include_router(_microstructure_api.router)
except Exception as _e:
    print(f"[microstructure] router disabled: {type(_e).__name__}: {_e}")

# ---- Strategy Verification Engine (read-only: verifies a raw BUY/SELL read
# against a weighted CE/PE strategy before it may become a trade signal; no
# order path, no live_trading, existing signal-generation engines untouched) ----
try:
    from .strategy import api as _strategy_api
    app.include_router(_strategy_api.router)
except Exception as _e:
    print(f"[strategy] router disabled: {type(_e).__name__}: {_e}")

# ---- Dynamic SR live-state observability (/api/sr/status). Read-only --
# reads whatever the live decision cycle last computed, never recomputes,
# never blocks the request. ----------------------------------------------
try:
    from .sr_dynamic import api as _sr_dynamic_api
    app.include_router(_sr_dynamic_api.router)
except Exception as _e:
    print(f"[sr_dynamic] router disabled: {type(_e).__name__}: {_e}")

# ---- Instrument profile / calibration / AI diagnostics (Phase F). Read-only,
# no broker calls, no secrets exposed. ------------------------------------
try:
    from .api import profile_routes as _profile_routes
    app.include_router(_profile_routes.router)
except Exception as _e:
    print(f"[profiles] router disabled: {type(_e).__name__}: {_e}")

# ---- Smart Scalper paper-trade scheduler (spec section 48). PAPER only, DISARMED
# by default — wiring it in does not start opening positions on its own.
try:
    from .smart_index_scalper.scheduler import SCHEDULER as smart_scalper_scheduler
except Exception as _e:
    smart_scalper_scheduler = None
    print(f"[smart_index_scalper] scheduler disabled: {type(_e).__name__}: {_e}")


# ---- Platform live stream (G2-G4: Angel feed -> TickGate -> 1s candles -> writer + delta WS). Observation only;
# kill switch PLATFORM_STREAM=0. Never touches the feed/connector or any order path. ----
platform_live = None
try:
    from .platform import live as _platform_live
    if _platform_live.enabled():
        from .feed_registry import get_feed as _get_feed
        platform_live = _platform_live.PlatformLive(_get_feed)
except Exception as _e:
    print(f"[platform_live] disabled: {type(_e).__name__}: {_e}")


@app.get("/api/platform/status")
def platform_status():
    return platform_live.status() if platform_live else {"enabled": False}


@app.get("/api/platform/candles")
def platform_candles(instrument: str, tf: int = 60, limit: int = 300):
    if not platform_live:
        return {"enabled": False, "candles": []}
    snap = platform_live.hub.snapshot_message(instrument, tf)
    return {"instrument": instrument, "tf": tf, "seq": snap["seq"], "candles": snap["candles"][-max(1, min(limit, 600)):]}


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    db.init_db()
    if platform_live is not None:
        try:
            platform_live.start()
        except Exception as e:
            print(f"[platform_live] start failed: {type(e).__name__}: {e}")
    if os.environ.get("CHARTINK_TRACKER", "1") != "0":
        try:
            from .chartink import tracker as _chartink_tracker
            _chartink_tracker.Runner().start()
        except Exception as e:
            print(f"[chartink] tracker start failed: {type(e).__name__}: {e}")
    try:
        from .zone_breakout import live as _zb_live
        _zb_live.Runner().start()
    except Exception as e:
        print(f"[zone_breakout] runner start failed: {type(e).__name__}: {e}")
    try:
        from .winners_scalper import live as _wp_live
        _wp_live.Runner().start()
    except Exception as e:
        print(f"[winners_pine] runner start failed: {type(e).__name__}: {e}")
    try:
        from .arena import pine_submitter as _wp_sub
        _wp_sub.Runner().start()
    except Exception as e:
        print(f"[winners_pine] submitter start failed: {type(e).__name__}: {e}")
    runtime.scalp_runner.start()
    runtime.autoscalp.start()
    if histcap_worker is not None:
        try:
            histcap_worker.start()
        except Exception as e:
            print(f"[histcap] start failed: {type(e).__name__}: {e}")
    try:
        from .histcap import priority as _priority          # ~3 s quotes for contracts that have a registered signal (no-op while the watch list is empty; PRIORITY_CAPTURE=0 disables)
        _priority.start_background()
        import threading as _th; from .arena import api as _arena_api; _th.Thread(target=_arena_api.prewarm, name='arena-prewarm', daemon=True).start()
    except Exception as e:
        print(f"[priority-capture] start failed: {type(e).__name__}: {e}")
    if l2_capture_worker is not None:
        try:
            l2_capture_worker.start()   # no-op unless L2_CAPTURE_ENABLED=1
        except Exception as e:
            print(f"[l2capture] start failed: {type(e).__name__}: {e}")
    if smart_scalper_scheduler is not None:
        try:
            smart_scalper_scheduler.start()
        except Exception as e:
            print(f"[smart_scalper_scheduler] start failed: {type(e).__name__}: {e}")
    if dhan_orderflow_worker is not None:
        try:
            dhan_orderflow_worker.start()      # no-op unless DHAN_ORDERFLOW_ENABLED=1
            dhan_orderflow_engine.start()
        except Exception as e:
            print(f"[dhan_orderflow] start failed: {type(e).__name__}: {e}")
    if hybrid_orderflow_shadow is not None:
        try:
            hybrid_orderflow_shadow.start()
        except Exception as e:
            print(f"[hybrid_orderflow] start failed: {type(e).__name__}: {e}")

    yield

    if hybrid_orderflow_shadow is not None:
        try:
            await hybrid_orderflow_shadow.stop()
        except Exception:
            pass

    if platform_live is not None:
        try:
            await platform_live.stop()
        except Exception:
            pass
    await runtime.scalp_runner.stop()
    await runtime.autoscalp.stop()
    if histcap_worker is not None:
        try:
            await histcap_worker.stop()
        except Exception:
            pass
    if l2_capture_worker is not None:
        try:
            await l2_capture_worker.stop()
        except Exception:
            pass
    if smart_scalper_scheduler is not None:
        try:
            await smart_scalper_scheduler.stop()
        except Exception:
            pass
    if dhan_orderflow_worker is not None:
        try:
            await dhan_orderflow_engine.stop()
            await dhan_orderflow_worker.stop()
        except Exception:
            pass


# Registered post-hoc (not via FastAPI(lifespan=...)) because this context
# manager closes over histcap_worker/l2_capture_worker/smart_scalper_scheduler,
# which are only defined after `app` is constructed and the route modules
# above are imported.
app.router.lifespan_context = _lifespan


@app.websocket("/ws/platform")
async def ws_platform(websocket: WebSocket):
    auth = websocket.headers.get("authorization", "")
    if not (_ws_query_token_ok(websocket.query_params.get("token"), API_TOKEN)
            or (auth.lower().startswith("basic ") and _basic_ok(auth))):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    if platform_live is None:
        await websocket.close(code=1013)
        return
    try:
        await _platform_live.ws_session(platform_live, websocket)
    except Exception:
        pass


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    auth = websocket.headers.get("authorization", "")
    if not (_ws_query_token_ok(websocket.query_params.get("token"), API_TOKEN)
            or (auth.lower().startswith("basic ") and _basic_ok(auth))):
        await websocket.close(code=1008)
        return
    await runtime.manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keepalive ping/pong from client
    except WebSocketDisconnect:
        runtime.manager.disconnect(websocket)


# ---------------------------------------------------------------- Frontend (static SPA)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")

_SPA_ASSETS = ("js/app.js", "css/style.css")


def _spa_html() -> HTMLResponse:
    """Serve index.html with the JS/CSS refs cache-busted by their file mtime.
    Without this a browser that cached an older bundle keeps running stale JS
    against a freshly-served index.html (the SPA has no build step / hashing)."""
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    for rel in _SPA_ASSETS:
        p = FRONTEND_DIR / "static" / rel
        try:
            v = int(p.stat().st_mtime)
        except OSError:
            continue
        html = html.replace(f"/static/{rel}\"", f"/static/{rel}?v={v}\"")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    return _spa_html()


@app.get("/{full_path:path}")
def spa_catchall(full_path: str):
    # Let real API/static paths 404 normally; everything else serves the SPA
    if full_path.startswith("api/") or full_path.startswith("static/") or full_path == "ws":
        raise HTTPException(404)
    return _spa_html()
