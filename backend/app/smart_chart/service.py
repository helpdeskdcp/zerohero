"""Smart Chart orchestration: compile / indicator run / backtest. Pure Python, no HTTP types (routes.py maps errors)."""
from __future__ import annotations

import re
import time

from . import backtest, candles, capabilities, config, runner, store
from .symbols import Instrument

TIMEFRAMES = tuple(candles.TIMEFRAMES)
_VERSION = re.compile(r"//\s*@version\s*=\s*(\d+)")
# PineTS-style timeframe code for the worker ('D' for daily, minutes otherwise)
PINE_TF = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "1d": "D"}


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def validate_source(source) -> str:
    if not isinstance(source, str) or not source.strip():
        raise ServiceError(422, "EMPTY_SCRIPT", "script source is empty")
    if len(source.encode("utf-8")) > config.MAX_SCRIPT_BYTES:
        raise ServiceError(413, "SCRIPT_TOO_LARGE", f"script exceeds {config.MAX_SCRIPT_BYTES} bytes")
    if "\x00" in source:
        raise ServiceError(422, "BAD_SCRIPT", "script contains a NUL byte")
    return source


def pine_version(source: str) -> int | None:
    m = _VERSION.search(source)
    return int(m.group(1)) if m else None


def _runner_call(owner: str, req: dict, count: bool = True) -> dict:
    try:
        return runner.execute(req, owner, count=count)
    except runner.RunnerError as e:
        status = {"busy": 503, "rate_limited": 429, "timeout": 408, "too_large": 413, "unavailable": 503}.get(e.kind, 500)
        raise ServiceError(status, f"RUNTIME_{e.kind.upper()}", str(e)) from None


def compile_script(owner: str, source: str) -> dict:
    validate_source(source)
    ver = pine_version(source)
    static = capabilities.diagnostics_for(source)
    if ver is not None and ver < 5:
        return {"ok": False, "kind": None, "title": None, "inputs": [], "pine_version": ver, "capabilities": capabilities.scan(source),
                "diagnostics": [{"severity": "error", "code": "UNSUPPORTED_VERSION", "line": 1, "column": 1,
                                 "message": f"Pine Script v{ver} is not supported; Smart Chart runs v5 and v6 (add //@version=5 or 6)"}]}
    res = _runner_call(owner, {"op": "compile", "source": source})
    diags = list(res.get("diagnostics") or []) + static
    return {"ok": bool(res.get("ok")), "kind": res.get("kind"), "title": res.get("title"), "inputs": res.get("inputs") or [],
            "pine_version": ver, "capabilities": capabilities.scan(source), "diagnostics": diags}


def load_bars(inst: Instrument, tf: str, bars: int, *, closed_only: bool, before_ms: int | None = None) -> tuple[list[dict], dict]:
    if tf not in candles.TIMEFRAMES:
        raise ServiceError(422, "BAD_TIMEFRAME", f"timeframe must be one of {', '.join(TIMEFRAMES)}")
    try:
        got = candles.get_candles(inst, tf, bars, before_ms)
    except candles.CandleError as e:
        raise ServiceError(502, "NO_MARKET_DATA", str(e)) from None
    rows, meta = got["candles"], got["meta"]
    if closed_only and meta.get("last_bar_forming") and rows:
        rows = rows[:-1]
    if len(rows) < 5:
        raise ServiceError(422, "NOT_ENOUGH_DATA", f"only {len(rows)} bars available for {inst.id} {tf}")
    return rows, meta


def run_indicator(owner: str, source: str, inst: Instrument, tf: str, bars: int, inputs: dict | None = None,
                  props: dict | None = None, script_id: str | None = None) -> dict:
    validate_source(source)
    rows, meta = load_bars(inst, tf, bars, closed_only=False)
    jid = store.start_job(owner, script_id, "indicator", inst.id, tf)
    t0 = time.monotonic()
    try:
        res = _runner_call(owner, {"op": "run", "source": source, "candles": rows, "tf": PINE_TF[tf], "symbol": inst.id,
                                   "inputs": inputs or {}, "props": props or {}})
    except ServiceError as e:
        store.finish_job(jid, "ERROR", int((time.monotonic() - t0) * 1000), e.message)
        raise
    dur = int((time.monotonic() - t0) * 1000)
    if not res.get("ok"):
        store.finish_job(jid, "ERROR", dur, (res.get("diagnostics") or [{}])[0].get("message"))
        raise ServiceError(422, "SCRIPT_ERROR", "script failed to run", res.get("diagnostics"))
    res["data_meta"] = meta
    res["duration_ms"] = dur
    res["bars"] = len(rows)
    res["job_id"] = jid
    # bounded summary only: last value per plot (full series are returned to the caller, not stored)
    store.finish_job(jid, "OK", dur, None, {"plots": {p["title"]: next((v for v in reversed(p["values"]) if v is not None), None) for p in res["plots"]}, "bars": len(rows)})
    return res


def _strategy_run(owner: str, source: str, rows: list[dict], inst: Instrument, tf: str, inputs, props, count: bool = True) -> dict:
    res = _runner_call(owner, {"op": "run", "source": source, "candles": rows, "tf": PINE_TF[tf], "symbol": inst.id,
                               "inputs": inputs or {}, "props": props or {}}, count)
    if not res.get("ok"):
        raise ServiceError(422, "SCRIPT_ERROR", "script failed to run", res.get("diagnostics"))
    if res.get("kind") != "strategy" or not res.get("strategy"):
        raise ServiceError(422, "NOT_A_STRATEGY", "backtests need a strategy() script (this one is an indicator)")
    return res


def run_backtest(owner: str, source: str, script_id: str, script_version: int, inst: Instrument, tf: str, bars: int,
                 inputs: dict | None, props: dict | None, causality: bool = True) -> dict:
    validate_source(source)
    rows, meta = load_bars(inst, tf, bars, closed_only=True)
    jid = store.start_job(owner, script_id, "backtest", inst.id, tf)
    t0 = time.monotonic()
    try:
        res = _strategy_run(owner, source, rows, inst, tf, inputs, props)
        st = res["strategy"]
        stats, eq = backtest.compute_stats(rows, st["closed_trades"], st["open_trades"], st["config"], st["pine_summary"])
        check = None
        if causality and len(rows) >= 60:
            parts = []
            for cut in backtest.pick_cuts(st, len(rows)):
                trunc = _strategy_run(owner, source, rows[:cut], inst, tf, inputs, props, count=False)["strategy"]
                parts.append(backtest.causality_diff(st, trunc, cut))
            check = backtest.merge_causality(parts) if parts else {"ok": True, "cuts_tested": 0, "failing_cuts": [], "mismatches": [], "note": "no trades to test"}
            if not check["ok"]:
                stats["warnings"].append("causality check FAILED: re-running on a shorter history changed past trades - the script likely repaints or peeks at future bars")
    except ServiceError as e:
        store.finish_job(jid, "ERROR", int((time.monotonic() - t0) * 1000), e.message)
        raise
    dur = int((time.monotonic() - t0) * 1000)
    store.finish_job(jid, "OK", dur, None, {"net_profit": stats["net_profit"], "trades": stats["trades"]})
    equity = [{"time": rows[i]["time"], "equity": eq[i]} for i in range(len(rows))]
    out = {"stats": stats, "trades": st["closed_trades"] + st["open_trades"], "equity": equity, "causality": check,
           "strategy_config": st["config"], "assumptions": backtest.ASSUMPTIONS, "data_meta": meta,
           "range": {"from_ms": rows[0]["time"], "to_ms": rows[-1]["time"], "bars": len(rows)}, "warnings": res.get("warnings") or [],
           "duration_ms": dur}
    out["backtest_id"] = store.save_backtest(owner, script_id, script_version, inst.id, tf, len(rows),
                                             {"inputs": inputs or {}, "props": props or {}}, stats, out["trades"], equity,
                                             {k: meta.get(k) for k in ("source", "native_interval", "aggregated", "volume_available", "server_time_ms")},
                                             backtest.ASSUMPTIONS)
    return out
