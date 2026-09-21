"""
Phase 3 -- versioned research-event schema + isolated store.

Own database file (data/research_events.db), never chanakya.db or
market_history.db. Every record must carry provenance (source +
capture_timestamp, never NULL) so a reader can always tell where a value
came from and when it was captured -- this is a research dataset, not a
live trading table, and its credibility depends on that traceability.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field, fields

SCHEMA_VERSION = 1

_DEFAULT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "research_events.db"))

# Overridable by tests (monkeypatch this module attribute), exactly like
# app.orderflow.depth's L2_DB_PATH pattern.
RESEARCH_DB_PATH = _DEFAULT_PATH


@dataclass
class ResearchEvent:
    # provenance -- required, never fabricated
    source: str                         # e.g. "SCREENSHOT:logic_trade" | "MARKET_CAPTURE"
    capture_timestamp: str              # ISO8601 UTC, when THIS row was written

    # identity / timing
    timestamp: str | None = None        # event timestamp (signal or market sample), ISO8601 UTC
    underlying: str | None = None
    spot_price: float | None = None
    option_symbol: str | None = None
    expiry: str | None = None
    strike: float | None = None
    ce_pe: str | None = None            # CE | PE | None

    # option market state
    option_ltp: float | None = None
    bid: float | None = None
    ask: float | None = None
    spread: float | None = None
    volume: float | None = None
    oi: float | None = None
    oi_change: float | None = None
    iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None

    # price structure
    underlying_ohlc: dict | None = None   # {"o":,"h":,"l":,"c":}
    option_ohlc: dict | None = None
    vwap: float | None = None
    ema: float | None = None
    sma: float | None = None
    atr: float | None = None
    rsi: float | None = None
    adx: float | None = None
    support: float | None = None
    resistance: float | None = None
    candle_structure: str | None = None

    # orderflow (honest -- most of these are DATA_UNAVAILABLE, see orderflow_features.py)
    volume_delta: float | None = None
    bid_volume: float | None = None
    ask_volume: float | None = None
    orderflow_imbalance: float | None = None
    depth_imbalance: float | None = None
    absorption: str | None = None
    sweep_aggression: str | None = None

    # classification
    regime: str | None = None
    signal_state: str | None = None
    public_signal_state: str | None = None

    # trade framing / outcome (label-only -- see ml_prep.py leakage rule)
    entry: float | None = None
    target: float | None = None
    stop_invalidation: float | None = None
    mae: float | None = None
    mfe: float | None = None
    exit: float | None = None
    outcome: str | None = None          # TARGET_ACHIEVED | SL_HIT | PARTIAL_BOOKED | ... | None

    # quality
    data_quality: str | None = None     # VERIFIED_MARKET_DATA | OBSERVED_PUBLIC_SIGNAL | INSUFFICIENT_SAMPLE
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


_COLUMNS: dict[str, str] = {
    "source": "TEXT NOT NULL", "capture_timestamp": "TEXT NOT NULL",
    "timestamp": "TEXT", "underlying": "TEXT", "spot_price": "REAL",
    "option_symbol": "TEXT", "expiry": "TEXT", "strike": "REAL", "ce_pe": "TEXT",
    "option_ltp": "REAL", "bid": "REAL", "ask": "REAL", "spread": "REAL",
    "volume": "REAL", "oi": "REAL", "oi_change": "REAL", "iv": "REAL",
    "delta": "REAL", "gamma": "REAL", "theta": "REAL", "vega": "REAL",
    "underlying_ohlc": "TEXT", "option_ohlc": "TEXT", "vwap": "REAL",
    "ema": "REAL", "sma": "REAL", "atr": "REAL", "rsi": "REAL", "adx": "REAL",
    "support": "REAL", "resistance": "REAL", "candle_structure": "TEXT",
    "volume_delta": "REAL", "bid_volume": "REAL", "ask_volume": "REAL",
    "orderflow_imbalance": "REAL", "depth_imbalance": "REAL",
    "absorption": "TEXT", "sweep_aggression": "TEXT",
    "regime": "TEXT", "signal_state": "TEXT", "public_signal_state": "TEXT",
    "entry": "REAL", "target": "REAL", "stop_invalidation": "REAL",
    "mae": "REAL", "mfe": "REAL", "exit": "REAL", "outcome": "TEXT",
    "data_quality": "TEXT", "schema_version": "INTEGER",
}

_JSON_COLS = {"underlying_ohlc", "option_ohlc"}


def _create_sql() -> str:
    cols = ",\n    ".join(f"{name} {decl}" for name, decl in _COLUMNS.items())
    return f"""
    CREATE TABLE IF NOT EXISTS research_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        {cols}
    );
    CREATE INDEX IF NOT EXISTS ix_research_events_underlying
        ON research_events(underlying, timestamp);
    """


def migrate(db_path: str | None = None) -> None:
    """Idempotent: CREATE TABLE IF NOT EXISTS, then add any missing columns
    -- same existence-check-before-ALTER pattern as app.db._migrate, so a
    pre-existing research_events.db from an older schema_version is upgraded
    in place rather than requiring a manual migration."""
    path = db_path or RESEARCH_DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_create_sql())
        have = {r[1] for r in conn.execute("PRAGMA table_info(research_events)")}
        for name, decl in _COLUMNS.items():
            if name not in have:
                conn.execute(f"ALTER TABLE research_events ADD COLUMN {name} {decl}")
        conn.commit()
    finally:
        conn.close()


@contextmanager
def _conn(db_path: str | None = None):
    path = db_path or RESEARCH_DB_PATH
    migrate(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def insert_event(event: ResearchEvent, *, db_path: str | None = None) -> int:
    """Provenance is mandatory: refuses to insert a row with no source or
    no capture_timestamp rather than silently writing an untraceable row."""
    if not event.source or not event.capture_timestamp:
        raise ValueError("ResearchEvent requires both source and capture_timestamp")
    data = event.to_dict()
    for c in _JSON_COLS:
        if data[c] is not None:
            import json
            data[c] = json.dumps(data[c])
    cols = list(_COLUMNS.keys())
    vals = [data[c] for c in cols]
    placeholders = ",".join(["?"] * len(cols))
    with _conn(db_path) as conn:
        cur = conn.execute(
            f"INSERT INTO research_events ({','.join(cols)}) VALUES ({placeholders})", vals)
        return cur.lastrowid


def query_events(*, underlying: str | None = None, db_path: str | None = None) -> list[dict]:
    with _conn(db_path) as conn:
        if underlying:
            rows = conn.execute(
                "SELECT * FROM research_events WHERE underlying=? ORDER BY id", (underlying,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM research_events ORDER BY id").fetchall()
    import json
    out = []
    for r in rows:
        d = dict(r)
        for c in _JSON_COLS:
            if d.get(c):
                try:
                    d[c] = json.loads(d[c])
                except (json.JSONDecodeError, TypeError):
                    pass
        out.append(d)
    return out


def count_events(*, db_path: str | None = None) -> int:
    with _conn(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM research_events").fetchone()
        return int(row["n"])
