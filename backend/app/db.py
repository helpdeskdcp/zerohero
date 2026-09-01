"""
SQLite persistence layer.
Tables mirror the n8n Data Tables: ai_signals_log, ai_paper_trades.
"""
import sqlite3
import os
import threading
from contextlib import contextmanager

DB_PATH = os.environ.get("CHANAKYA_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "chanakya.db"))
DB_PATH = os.path.abspath(DB_PATH)

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_signals_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT UNIQUE,
    created_ts TEXT,
    market TEXT,
    symbol TEXT,
    instrument TEXT,
    underlying TEXT,
    expiry TEXT,
    strike REAL,
    option_type TEXT,
    direction TEXT,
    timeframe TEXT,
    entry_ref REAL,
    target_1 REAL,
    target_2 REAL,
    stop_loss REAL,
    trailing_stop REAL,
    probability REAL,
    confidence REAL,
    risk_reward REAL,
    market_regime TEXT,
    decision TEXT,
    data_status TEXT,
    risk_status TEXT,
    reason TEXT,
    model_version TEXT,
    live_trading INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ai_paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT UNIQUE,
    signal_id TEXT,
    opened_ts TEXT,
    closed_ts TEXT,
    status TEXT,               -- OPEN | CLOSED
    result TEXT,                -- WIN | LOSS | FLAT
    market TEXT,
    underlying TEXT,
    instrument TEXT,
    expiry TEXT,
    strike REAL,
    option_type TEXT,
    direction TEXT,
    timeframe TEXT,
    entry REAL,
    exit_price REAL,
    target_1 REAL,
    target_2 REAL,
    stop_loss REAL,
    trailing_stop REAL,
    quantity REAL,
    probability REAL,
    confidence REAL,
    market_regime TEXT,
    oi_evidence TEXT,
    pnl REAL,
    reason TEXT,
    strategy TEXT DEFAULT 'CORE',   -- CORE | SCALP
    setup TEXT,                      -- scalp setup name
    atr_pct REAL,
    max_hold_sec REAL,
    mfe REAL DEFAULT 0,             -- max favourable excursion (price)
    mae REAL DEFAULT 0,            -- max adverse excursion (price)
    exit_reason TEXT,              -- TARGET | STOP | TRAIL | TIME | MANUAL | BROKER_*
    symboltoken TEXT              -- Angel One token (for the live feed / dedupe)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Turning-Point Engine predictions, resolved against future OHLC for
-- deterministic closed-form calibration (no ML).
CREATE TABLE IF NOT EXISTS tp_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    symbol TEXT,
    timeframe TEXT,
    direction TEXT,              -- UP_TURN | DOWN_TURN | NO_TURN
    turn REAL,
    raw REAL,
    p_up REAL,
    confidence INTEGER,
    close_at_pred REAL,
    atr_at_pred REAL,
    horizon_bars INTEGER,
    next_hi_lo REAL, next_hi_hi REAL,
    next_lo_lo REAL, next_lo_hi REAL,
    expected_move_pts REAL,
    feature_scores TEXT,        -- JSON
    resolved INTEGER DEFAULT 0,
    resolved_ts TEXT,
    outcome TEXT,               -- DIRECTION_HIT | ZONE_HIT | BOTH | MISS | TIMEOUT
    fwd_close REAL,
    mfe_atr REAL, mae_atr REAL,
    signed_outcome REAL,        -- realised (fwd_close - close_at_pred)/atr, for weight corr
    err_pts REAL
);

-- Order Adapter: one row per broker order INTENT (entry / target / SL / exit).
-- Written PREARMED before any submit so a crash mid-submit is recoverable by
-- reconciliation instead of a blind re-send. client_tag is the idempotency key.
CREATE TABLE IF NOT EXISTS broker_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_tag TEXT UNIQUE,       -- trade_id + ':' + leg  (idempotency key)
    trade_id TEXT,
    leg TEXT,                     -- ENTRY | TARGET | SL | EXIT
    mode TEXT,                    -- PAPER | SHADOW | LIVE
    side TEXT,                    -- BUY | SELL
    order_type TEXT,             -- MARKET | LIMIT | SL | SL-M
    variety TEXT,
    product TEXT,
    symbol TEXT,
    symboltoken TEXT,
    exchange TEXT,
    tradingsymbol TEXT,
    requested_qty REAL,
    limit_price REAL,
    trigger_price REAL,
    status TEXT,                 -- PREARMED|ACCEPTED|OPEN|PARTIAL|COMPLETE|REJECTED|CANCELLED|UNKNOWN
    broker_order_id TEXT,
    unique_order_id TEXT,
    filled_qty REAL DEFAULT 0,
    avg_fill_price REAL,
    prearm_ts TEXT,
    submit_ts TEXT,
    fill_ts TEXT,
    last_reconcile_ts TEXT,
    exit_reason TEXT,
    error TEXT,
    signal_confidence REAL,
    signal_ts TEXT,
    market_data_ts TEXT,
    raw_json TEXT
);

-- Append-only audit trail: every state transition, reconcile, error, kill-switch
-- toggle. Never updated, only inserted.
CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    trade_id TEXT,
    client_tag TEXT,
    kind TEXT,
    detail TEXT                  -- JSON
);

-- Autonomous scalper: one row per decision (the spec-4 rich signal/outcome
-- record). Shared shape for HISTORICAL_REPLAY / BACKTEST / LIVE so a signal can
-- be reproduced and graded identically. Historical BATI data is NEVER written
-- here; `source` + `provenance` keep the origins distinguishable.
CREATE TABLE IF NOT EXISTS scalp_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT UNIQUE,           -- globally unique per decision
    source TEXT,                     -- LIVE | REPLAY | BACKTEST
    provenance TEXT,                 -- JSON: {db, cycle_id, run_id, ...}
    created_ts TEXT,                 -- decision timestamp (ISO)
    session_date TEXT,
    tod_bucket TEXT,                 -- OPEN / MORNING / MIDDAY / AFTERNOON / CLOSE
    symbol TEXT,
    -- market snapshot at decision time
    index_ltp REAL, vwap REAL, atr REAL, pcr REAL, max_pain REAL,
    regime TEXT, momentum REAL,
    -- support / resistance context
    support REAL, resistance REAL,
    support_strength REAL, resistance_strength REAL,
    sr_level REAL, sr_side TEXT,     -- SUPPORT | RESISTANCE
    -- signal
    signal_type TEXT,               -- SUPPORT_REVERSAL|SUPPORT_BREAKDOWN|RESISTANCE_REVERSAL|RESISTANCE_BREAKOUT|NONE
    direction TEXT,                 -- BULLISH | BEARISH | NONE
    mtf_alignment REAL,
    component_scores TEXT,          -- JSON {price_action,level_strength,volume,oi,momentum,vwap,atr,htf,retest}
    signal_score REAL,              -- 0-100
    probability REAL,               -- calibrated 0-1
    confidence TEXT,                -- LOW | MEDIUM | HIGH
    ev REAL, rr REAL,
    decision TEXT,                  -- BUY_CE|BUY_PE|NO_TRADE|WATCH|WAIT_FOR_CONFIRMATION
    reason TEXT,
    calib_version TEXT,
    -- locked option contract (never changes for the life of the trade)
    opt_underlying TEXT, opt_strike REAL, opt_expiry TEXT, opt_type TEXT,
    opt_token TEXT, opt_tradingsymbol TEXT,
    -- trade plan
    entry REAL, stop_loss REAL, target_1 REAL, target_2 REAL, trailing_stop REAL,
    max_hold_sec REAL, entry_ts TEXT,
    -- outcome (filled by the replay simulator / live monitor)
    status TEXT DEFAULT 'PENDING',  -- PENDING|OPEN|CLOSED|CANCELLED|EXPIRED|NO_FILL
    exit_price REAL, exit_ts TEXT, exit_reason TEXT,
    points REAL, r_multiple REAL, mfe REAL, mae REAL,
    outcome TEXT,                   -- WIN | LOSS | FLAT | NO_FILL
    holding_sec REAL,
    resolved INTEGER DEFAULT 0
);

-- Canonical ZeroHero LIVE market store. The autonomous scalper writes one row
-- per evaluated cycle here (never to the read-only oi_history.db). Normalised
-- to mirror oi_history.cycles + a compact chain blob -- the minimum needed to
-- replay/audit/recalibrate a live signal later (spec-17).
CREATE TABLE IF NOT EXISTS live_market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,                        -- ISO, decision time
    session_date TEXT,
    symbol TEXT,
    source TEXT DEFAULT 'LIVE',
    provenance TEXT,                -- JSON {feed, run_id, ...}
    index_ltp REAL, atm REAL, vwap REAL, atr REAL,
    pcr REAL, max_pain REAL,
    momentum REAL, state_score REAL,
    regime TEXT, mtf_alignment REAL,
    support REAL, resistance REAL, support_strength REAL, resistance_strength REAL,
    signal_type TEXT, direction TEXT, signal_score REAL, probability REAL,
    confidence TEXT, decision TEXT, reason TEXT,
    ev REAL, rr REAL,
    feed_age_sec REAL, chain_json TEXT
);

"""

# Indexes are created AFTER _migrate() runs, because some of them
# (ix_trades_status_strategy, ix_trades_symboltoken) reference columns that
# _migrate adds to a pre-existing ai_paper_trades. Creating them inside SCHEMA
# blows up on an old DB where the table exists without those columns.
_SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_trades_status_strategy ON ai_paper_trades(status, strategy);
CREATE INDEX IF NOT EXISTS ix_trades_symboltoken     ON ai_paper_trades(symboltoken);
CREATE INDEX IF NOT EXISTS ix_trades_opened_ts       ON ai_paper_trades(opened_ts);
CREATE INDEX IF NOT EXISTS ix_signals_created_ts     ON ai_signals_log(created_ts);
CREATE INDEX IF NOT EXISTS ix_broker_orders_trade    ON broker_orders(trade_id);
CREATE INDEX IF NOT EXISTS ix_broker_orders_status   ON broker_orders(status);
CREATE INDEX IF NOT EXISTS ix_order_events_trade     ON order_events(trade_id);
CREATE INDEX IF NOT EXISTS ix_scalp_signals_src      ON scalp_signals(source, session_date);
CREATE INDEX IF NOT EXISTS ix_scalp_signals_status   ON scalp_signals(status, resolved);
CREATE INDEX IF NOT EXISTS ix_scalp_signals_symbol   ON scalp_signals(symbol, created_ts);
CREATE INDEX IF NOT EXISTS ix_live_snap_symbol_ts     ON live_market_snapshots(symbol, ts);
CREATE INDEX IF NOT EXISTS ix_live_snap_date          ON live_market_snapshots(session_date, symbol);
"""


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


@contextmanager
def db():
    with _lock:
        conn = get_conn()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


# Columns added after the initial release — applied idempotently on every boot
# so an existing chanakya.db picks them up without a manual migration.
_MIGRATIONS = {
    "ai_paper_trades": {
        "strategy": "TEXT DEFAULT 'CORE'",
        "setup": "TEXT",
        "atr_pct": "REAL",
        "max_hold_sec": "REAL",
        "mfe": "REAL DEFAULT 0",
        "mae": "REAL DEFAULT 0",
        "exit_reason": "TEXT",
        "symboltoken": "TEXT",
        "risk_ref": "REAL",          # |entry - initial stop| captured at open (1R)
    },
    "live_market_snapshots": {
        "ev": "REAL",
        "rr": "REAL",
        "vwap_status": "TEXT",   # available | invalid_volume | insufficient_data
        "momentum": "REAL",      # state-classifier roc_pct at decision time
        "state_score": "REAL",   # state-classifier composite score (0-100)
    },
}


def _migrate(conn):
    for table, cols in _MIGRATIONS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db():
    with db() as conn:
        conn.executescript(SCHEMA)      # tables (CREATE TABLE IF NOT EXISTS)
        _migrate(conn)                  # add late columns to a pre-existing DB
        conn.executescript(_SCHEMA_INDEXES)   # indexes, now that columns exist


def insert_signal(row: dict):
    cols = ["signal_id", "created_ts", "market", "symbol", "instrument", "underlying",
            "expiry", "strike", "option_type", "direction", "timeframe", "entry_ref",
            "target_1", "target_2", "stop_loss", "trailing_stop", "probability",
            "confidence", "risk_reward", "market_regime", "decision", "data_status",
            "risk_status", "reason", "model_version", "live_trading"]
    vals = [row.get(c) for c in cols]
    placeholders = ",".join(["?"] * len(cols))
    with db() as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO ai_signals_log ({','.join(cols)}) VALUES ({placeholders})",
            vals,
        )


def list_signals(limit=200):
    with db() as conn:
        cur = conn.execute("SELECT * FROM ai_signals_log ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]


def insert_trade(row: dict):
    cols = ["trade_id", "signal_id", "opened_ts", "closed_ts", "status", "result",
            "market", "underlying", "instrument", "expiry", "strike", "option_type",
            "direction", "timeframe", "entry", "exit_price", "target_1", "target_2",
            "stop_loss", "trailing_stop", "quantity", "probability", "confidence",
            "market_regime", "oi_evidence", "pnl", "reason",
            "strategy", "setup", "atr_pct", "max_hold_sec", "mfe", "mae", "exit_reason",
            "symboltoken", "risk_ref"]
    vals = [row.get(c) for c in cols]
    placeholders = ",".join(["?"] * len(cols))
    with db() as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO ai_paper_trades ({','.join(cols)}) VALUES ({placeholders})",
            vals,
        )


def update_trade(trade_id: str, fields: dict):
    if not fields:
        return
    sets = ",".join([f"{k}=?" for k in fields.keys()])
    vals = list(fields.values()) + [trade_id]
    with db() as conn:
        conn.execute(f"UPDATE ai_paper_trades SET {sets} WHERE trade_id=?", vals)


def get_trade(trade_id: str):
    with db() as conn:
        cur = conn.execute("SELECT * FROM ai_paper_trades WHERE trade_id=?", (trade_id,))
        r = cur.fetchone()
        return dict(r) if r else None


def list_trades(status=None, limit=200, strategy=None):
    clauses, params = [], []
    if status:
        clauses.append("status=?")
        params.append(status)
    if strategy:
        clauses.append("COALESCE(strategy,'CORE')=?")
        params.append(strategy)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with db() as conn:
        cur = conn.execute(f"SELECT * FROM ai_paper_trades{where} ORDER BY id DESC LIMIT ?", params)
        return [dict(r) for r in cur.fetchall()]


def list_open_managed():
    """OPEN trades the scalp runner is responsible for marking to market."""
    with db() as conn:
        cur = conn.execute(
            "SELECT * FROM ai_paper_trades WHERE status='OPEN' "
            "AND COALESCE(strategy,'CORE') IN ('SCALP','MANUAL') ORDER BY id DESC")
        return [dict(r) for r in cur.fetchall()]


def count_trades_since(iso_ts, strategy=None):
    clauses = ["opened_ts >= ?"]
    params = [iso_ts]
    if strategy:
        clauses.append("COALESCE(strategy,'CORE')=?")
        params.append(strategy)
    with db() as conn:
        cur = conn.execute(
            f"SELECT COUNT(*) AS c FROM ai_paper_trades WHERE {' AND '.join(clauses)}", params)
        return int(cur.fetchone()["c"])


def get_setting(key, default=None):
    with db() as conn:
        cur = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,))
        r = cur.fetchone()
        return r["value"] if r else default


def set_setting(key, value):
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, str(value)))


# ---------------------------------------------------------------- singleton lease
# Cross-PROCESS mutual exclusion so only ONE background runner is ever active,
# no matter how many uvicorn workers are launched. `_lock` above only guards
# threads within one process; this uses SQLite's own write lock (BEGIN IMMEDIATE)
# which IS honoured across processes.
import time as _time  # noqa: E402
import json as _json  # noqa: E402


def lease_acquire(key: str, owner: str, ttl_sec: int = 30) -> bool:
    """Claim or renew the lease `key` for `owner`. Returns True if `owner` holds
    it after the call. A lease whose heartbeat is older than ttl_sec is stale and
    can be stolen (covers a crashed holder)."""
    now = _time.time()
    with _lock:
        conn = get_conn()
        conn.isolation_level = None          # explicit transaction control
        try:
            conn.execute("PRAGMA busy_timeout=3000")
            conn.execute("BEGIN IMMEDIATE")
            r = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
            cur_owner, hb = None, 0.0
            if r:
                try:
                    d = _json.loads(r["value"])
                    cur_owner, hb = d.get("owner"), float(d.get("hb") or 0)
                except Exception:
                    pass
            if (not cur_owner) or cur_owner == owner or (now - hb) > ttl_sec:
                conn.execute(
                    "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                    (key, _json.dumps({"owner": owner, "hb": now})))
                conn.commit()
                return True
            conn.commit()
            return False
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            conn.close()


def lease_release(key: str, owner: str):
    with _lock:
        conn = get_conn()
        try:
            r = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
            if r:
                try:
                    if _json.loads(r["value"]).get("owner") == owner:
                        conn.execute("DELETE FROM app_settings WHERE key=?", (key,))
                        conn.commit()
                except Exception:
                    pass
        finally:
            conn.close()


def lease_owner(key: str):
    raw = get_setting(key)
    if not raw:
        return None
    try:
        return _json.loads(raw).get("owner")
    except Exception:
        return None


def find_open_by_token(symboltoken: str, strategy: str = "MANUAL"):
    """Newest OPEN trade for a symboltoken+strategy, or None. Used to keep
    position auto-sync and manual /track from creating duplicate mirrors."""
    if not symboltoken:
        return None
    with db() as conn:
        cur = conn.execute(
            "SELECT * FROM ai_paper_trades WHERE status='OPEN' AND symboltoken=? "
            "AND COALESCE(strategy,'CORE')=? ORDER BY id DESC LIMIT 1",
            (str(symboltoken), strategy))
        r = cur.fetchone()
        return dict(r) if r else None


# ---------------------------------------------------------------- order adapter
_BROKER_ORDER_COLS = (
    "client_tag", "trade_id", "leg", "mode", "side", "order_type", "variety",
    "product", "symbol", "symboltoken", "exchange", "tradingsymbol",
    "requested_qty", "limit_price", "trigger_price", "status", "broker_order_id",
    "unique_order_id", "filled_qty", "avg_fill_price", "prearm_ts", "submit_ts",
    "fill_ts", "last_reconcile_ts", "exit_reason", "error", "signal_confidence",
    "signal_ts", "market_data_ts", "raw_json",
)


def insert_broker_order(row: dict):
    """INSERT OR IGNORE on client_tag — a second call with the same tag is a
    no-op, which is exactly the idempotency guarantee the OrderManager relies on."""
    vals = [row.get(c) for c in _BROKER_ORDER_COLS]
    ph = ",".join(["?"] * len(_BROKER_ORDER_COLS))
    with db() as conn:
        conn.execute(
            f"INSERT OR IGNORE INTO broker_orders ({','.join(_BROKER_ORDER_COLS)}) VALUES ({ph})",
            vals)


def update_broker_order(client_tag: str, fields: dict):
    if not fields:
        return
    sets = ",".join([f"{k}=?" for k in fields])
    with db() as conn:
        conn.execute(f"UPDATE broker_orders SET {sets} WHERE client_tag=?",
                     list(fields.values()) + [client_tag])


def get_broker_order(client_tag: str):
    with db() as conn:
        r = conn.execute("SELECT * FROM broker_orders WHERE client_tag=?", (client_tag,)).fetchone()
        return dict(r) if r else None


def list_broker_orders(trade_id: str | None = None, status=None, limit: int = 500):
    clauses, params = [], []
    if trade_id:
        clauses.append("trade_id=?")
        params.append(trade_id)
    if status:
        stats = [status] if isinstance(status, str) else list(status)
        clauses.append(f"status IN ({','.join(['?'] * len(stats))})")
        params += stats
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with db() as conn:
        cur = conn.execute(f"SELECT * FROM broker_orders{where} ORDER BY id DESC LIMIT ?", params)
        return [dict(r) for r in cur.fetchall()]


def insert_order_event(trade_id, client_tag, kind, detail_json):
    from datetime import datetime as _dt, timezone as _tz
    with db() as conn:
        conn.execute(
            "INSERT INTO order_events (ts, trade_id, client_tag, kind, detail) VALUES (?,?,?,?,?)",
            (_dt.now(_tz.utc).isoformat(), trade_id, client_tag, kind, detail_json))


def list_order_events(trade_id: str | None = None, limit: int = 500):
    if trade_id:
        q = "SELECT * FROM order_events WHERE trade_id=? ORDER BY id DESC LIMIT ?"
        params = (trade_id, limit)
    else:
        q = "SELECT * FROM order_events ORDER BY id DESC LIMIT ?"
        params = (limit,)
    with db() as conn:
        return [dict(r) for r in conn.execute(q, params).fetchall()]


# ---------------------------------------------------------------- autonomous scalper (spec-4)
_SCALP_SIGNAL_COLS = (
    "signal_id", "source", "provenance", "created_ts", "session_date", "tod_bucket",
    "symbol", "index_ltp", "vwap", "atr", "pcr", "max_pain", "regime", "momentum",
    "support", "resistance", "support_strength", "resistance_strength", "sr_level",
    "sr_side", "signal_type", "direction", "mtf_alignment", "component_scores",
    "signal_score", "probability", "confidence", "ev", "rr", "decision", "reason",
    "calib_version", "opt_underlying", "opt_strike", "opt_expiry", "opt_type",
    "opt_token", "opt_tradingsymbol", "entry", "stop_loss", "target_1", "target_2",
    "trailing_stop", "max_hold_sec", "entry_ts", "status", "exit_price", "exit_ts",
    "exit_reason", "points", "r_multiple", "mfe", "mae", "outcome", "holding_sec",
    "resolved",
)


def insert_scalp_signal(row: dict):
    """INSERT OR IGNORE on signal_id — a signal row is written once at decision
    time; the outcome is filled later with update_scalp_signal()."""
    vals = [row.get(c) for c in _SCALP_SIGNAL_COLS]
    ph = ",".join(["?"] * len(_SCALP_SIGNAL_COLS))
    with db() as conn:
        conn.execute(
            f"INSERT OR IGNORE INTO scalp_signals ({','.join(_SCALP_SIGNAL_COLS)}) VALUES ({ph})",
            vals)


def update_scalp_signal(signal_id: str, fields: dict):
    fields = {k: v for k, v in (fields or {}).items() if k in _SCALP_SIGNAL_COLS}
    if not fields:
        return
    sets = ",".join(f"{k}=?" for k in fields)
    with db() as conn:
        conn.execute(f"UPDATE scalp_signals SET {sets} WHERE signal_id=?",
                     list(fields.values()) + [signal_id])


def get_scalp_signal(signal_id: str):
    with db() as conn:
        r = conn.execute("SELECT * FROM scalp_signals WHERE signal_id=?", (signal_id,)).fetchone()
        return dict(r) if r else None


def list_scalp_signals(source=None, status=None, symbol=None, resolved=None,
                       session_date=None, limit: int = 1000):
    clauses, params = [], []
    for col, val in (("source", source), ("status", status), ("symbol", symbol),
                     ("resolved", resolved), ("session_date", session_date)):
        if val is not None:
            clauses.append(f"{col}=?")
            params.append(val)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with db() as conn:
        cur = conn.execute(f"SELECT * FROM scalp_signals{where} ORDER BY id DESC LIMIT ?", params)
        return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- canonical LIVE market store
_LIVE_SNAP_COLS = (
    "ts", "session_date", "symbol", "source", "provenance", "index_ltp", "atm",
    "vwap", "vwap_status", "atr", "momentum", "state_score",
    "pcr", "max_pain", "regime", "mtf_alignment", "support",
    "resistance", "support_strength", "resistance_strength", "signal_type",
    "direction", "signal_score", "probability", "confidence", "decision",
    "reason", "ev", "rr", "feed_age_sec", "chain_json",
)


def insert_live_snapshot(row: dict) -> int:
    vals = [row.get(c) for c in _LIVE_SNAP_COLS]
    ph = ",".join(["?"] * len(_LIVE_SNAP_COLS))
    with db() as conn:
        cur = conn.execute(
            f"INSERT INTO live_market_snapshots ({','.join(_LIVE_SNAP_COLS)}) VALUES ({ph})", vals)
        return int(cur.lastrowid)


def update_live_snapshot(snap_id: int, fields: dict):
    if not fields or not snap_id:
        return
    sets = ",".join(f"{k}=?" for k in fields)
    with db() as conn:
        conn.execute(f"UPDATE live_market_snapshots SET {sets} WHERE id=?",
                     list(fields.values()) + [snap_id])


def list_live_snapshots(symbol=None, session_date=None, decision=None, limit: int = 500):
    clauses, params = [], []
    for col, val in (("symbol", symbol), ("session_date", session_date), ("decision", decision)):
        if val is not None:
            clauses.append(f"{col}=?")
            params.append(val)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with db() as conn:
        cur = conn.execute(
            f"SELECT * FROM live_market_snapshots{where} ORDER BY id DESC LIMIT ?", params)
        return [dict(r) for r in cur.fetchall()]
