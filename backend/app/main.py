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
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from . import db
from . import runtime

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


# ---------------------------------------------------------------- route modules
from .api import (   # noqa: E402
    engines_routes, instruments_routes, analysis_routes, scalp_routes,
    execution_routes, monitor_routes, autoscalp_routes, positions_routes,
    data_routes, system_routes,
)

for _mod in (engines_routes, instruments_routes, analysis_routes, scalp_routes,
            execution_routes, monitor_routes, autoscalp_routes, positions_routes,
            data_routes, system_routes):
    app.include_router(_mod.router)

# Re-exports for backward compatibility: a handful of tests call these route
# handlers / helpers directly as `main.X` (unit-testing the function rather
# than going through HTTP). Keep them reachable under their old home so those
# tests need no changes -- the definitions themselves now live in app/runtime
# and app/api/*.
from .api.schemas import SignalRequest, LevelsRequest, TrackPositionRequest  # noqa: E402

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
    from .histcap.worker import CaptureWorker as _CaptureWorker
    from .histcap import api as _histcap_api
    histcap_worker = _CaptureWorker()
    _histcap_api.bind_worker(histcap_worker)
    app.include_router(_histcap_api.router)
except Exception as _e:  # never let capture wiring break the app
    histcap_worker = None
    print(f"[histcap] disabled: {type(_e).__name__}: {_e}")

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

# ---- Smart Scalper paper-trade scheduler (spec section 48). PAPER only, DISARMED
# by default — wiring it in does not start opening positions on its own.
try:
    from .smart_index_scalper.scheduler import SCHEDULER as smart_scalper_scheduler
except Exception as _e:
    smart_scalper_scheduler = None
    print(f"[smart_index_scalper] scheduler disabled: {type(_e).__name__}: {_e}")


@app.on_event("startup")
async def _start_scalp_runner():
    runtime.scalp_runner.start()
    runtime.autoscalp.start()
    if histcap_worker is not None:
        try:
            histcap_worker.start()
        except Exception as e:
            print(f"[histcap] start failed: {type(e).__name__}: {e}")
    if smart_scalper_scheduler is not None:
        try:
            smart_scalper_scheduler.start()
        except Exception as e:
            print(f"[smart_scalper_scheduler] start failed: {type(e).__name__}: {e}")


@app.on_event("shutdown")
async def _stop_scalp_runner():
    await runtime.scalp_runner.stop()
    await runtime.autoscalp.stop()
    if histcap_worker is not None:
        try:
            await histcap_worker.stop()
        except Exception:
            pass
    if smart_scalper_scheduler is not None:
        try:
            await smart_scalper_scheduler.stop()
        except Exception:
            pass


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    auth = websocket.headers.get("authorization", "")
    if not ((API_TOKEN and websocket.query_params.get("token") == API_TOKEN)
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
