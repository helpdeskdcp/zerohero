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


def _last_close() -> dict:
    """Latest completed NSE session that the HISTORICAL store actually holds, plus the calendar's own latest session so a data lag is visible (never hidden)."""
    import sqlite3
    from datetime import date, timedelta
    from . import indicators as I
    from .historical import DB
    from .. import market_calendar as mc
    today = datetime.now(I.IST); cal = None; d = today.date()
    if today.hour * 60 + today.minute < 15 * 60 + 30: d -= timedelta(days=1)                      # today's session is not complete yet
    for _ in range(10):
        if d.weekday() < 5 and not mc.is_holiday(d, "NSE"): cal = d; break
        d -= timedelta(days=1)
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    try:
        hi = int(time.time()); lo = hi - 86400 * 12
        days = sorted({datetime.fromtimestamp(r[0], I.IST).strftime("%Y-%m-%d") for r in c.execute("SELECT ts FROM bars WHERE sym='NIFTY' AND ts BETWEEN ? AND ? AND ts%1800=0", (lo, hi))})
    finally:
        c.close()
    if not days: raise HTTPException(503, "no historical session available for last_close")
    sess = days[-1]; as_of = datetime.fromisoformat(sess + "T15:30:00+05:30")
    return {"session": sess, "as_of": as_of.isoformat(), "calendar_last_session": str(cal) if cal else None, "data_lag": bool(cal and str(cal) != sess)}


def _meta(mode: str, out: dict, ad, extra: dict | None = None) -> dict:
    from .. import market_calendar as mc
    h = out.get("health", {}); kind = out.get("data_kind")
    try: open_now = bool(mc.is_trading("NSE"))
    except Exception: open_now = None
    live = mode == "live"
    fresh = "FRESH" if live and h.get("quotes_valid", 0) and not h.get("quotes_invalid") else ("STALE_OR_PARTIAL" if live else "NOT_LIVE")
    m = {"mode": mode, "data_kind": kind, "timezone": "Asia/Kolkata", "source": getattr(ad, "name", None), "last_update_utc": datetime.fromtimestamp(out["ts"], tz=__import__("datetime").timezone.utc).isoformat(),
         "freshness": fresh, "market_open_now": open_now, "signals": {"confirmed_closed_candle": True, "provisional_intrabar": "NOT_COMPUTED"}}
    if live and open_now is False: m["note"] = "NSE is closed: fresh live updates are not available; use LAST CLOSE or REPLAY"
    if extra: m.update(extra)
    return m


@router.get("/overview")
def overview(mode: str = "live", as_of: str | None = None, persist: bool = False):
    """mode=live (adapter) | last_close (latest completed stored session) | replay (as_of). Anything not live is labelled HISTORICAL and never as live."""
    assert_paper_only(); m = _master()
    if mode not in ("live", "last_close", "replay"): raise HTTPException(400, "mode must be live, last_close or replay")
    extra = None
    if mode == "last_close":
        lc = _last_close(); as_of = lc["as_of"]; extra = {"session": lc["session"], "calendar_last_session": lc["calendar_last_session"], "data_lag": lc["data_lag"]}
        if lc["data_lag"]: extra["note"] = f"stored history ends {lc['session']} but the exchange's latest completed session is {lc['calendar_last_session']}"
    ad = _adapter("replay" if mode in ("last_close", "replay") else "live", as_of)
    if ad is None: return {"status": "NO_ADAPTER_CONFIGURED", "data_kind": "NONE", "meta": {"mode": mode, "data_kind": "NONE", "freshness": "NO_DATA", "timezone": "Asia/Kolkata"}, "detail": "no live market-data adapter is configured/tested; use mode=last_close or mode=replay", "master": {"weights_status": m["weights_status"], "count": m["count"]}}
    now = datetime.fromisoformat(as_of).timestamp() if mode != "live" else time.time()
    try:
        out = service.evaluate(ad, m, now, max_age=120 if mode != "live" else None, persist=persist)
        out["meta"] = _meta(mode, out, ad, extra); return out
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
