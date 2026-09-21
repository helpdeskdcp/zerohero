"""
Phase 12 -- data collection status report. Read-only over the new
research_events.db plus the existing capture DBs (market_history.db,
l2_capture.db via app.orderflow.depth). Every number here comes from a
real query at call time -- nothing is a cached/remembered figure from an
earlier session.
"""
from __future__ import annotations

import sqlite3

from . import ml_prep, schema
from .capture import MARKET_DB_PATH
from ..orderflow import depth as _depth


def _connect_ro(path: str) -> sqlite3.Connection | None:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
        return None


def _data_coverage(db_path: str) -> dict:
    conn = _connect_ro(db_path)
    if conn is None:
        return {"available": False, "reason": f"cannot open {db_path}"}
    try:
        rows = conn.execute(
            "SELECT symbol, kind, MIN(session_date_ist) AS first_date, "
            "MAX(session_date_ist) AS last_date, COUNT(*) AS n "
            "FROM market_candles GROUP BY symbol, kind").fetchall()
        return {"available": True, "by_symbol_kind": [dict(r) for r in rows]}
    except sqlite3.OperationalError as e:
        return {"available": False, "reason": str(e)}
    finally:
        conn.close()


def _option_chain_density(db_path: str) -> dict:
    """Same query shape used for PHASE0_PHASE1_REPORT.md's sparsity
    finding -- snapshots per strike per session, real numbers only."""
    conn = _connect_ro(db_path)
    if conn is None:
        return {"available": False, "reason": f"cannot open {db_path}"}
    try:
        rows = conn.execute(
            "SELECT symbol, session_date_ist, COUNT(*) AS n_snapshots, "
            "COUNT(DISTINCT strike) AS n_strikes "
            "FROM quote_snapshots WHERE kind='OPTION' "
            "GROUP BY symbol, session_date_ist ORDER BY symbol, session_date_ist").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["snapshots_per_strike"] = (round(d["n_snapshots"] / d["n_strikes"], 2)
                                         if d["n_strikes"] else None)
            out.append(d)
        return {"available": True, "per_session": out}
    except sqlite3.OperationalError as e:
        return {"available": False, "reason": str(e)}
    finally:
        conn.close()


def build_status_report(*, research_db_path: str | None = None,
                        market_db_path: str | None = None) -> dict:
    events = schema.query_events(db_path=research_db_path)
    verified = [e for e in events if e.get("data_quality") == "VERIFIED_MARKET_DATA"]
    insufficient = [e for e in events if e.get("data_quality") == "INSUFFICIENT_SAMPLE"]

    missing_fields: dict[str, int] = {}
    for e in events:
        for k, v in e.items():
            if v is None:
                missing_fields[k] = missing_fields.get(k, 0) + 1

    m_path = market_db_path or MARKET_DB_PATH

    return {
        "events_captured": len(events),
        "events_verified": len(verified),
        "events_insufficient": len(insufficient),
        "missing_fields_summary": missing_fields,
        "note_on_events_captured": (
            "research_events.db is queried live here; the 5 known signals from "
            "PHASE0_PHASE1_REPORT.md are recorded there as a markdown table only "
            "and have not been loaded into this store via schema.insert_event -- "
            "this count reflects the store's real current contents, not that report."
        ),
        "data_coverage": _data_coverage(m_path),
        "option_chain_density": _option_chain_density(m_path),
        "orderflow_availability": {
            "NIFTY": _depth.snapshot_for_symbol("NIFTY").get("available"),
            "note": _depth.snapshot_for_symbol("NIFTY").get("note"),
        },
        "model_readiness": ml_prep.check_training_readiness(events),
        "confirmation_engine_readiness": {
            "status": "BUILT_SHADOW_ONLY",
            "note": "app.reverse_engineering.confirmation is implemented and unit-tested; "
                   "not wired into any live/paper decision path.",
        },
    }
