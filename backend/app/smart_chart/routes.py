"""/api/smart-chart/* - authenticated by the app-wide gate (Basic or Bearer, see main._AuthGateMiddleware).

Every object route is owner-scoped (owner = the authenticated principal); another owner's id behaves exactly like a missing id (404).
Mutating routes (POST/PUT/DELETE) additionally require the header `X-SmartChart-CSRF: 1`: a browser attaches Basic credentials to
cross-site requests automatically, but cannot add a custom header cross-site without a CORS preflight, which this app does not grant.
Error envelope: {"error": {"code": str, "message": str, "details": any}}.
"""
from __future__ import annotations

import base64
import functools
import json
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import analysis, candles, capabilities, config, paper, runner, service, store, symbols, templates, upstox_ext
from .service import ServiceError

router = APIRouter(prefix="/api/smart-chart", tags=["smart-chart"])

LICENSE_NOTICE = {
    "components": [
        {"name": "@luxalgo/vela", "version": "0.9.0", "license": "Apache-2.0", "note": "attribution watermark must stay visible (NOTICE)"},
        {"name": "@luxalgo/vela-pinets", "version": "0.3.0", "license": "AGPL-3.0-only"},
        {"name": "pinets", "version": "0.13.0", "license": "AGPL-3.0-only"},
    ],
    "agpl_obligation": "Users who interact with this service over the network are entitled to the Corresponding Source of the AGPL-covered "
                       "Smart Chart code (this repository's app/smart_chart and frontend/static/js/smartchart.js, including any modifications). "
                       "See backend/SMART_CHART.md.",
    "disclaimer": "Pine Script and TradingView are trademarks of TradingView, Inc. PineTS/Vela are independent LuxAlgo projects, not affiliated with or endorsed by TradingView. "
                  "Compatibility is practical, not complete.",
}


# ------------------------------------------------------------------------------------ plumbing
def owner_of(request: Request) -> str:
    """Principal behind the already-authenticated request (the middleware rejected anything else)."""
    a = request.headers.get("authorization", "")
    if a.lower().startswith("basic "):
        try:
            return base64.b64decode(a[6:].strip()).decode("utf-8").split(":", 1)[0][:64] or "admin"
        except Exception:  # noqa: BLE001
            return "admin"
    return "api-token"


def csrf(request: Request, x_smartchart_csrf: str | None = Header(default=None)) -> None:
    if request.method not in ("GET", "HEAD", "OPTIONS") and x_smartchart_csrf != "1":
        raise HTTPException(403, detail={"error": {"code": "CSRF", "message": "missing X-SmartChart-CSRF header", "details": None}})


def err(status: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "details": details}}, status_code=status)


def api(fn):
    """Map ServiceError/SymbolError to the error envelope; unexpected errors become a generic 500 without internals."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except ServiceError as e:
            return err(e.status, e.code, e.message, e.details)
        except symbols.SymbolError as e:
            return err(422, "BAD_SYMBOL", str(e))
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger("smart_chart").exception("unhandled error in %s", fn.__name__)
            return err(500, "INTERNAL", f"internal error ({type(e).__name__})")
    return wrapper


_hits: dict[str, deque] = defaultdict(deque)


def throttle(owner: str, bucket: str, per_min: int) -> None:
    now = time.monotonic()
    q = _hits[f"{owner}:{bucket}"]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= per_min:
        raise ServiceError(429, "RATE_LIMITED", f"too many {bucket} requests; slow down")
    q.append(now)


def inst_of(symbol: str):
    return symbols.resolve(symbol)


def tf_check(tf: str) -> str:
    if tf not in candles.TIMEFRAMES:
        raise ServiceError(422, "BAD_TIMEFRAME", f"timeframe must be one of {', '.join(candles.TIMEFRAMES)}")
    return tf


# ------------------------------------------------------------------------------------ models
class ScriptIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    kind: str = Field(pattern="^(indicator|strategy)$")
    source: str
    inputs: dict = Field(default_factory=dict)


class ScriptPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    kind: str | None = Field(default=None, pattern="^(indicator|strategy)$")
    source: str | None = None
    inputs: dict | None = None
    favorite: bool | None = None


class CompileIn(BaseModel):
    source: str


class RunIn(BaseModel):
    script_id: str | None = None
    source: str | None = None
    symbol: str = "NIFTY"
    tf: str = "5m"
    bars: int = Field(default=config.DEFAULT_CANDLES, ge=10, le=config.MAX_CANDLES)
    inputs: dict = Field(default_factory=dict)
    props: dict = Field(default_factory=dict)


class BacktestIn(BaseModel):
    script_id: str
    symbol: str = "NIFTY"
    tf: str = "5m"
    bars: int = Field(default=3000, ge=60, le=config.MAX_CANDLES)
    inputs: dict = Field(default_factory=dict)
    initial_capital: float | None = Field(default=None, ge=1000, le=1e9)
    commission_pct: float | None = Field(default=None, ge=0, le=5)
    slippage_ticks: float | None = Field(default=None, ge=0, le=1000)
    causality_check: bool = True


class PaperIn(BaseModel):
    script_id: str
    symbol: str = "NIFTY"
    tf: str = "5m"
    name: str | None = Field(default=None, max_length=120)
    capital: float = Field(default=100000, ge=1000, le=1e9)
    commission_pct: float = Field(default=0.03, ge=0, le=5)
    slippage_ticks: float = Field(default=1, ge=0, le=1000)
    daily_loss_limit: float = Field(default=5000, ge=0, le=1e9)


class AnalysisIn(BaseModel):
    symbol: str = "NIFTY"
    tf: str = "5m"
    script_id: str | None = None
    ai: bool = False


class PrefIn(BaseModel):
    value: object


# ------------------------------------------------------------------------------------ health / meta
@router.get("/health")
@api
def health(request: Request):
    rt = runner.available()
    up = upstox_ext.auth_summary()
    return {"ok": bool(rt["ok"] and up.get("ok")), "runtime": rt, "upstox": up, "db_schema_version": store.schema_version(),
            "cache": candles.cache_stats(), "execution": {"mode": "PAPER_ONLY", "live_orders": False, "note": "no broker/order path exists in this feature"},
            "live_transport": "rest-poll", "limits": {"max_script_bytes": config.MAX_SCRIPT_BYTES, "max_candles": config.MAX_CANDLES,
                                                      "exec_timeout_sec": config.EXEC_TIMEOUT_SEC, "runs_per_minute": config.RUNS_PER_MINUTE,
                                                      "max_concurrent_runs": config.MAX_CONCURRENT_RUNS},
            "server_time_ms": int(time.time() * 1000)}


@router.get("/meta")
@api
def meta(request: Request):
    reg = capabilities.registry()
    return {"timeframes": list(candles.TIMEFRAMES), "indices": list(symbols.INDICES), "equities": "NSE:<EQ ticker>, searchable via /symbols",
            "pine": {"versions": reg["pine_versions"], "pinets_version": reg["pinets_version"], "tiers": reg["tiers"], "notice": reg["notice"]},
            "not_supported": ["1-second bars (no historical source; Upstox serves 1m minimum)", "MCX / futures / options contracts", "week/month bars",
                              "request.security() to other symbols or lower timeframes", "live order routing (paper only)"],
            "license": LICENSE_NOTICE}


@router.get("/license")
@api
def license_info(request: Request):
    return LICENSE_NOTICE


@router.get("/capabilities")
@api
def capabilities_registry(request: Request):
    return capabilities.registry()


@router.get("/templates")
@api
def get_templates(request: Request):
    return {"templates": templates.TEMPLATES}


@router.get("/symbols")
@api
def search_symbols(request: Request, q: str = Query(default="", max_length=40), limit: int = Query(default=25, ge=1, le=100)):
    return {"symbols": symbols.search(q, limit)}


@router.get("/symbols/all")
@api
def all_symbols(request: Request):
    """Full picker list (indices + NSE EQ) for the chart's symbol search; the browser caches it."""
    return {"symbols": symbols.all_symbols()}


# ------------------------------------------------------------------------------------ market data
@router.get("/candles")
@api
def get_candles(request: Request, symbol: str = Query(max_length=40), tf: str = "5m", limit: int = Query(default=config.DEFAULT_CANDLES, ge=1, le=config.MAX_CANDLES),
                before: int | None = Query(default=None, ge=0)):
    owner = owner_of(request)
    throttle(owner, "candles", 240)
    inst = inst_of(symbol)
    try:
        return candles.get_candles(inst, tf_check(tf), limit, before)
    except candles.CandleError as e:
        raise ServiceError(502, "NO_MARKET_DATA", str(e)) from None


@router.get("/live")
@api
def live(request: Request, symbol: str = Query(max_length=40), tf: str = "5m", since: int = Query(default=0, ge=0)):
    """Cheap poll for the forming/new candles: everything with open time >= `since` (+ the bar before it, for continuity)."""
    owner = owner_of(request)
    throttle(owner, "live", 300)
    inst = inst_of(symbol)
    try:
        got = candles.get_candles(inst, tf_check(tf), 6)
    except candles.CandleError as e:
        raise ServiceError(502, "NO_MARKET_DATA", str(e)) from None
    got["candles"] = [c for c in got["candles"] if c["time"] >= since] or got["candles"][-1:]
    return got


# ------------------------------------------------------------------------------------ scripts
@router.get("/scripts")
@api
def scripts_list(request: Request, kind: str | None = Query(default=None, pattern="^(indicator|strategy)$"), q: str | None = Query(default=None, max_length=80),
                 limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    return store.list_scripts(owner_of(request), kind, q, limit, offset)


@router.post("/scripts", status_code=201, dependencies=[Depends(csrf)])
@api
def scripts_create(request: Request, body: ScriptIn):
    owner = owner_of(request)
    service.validate_source(body.source)
    if store.count_scripts(owner) >= config.MAX_SCRIPTS_PER_OWNER:
        raise ServiceError(409, "TOO_MANY_SCRIPTS", f"limit of {config.MAX_SCRIPTS_PER_OWNER} scripts reached")
    return store.create_script(owner, body.name.strip(), body.description, body.kind, body.source, body.inputs)


@router.get("/scripts/{sid}")
@api
def scripts_get(request: Request, sid: str):
    s = store.get_script(owner_of(request), sid)
    if not s:
        raise ServiceError(404, "NOT_FOUND", "script not found")
    s["last_compile"] = store.last_compile(owner_of(request), sid)
    return s


@router.put("/scripts/{sid}", dependencies=[Depends(csrf)])
@api
def scripts_update(request: Request, sid: str, body: ScriptPatch):
    owner = owner_of(request)
    if body.source is not None:
        service.validate_source(body.source)
    s = store.update_script(owner, sid, name=body.name, description=body.description, kind=body.kind, source=body.source,
                            inputs=body.inputs, favorite=body.favorite)
    if not s:
        raise ServiceError(404, "NOT_FOUND", "script not found")
    return s


@router.delete("/scripts/{sid}", dependencies=[Depends(csrf)])
@api
def scripts_delete(request: Request, sid: str):
    if not store.delete_script(owner_of(request), sid):
        raise ServiceError(404, "NOT_FOUND", "script not found")
    return {"deleted": sid}


@router.get("/scripts/{sid}/versions")
@api
def scripts_versions(request: Request, sid: str):
    v = store.list_versions(owner_of(request), sid)
    if v is None:
        raise ServiceError(404, "NOT_FOUND", "script not found")
    return {"versions": v}


@router.get("/scripts/{sid}/versions/{version}")
@api
def scripts_version(request: Request, sid: str, version: int):
    v = store.get_version(owner_of(request), sid, version)
    if not v:
        raise ServiceError(404, "NOT_FOUND", "version not found")
    return v


@router.post("/compile", dependencies=[Depends(csrf)])
@api
def compile_adhoc(request: Request, body: CompileIn):
    return service.compile_script(owner_of(request), body.source)


@router.post("/scripts/{sid}/compile", dependencies=[Depends(csrf)])
@api
def compile_saved(request: Request, sid: str):
    owner = owner_of(request)
    s = store.get_script(owner, sid)
    if not s:
        raise ServiceError(404, "NOT_FOUND", "script not found")
    res = service.compile_script(owner, s["source"])
    store.record_compile(owner, sid, s["version"], res["ok"], res["diagnostics"], res["inputs"])
    if res["ok"] and res["kind"] and res["kind"] != s["kind"]:
        res["diagnostics"].append({"severity": "warning", "code": "KIND_MISMATCH", "line": None, "column": None,
                                   "message": f"saved as {s['kind']} but the script declares {res['kind']}"})
    return res


# ------------------------------------------------------------------------------------ execution
def _resolve_source(owner: str, script_id: str | None, source: str | None) -> tuple[str, dict | None]:
    if script_id:
        s = store.get_script(owner, script_id)
        if not s:
            raise ServiceError(404, "NOT_FOUND", "script not found")
        return s["source"], s
    if source is None:
        raise ServiceError(422, "NO_SCRIPT", "provide script_id or source")
    return service.validate_source(source), None


@router.post("/run", dependencies=[Depends(csrf)])
@api
def run(request: Request, body: RunIn):
    owner = owner_of(request)
    src, s = _resolve_source(owner, body.script_id, body.source)
    inputs = body.inputs or (s["inputs"] if s else {})
    return service.run_indicator(owner, src, inst_of(body.symbol), tf_check(body.tf), body.bars, inputs, body.props, s["id"] if s else None)


@router.post("/backtests", dependencies=[Depends(csrf)])
@api
def backtests_run(request: Request, body: BacktestIn):
    owner = owner_of(request)
    s = store.get_script(owner, body.script_id)
    if not s:
        raise ServiceError(404, "NOT_FOUND", "script not found")
    props = {k: v for k, v in (("initial_capital", body.initial_capital), ("slippage", body.slippage_ticks)) if v is not None}
    if body.commission_pct is not None:
        props.update(commission_type="percent", commission_value=body.commission_pct)
    return service.run_backtest(owner, s["source"], s["id"], s["version"], inst_of(body.symbol), tf_check(body.tf), body.bars,
                                body.inputs or s["inputs"], props, body.causality_check)


@router.get("/backtests")
@api
def backtests_list(request: Request, script_id: str | None = None, limit: int = Query(default=30, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return {"backtests": store.list_backtests(owner_of(request), script_id, limit, offset)}


@router.get("/backtests/{bid}")
@api
def backtests_get(request: Request, bid: str):
    b = store.get_backtest(owner_of(request), bid)
    if not b:
        raise ServiceError(404, "NOT_FOUND", "backtest not found")
    return b


# ------------------------------------------------------------------------------------ paper trading
def _pf_or_404(owner: str, pid: str) -> dict:
    pf = store.get_portfolio(owner, pid)
    if not pf:
        raise ServiceError(404, "NOT_FOUND", "portfolio not found")
    return pf


@router.get("/paper")
@api
def paper_list(request: Request):
    return {"portfolios": store.list_portfolios(owner_of(request)), "mode": "PAPER_ONLY"}


@router.post("/paper", status_code=201, dependencies=[Depends(csrf)])
@api
def paper_create(request: Request, body: PaperIn):
    owner = owner_of(request)
    s = store.get_script(owner, body.script_id)
    if not s:
        raise ServiceError(404, "NOT_FOUND", "script not found")
    inst = inst_of(body.symbol)
    return paper.create(owner, s, inst, tf_check(body.tf), body.capital, body.commission_pct, body.slippage_ticks, body.daily_loss_limit, body.name)


@router.get("/paper/{pid}")
@api
def paper_get(request: Request, pid: str):
    return _pf_or_404(owner_of(request), pid)


@router.post("/paper/{pid}/start", dependencies=[Depends(csrf)])
@api
def paper_start(request: Request, pid: str):
    return paper.start(owner_of(request), pid)


@router.post("/paper/{pid}/stop", dependencies=[Depends(csrf)])
@api
def paper_stop(request: Request, pid: str):
    return paper.stop(owner_of(request), pid)


@router.post("/paper/{pid}/step", dependencies=[Depends(csrf)])
@api
def paper_step(request: Request, pid: str):
    owner = owner_of(request)
    throttle(owner, "paper-step", 60)
    return paper.step(owner, pid)


@router.delete("/paper/{pid}", dependencies=[Depends(csrf)])
@api
def paper_delete(request: Request, pid: str):
    if not store.delete_portfolio(owner_of(request), pid):
        raise ServiceError(404, "NOT_FOUND", "portfolio not found")
    return {"deleted": pid}


@router.get("/paper/{pid}/trades")
@api
def paper_trades(request: Request, pid: str, limit: int = Query(default=200, ge=1, le=1000), offset: int = Query(default=0, ge=0)):
    _pf_or_404(owner_of(request), pid)
    return {"trades": store.list_paper_trades(pid, limit, offset)}


# ------------------------------------------------------------------------------------ analysis / prefs / ops
@router.post("/analysis", dependencies=[Depends(csrf)])
@api
def analysis_run(request: Request, body: AnalysisIn):
    owner = owner_of(request)
    throttle(owner, "analysis", 20)
    inst = inst_of(body.symbol)
    tf = tf_check(body.tf)
    got = candles.get_candles(inst, tf, 600)
    sigs, validation = [], None
    if body.script_id:
        s = store.get_script(owner, body.script_id)
        if not s:
            raise ServiceError(404, "NOT_FOUND", "script not found")
        res = service.run_indicator(owner, s["source"], inst, tf, 600, s["inputs"], {}, s["id"])
        sigs = analysis.signals_from_plots(res["plots"], res["times"])
        bts = [b for b in store.list_backtests(owner, s["id"], 20) if b["symbol"] == inst.id and b["tf"] == tf]
        if bts:
            st = bts[0]["stats"]
            validation = {"backtest_id": bts[0]["id"], "trades": st.get("trades"), "win_rate_pct": st.get("win_rate_pct"), "profit_factor": st.get("profit_factor"),
                          "net_profit": st.get("net_profit"), "note": "past simulation, not a forecast" + ("; fewer than 30 trades" if (st.get("trades") or 0) < 30 else "")}
    summary = analysis.deterministic(got["candles"], got["meta"], sigs, validation)
    summary["symbol"], summary["tf"] = inst.id, tf
    summary["ai"] = analysis.narrative(summary) if body.ai else {"status": "NOT_REQUESTED", "narrative": None}
    return summary


@router.get("/prefs")
@api
def prefs_get(request: Request):
    return store.get_prefs(owner_of(request))


@router.put("/prefs/{key}", dependencies=[Depends(csrf)])
@api
def prefs_set(request: Request, key: str, body: PrefIn):
    if not key.replace("_", "").replace("-", "").isalnum() or len(key) > 40:
        raise ServiceError(422, "BAD_KEY", "invalid preference key")
    if len(json.dumps(body.value)) > 20_000:
        raise ServiceError(413, "TOO_LARGE", "preference too large")
    store.set_pref(owner_of(request), key, body.value)
    return {"ok": True}


@router.get("/jobs")
@api
def jobs(request: Request, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    return {"jobs": store.list_jobs(owner_of(request), limit, offset)}


@router.get("/audit")
@api
def audit_log(request: Request, limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)):
    return {"events": store.list_audit(owner_of(request), limit, offset)}
