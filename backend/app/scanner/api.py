"""/api/scanner/*: read-only views of the in-house scanner (auth'd by the normal gate)."""
from __future__ import annotations

import json
import os
import time

import pandas as pd
from fastapi import APIRouter

from . import backtest as BT, data as D, store
from .library import SCANS

router = APIRouter(prefix="/api/scanner", tags=["scanner"])
RESULTS = os.path.join(D.ROOT, "data", "research", "scanner", "results_backtest.json")
_cache: dict = {}


def forward_report(path: str | None = None, loader=None) -> dict:
    """Realised forward result of the stored hits: entry open of D+1, exit close of D+h (h = 1, 3, 5, 10 when those bars exist), net of the spec's assumed cost."""
    loader = loader or D.load_daily
    with store.conn(path) as c: hits = [dict(r) for r in c.execute("SELECT inst, scan, side, signal_date, close FROM hits ORDER BY signal_date DESC").fetchall()]
    frames, rows = {}, []
    for h in hits:
        if h["inst"] not in frames:
            try: frames[h["inst"]] = loader(h["inst"])
            except Exception: frames[h["inst"]] = None
        df = frames[h["inst"]]
        if df is None: continue
        idx = df.index[pd.to_datetime(df["date"]) == pd.Timestamp(h["signal_date"])]
        if not len(idx): continue
        i = int(idx[0]); sg = 1.0 if h["side"] == "LONG" else -1.0; r = {k: h[k] for k in ("inst", "scan", "side", "signal_date")}
        for hz in BT.HORIZONS:
            r[f"h{hz}"] = round(sg * (df["c"].iloc[i + hz] / df["o"].iloc[i + 1] - 1) * 1e4 - BT.COST[h["inst"]], 1) if i + hz < len(df) else None
        rows.append(r)
    return {"hits": len(hits), "evaluated": rows[:200], "note": "net bps after the assumed cost; None = horizon not yet reached; the historical verdicts are in /api/scanner/backtest"}


@router.get("/scans")
def scans(): return {"scans": [{"name": k, "side": v[0], "expr": v[1]} for k, v in SCANS.items()], "instruments": D.INSTRUMENTS}


@router.get("/hits")
def hits(limit: int = 100):
    with store.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT inst, scan, side, signal_date, close FROM hits ORDER BY signal_date DESC, id DESC LIMIT ?", (min(limit, 500),)).fetchall()]
        last = c.execute("SELECT ts, group_name FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return {"hits": rows, "last_run": dict(last) if last else None}


@router.get("/backtest")
def backtest():
    try: return json.load(open(RESULTS))
    except Exception: return {"error": "results_backtest.json not found"}


@router.get("/forward")
def forward():
    if _cache.get("t", 0) > time.time() - 600: return _cache["v"]
    v = forward_report(); _cache.update(t=time.time(), v=v); return v
