"""/api/nifty-direction/* — read-only views + master refresh. No order endpoint exists. Data kinds: LIVE (a configured, tested adapter), HISTORICAL (replay), MOCK."""
from __future__ import annotations

import os
import time
from datetime import datetime

from fastapi import APIRouter, HTTPException

from . import adapters as A, constituents as C, scoring as S, service, store, strategy as ST
from .paper import PaperConfig, assert_paper_only

router = APIRouter(prefix="/api/nifty-direction", tags=["nifty-direction"])
_replay_cache: dict = {}


def _master() -> dict:
    m = C.current()
    if m is None: raise HTTPException(503, "constituent master not built yet: POST /api/nifty-direction/refresh-master")
    return m


def _adapter(mode: str, as_of: str | None):
    if mode == "replay":
        from .historical import HistoricalAdapter
        ts = datetime.fromisoformat(as_of).timestamp() if as_of else None
        if ts is None: raise HTTPException(400, "replay needs as_of=YYYY-MM-DDTHH:MM:SS+05:30")
        return HistoricalAdapter(ts)
    name = os.environ.get("NIFTY_DIR_ADAPTER", "").strip()
    if not name: return None
    return A.get(name)


@router.get("/status")
def status():
    m = C.current()
    return {"paper_only": True, "live_orders_enabled": False, "adapters_registered": A.names(), "live_adapter_configured": os.environ.get("NIFTY_DIR_ADAPTER") or None,
            "master": None if m is None else {k: m[k] for k in ("count", "weights_status", "weights_notes", "csv_sha256", "weights_sha256", "fetched_at", "source")},
            "blockers": ([] if m is None else C.validate(m)) or None, "weights_howto": "drop a symbol,weight_pct CSV (50 rows, sum ~100, from NSE Indices) at data/nifty_direction/weights.csv then POST /refresh-master"}


@router.get("/overview")
def overview(mode: str = "live", as_of: str | None = None, persist: bool = False):
    assert_paper_only(); m = _master(); ad = _adapter(mode, as_of)
    if ad is None: return {"status": "NO_ADAPTER_CONFIGURED", "data_kind": "NONE", "detail": "no live market-data adapter is configured/tested; use mode=replay or set NIFTY_DIR_ADAPTER", "master": {"weights_status": m["weights_status"], "count": m["count"]}}
    now = datetime.fromisoformat(as_of).timestamp() if mode == "replay" else time.time()
    try: return service.evaluate(ad, m, now, max_age=120 if mode == "replay" else None, persist=persist)
    finally:
        close = getattr(ad, "close", None)
        if callable(close): close()


@router.get("/signals")
def signals(limit: int = 100): return {"rows": store.rows("signals", min(limit, 500))}


@router.get("/health-events")
def health_events(limit: int = 100): return {"rows": store.rows("health_events", min(limit, 500))}


@router.get("/paper")
def paper(limit: int = 100):
    r = store.rows("paper_trades", min(limit, 500)); net = sum(x["net"] or 0 for x in r)
    return {"paper_only": True, "rows": r, "net": net, "note": "hypothetical; costs are an assumption (cost_bps_round_trip), not a validated NSE cost model"}


@router.get("/config")
def config():
    return {"strategy": store.latest_config("strategy") or ST.StrategyConfig().to_dict(), "score": store.latest_config("score") or S.ScoreConfig().to_dict(), "paper": store.latest_config("paper") or PaperConfig().to_dict()}


@router.get("/replay")
def replay_view(sessions: int = 20, end: str | None = None):
    from . import replay as R
    import sqlite3
    from .historical import DB
    sessions = max(2, min(sessions, 60)); key = (sessions, end)
    if key in _replay_cache and time.time() - _replay_cache[key][0] < 600: return _replay_cache[key][1]
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    hi = int(datetime.fromisoformat(end).timestamp()) if end else int(time.time()); lo = hi - 86400 * (sessions * 2 + 10)
    days = sorted({datetime.fromtimestamp(r[0], R.I.IST).strftime("%Y-%m-%d") for r in c.execute("SELECT ts FROM bars WHERE sym='NIFTY' AND ts BETWEEN ? AND ? AND ts%1800=0", (lo, hi))}); c.close()
    days = days[-sessions:]
    res = R.replay(days); out = {"data_kind": "HISTORICAL", "scope": "15m-trend + 5m-entry sub-engine on NIFTY index bars only (contribution/breadth/sector not replayable: no official weights)", "sessions": days,
                                 "summary": res["summary"], "split": R.oos_split(res), "trades": res["trades"][-50:]}
    _replay_cache[key] = (time.time(), out); return out


@router.post("/refresh-master")
def refresh_master():
    try: m = C.refresh()
    except Exception as e: raise HTTPException(502, f"constituent refresh failed: {type(e).__name__}")
    return {"count": m["count"], "weights_status": m["weights_status"], "csv_sha256": m["csv_sha256"], "problems": C.validate(m)}
