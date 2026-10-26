"""Orchestration: one evaluation pass over an adapter. Labels every result with the adapter's data_kind (LIVE / HISTORICAL / MOCK) and never scores without it."""
from __future__ import annotations

import time

from . import adapters as A, constituents as C, derivatives as D, engine as E, scoring as S, strategy as ST, validation as V, store, indicators as I
from .paper import assert_paper_only


def evaluate(adapter, master: dict, now: float, *, scfg=None, cfg=None, max_age: float | None = None, underlying: str = "NIFTY", persist: bool = False, db_path=None) -> dict:
    assert_paper_only()
    scfg = scfg or ST.StrategyConfig(); cfg = cfg or S.ScoreConfig(); kind = getattr(adapter, "data_kind", "UNKNOWN"); caps = A.capabilities(adapter)
    syms = [m["symbol"] for m in master["members"]]
    quotes = adapter.quotes(syms) if A.CAP_QUOTES in caps else {}
    val = V.validate_universe(master, quotes, now, max_age)
    snap = E.snapshot(master, val)
    spot_q = adapter.quotes([underlying]).get(underlying) if A.CAP_QUOTES in caps else None
    spot = spot_q.ltp if spot_q else None
    c15 = adapter.candles(underlying, "15m", 80) if A.CAP_CANDLES in caps else []; c5 = adapter.candles(underlying, "5m", 80) if A.CAP_CANDLES in caps else []
    trend = ST.trend15(c15, now, scfg) if c15 else {"state": "INVALID", "reason": "NO_15M_DATA"}
    entry = ST.entry5(c5, trend, now, scfg) if c5 else {"signal": None, "reason": "NO_5M_DATA"}
    fut = adapter.futures() if A.CAP_FUTURES in caps else None; ch = adapter.option_chain() if A.CAP_CHAIN in caps else None
    deriv = D.confirm(fut, ch, now, spot, getattr(adapter, "deriv_max_age", None))
    sc = S.score(cfg, snapshot=snap, trend=trend, entry=entry, deriv=deriv)
    health = {"data_kind": kind, "capabilities": sorted(caps), "quotes_valid": len(val["valid"]), "quotes_invalid": val["invalid"], "weight_coverage": val["weight_coverage"], "weights_status": master.get("weights_status"),
              "weights_notes": master.get("weights_notes"), "constituents": master.get("count"), "csv_sha256": master.get("csv_sha256"), "master_problems": C.validate(master),
              "futures": deriv["futures"].get("reason") if not deriv["futures"].get("available") else "OK", "chain": deriv["chain"].get("reason") if not deriv["chain"].get("available") else "OK", "deriv_limits_sec": getattr(adapter, "deriv_max_age", "live default")}
    out = {"ts": now, "data_kind": kind, "snapshot": snap, "trend15": trend, "entry5": entry, "derivatives": deriv, "score": sc, "health": health, "spot": spot,
           "heatmap": [{"symbol": m["symbol"], "sector": m["sector"], "weight_pct": m.get("weight_pct"), "chg_pct": (quotes[m["symbol"]].ltp / quotes[m["symbol"]].prev_close - 1) * 100 if m["symbol"] in val["valid"] else None,
                        "status": val["invalid"].get(m["symbol"], "OK")} for m in master["members"]]}
    if persist:
        sid = store.add_signal(sc, entry, {"health": health, "trend15": trend, "entry5": entry, "deriv": deriv, "snapshot_status": snap.get("status")}, kind, ts=now, path=db_path)
        out["signal_id"] = sid
        if val["invalid"] or master.get("weights_status") != "COMPLETE": store.health("DATA_HEALTH", {"invalid": val["invalid"], "weights": master.get("weights_status")}, path=db_path, ts=now)
    return out
