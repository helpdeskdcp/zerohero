"""
Module 16 -- historical similarity / setup memory.

k-NN over the immutable per-trade entry features (`trade_entry_features` + the
paired outcome in `trade_outcomes` / `ai_paper_trades`), on a small fixed
feature set. Returns the empirical win-rate of the k most-similar *resolved*
past setups, with an explicit n / INSUFFICIENT guard. No look-ahead: only trades
opened strictly before the query time are eligible.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3

_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                   "data", "chanakya.db")
_K = 15
_MIN_N = 12


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _rows(before_ts):
    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        q = con.execute(
            "SELECT signal_score, momentum, regime, signal_type, tod_bucket, "
            "       outcome AS result, created_ts, component_scores "
            "FROM scalp_signals "
            "WHERE resolved=1 AND outcome IN ('WIN','LOSS') "
            + ("AND created_ts < ? " if before_ts else "")
            + "ORDER BY created_ts DESC LIMIT 800",
            ((before_ts,) if before_ts else ()))
        rows = []
        for r in q.fetchall():
            d = dict(r)
            try:
                d["comp"] = json.loads(d.get("component_scores") or "{}")
            except (ValueError, TypeError):
                d["comp"] = {}
            d["state_score"] = d.get("signal_score")   # scalp_signals has no separate state_score
            rows.append(d)
        return rows
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _dist(a, b):
    """scaled euclidean on (state_score/100, signal_score/100, tanh(momentum), regime==, type==, tod==)."""
    d = 0.0
    for k, scale in (("state_score", 100.0), ("signal_score", 100.0)):
        av, bv = _f(a.get(k)), _f(b.get(k))
        if av is not None and bv is not None:
            d += ((av - bv) / scale) ** 2
    am, bm = _f(a.get("momentum")), _f(b.get("momentum"))
    if am is not None and bm is not None:
        d += (math.tanh(am) - math.tanh(bm)) ** 2
    for k in ("regime", "signal_type", "tod_bucket"):
        if a.get(k) and b.get(k):
            d += 0.0 if a[k] == b[k] else 0.5
    return math.sqrt(d)


def similarity_score(snap: dict, *, before_ts: str | None = None) -> dict:
    """snap = a live_market_snapshots row. Returns the k-NN empirical win-rate."""
    query = {
        "state_score": snap.get("state_score"), "signal_score": snap.get("signal_score"),
        "momentum": snap.get("momentum"), "regime": snap.get("regime"),
        "signal_type": snap.get("signal_type"), "tod_bucket": snap.get("tod_bucket"),
    }
    rows = _rows(before_ts or snap.get("ts"))
    if len(rows) < _MIN_N:
        return {"status": "INSUFFICIENT", "n_pool": len(rows),
                "note": f"< {_MIN_N} resolved similar setups in history (one-week outcome log)"}
    ranked = sorted(rows, key=lambda r: _dist(query, r))[:_K]
    wins = sum(1 for r in ranked if r["result"] == "WIN")
    n = len(ranked)
    wr = wins / n
    # shrink toward the pool base rate when n is small
    base = sum(1 for r in rows if r["result"] == "WIN") / len(rows)
    shrunk = (wins + 4 * base) / (n + 4)
    return {
        "status": "OK" if n >= _MIN_N else "THIN",
        "k": n, "n_pool": len(rows),
        "knn_win_rate": round(wr, 3), "shrunk_win_rate": round(shrunk, 3),
        "pool_base_rate": round(base, 3),
        "note": "empirical win-rate of the k nearest past resolved setups (state/signal score, "
                "momentum, regime, type, tod). One-week pool -> treat as weak prior.",
    }
