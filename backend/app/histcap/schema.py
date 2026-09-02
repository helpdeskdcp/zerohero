"""Append-only schema for the historical market-data store. Idempotent."""
from __future__ import annotations

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

-- verbatim broker payloads, gzip-compressed, deduped by content hash
CREATE TABLE IF NOT EXISTS raw_responses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    received_ts   TEXT NOT NULL,               -- UTC ISO, our wall clock
    server_ts     TEXT,                        -- broker server_timestamp / HTTP Date, else NULL
    endpoint      TEXT NOT NULL,               -- e.g. "market/v1/quote" | "marketData/v1/optionGreek" | "historical/v1/getCandleData"
    request_json  TEXT,                        -- the request body we sent
    http_status   INTEGER,
    status        TEXT,                        -- broker-level status token (OK|NO_DATA|AB9019|AUTH_FAILED|...)
    sha256        TEXT NOT NULL UNIQUE,        -- dedup: identical payloads collapse to one row
    gzip_b64      TEXT NOT NULL,               -- base64(gzip(response_json))
    run_id        INTEGER
);

-- one row per (instrument, timeframe, closed bar). Re-fetching a day is a no-op.
CREATE TABLE IF NOT EXISTS market_candles (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    received_ts    TEXT NOT NULL,
    instrument_key TEXT NOT NULL,              -- "<EXCH>:<token>" e.g. "NSE:99926000", "NFO:68407", "MCX:454185"
    symbol         TEXT NOT NULL,              -- friendly, e.g. "NIFTY"
    kind           TEXT NOT NULL,              -- INDEX | FUTURE | OPTION
    exchange       TEXT NOT NULL,
    token          TEXT NOT NULL,
    expiry         TEXT,                       -- DDMMMYYYY (future/option), else NULL
    strike         REAL,                       -- option only
    option_type    TEXT,                       -- CE | PE | NULL
    tf             TEXT NOT NULL,              -- "1m" | "5m" | "15m" | ...
    bar_start      TEXT NOT NULL,              -- UTC ISO, exchange bar-open time
    session_date_ist TEXT NOT NULL,            -- IST calendar date of the bar
    o REAL, h REAL, l REAL, c REAL,
    v REAL,                                    -- NULL when the broker sent no volume (e.g. a cash index)
    oi REAL, oi_change REAL,                   -- usually NULL for candle data (broker does not return it)
    source        TEXT NOT NULL,               -- "ANGELONE_CANDLES"
    raw_id        INTEGER REFERENCES raw_responses(id),
    flags         TEXT,                        -- comma list of soft integrity flags, else NULL
    run_id        INTEGER,
    UNIQUE(instrument_key, tf, bar_start)
);
CREATE INDEX IF NOT EXISTS ix_candles_lookup ON market_candles(symbol, kind, tf, bar_start);
CREATE INDEX IF NOT EXISTS ix_candles_ik     ON market_candles(instrument_key, tf, bar_start);
CREATE INDEX IF NOT EXISTS ix_candles_day    ON market_candles(session_date_ist, symbol);

-- point-in-time FULL quote per token (spot / future / option leg)
CREATE TABLE IF NOT EXISTS quote_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    received_ts    TEXT NOT NULL,
    server_ts      TEXT,
    exch_ts        TEXT,                       -- exchangeTimestamp, else NULL
    snap_key       TEXT NOT NULL,              -- exch_ts or received_ts truncated to the second -> dedup key
    instrument_key TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    kind           TEXT NOT NULL,              -- INDEX | FUTURE | OPTION
    exchange       TEXT NOT NULL,
    token          TEXT NOT NULL,
    expiry         TEXT,
    strike         REAL,
    option_type    TEXT,
    session_date_ist TEXT NOT NULL,
    ltp REAL, open REAL, high REAL, low REAL, close REAL,
    volume REAL, oi REAL, oi_change REAL,
    avg_price REAL, last_trade_qty REAL,
    bid REAL, ask REAL, bid_qty REAL, ask_qty REAL,
    tot_buy_qty REAL, tot_sell_qty REAL,
    depth_json     TEXT,                       -- {"buy":[{price,quantity,orders} x5],"sell":[...]}
    net_change REAL, pct_change REAL,
    lower_circuit REAL, upper_circuit REAL,
    week52_high REAL, week52_low REAL,
    basis REAL,                                -- FUTURE only: future_ltp - spot_ltp (source DERIVED); NULL otherwise
    quote_status   TEXT,                       -- OK | DATA_UNAVAILABLE | AUTH_FAILED | ...
    source         TEXT NOT NULL,              -- "ANGELONE_QUOTE_FULL"
    raw_id         INTEGER REFERENCES raw_responses(id),
    flags          TEXT,
    run_id         INTEGER,
    UNIQUE(token, snap_key)
);
CREATE INDEX IF NOT EXISTS ix_quotes_lookup ON quote_snapshots(symbol, kind, exch_ts);
CREATE INDEX IF NOT EXISTS ix_quotes_opt    ON quote_snapshots(symbol, expiry, strike, option_type, exch_ts);
CREATE INDEX IF NOT EXISTS ix_quotes_day    ON quote_snapshots(session_date_ist, symbol);

-- one optionGreek pull expanded per (strike, type). Greeks ONLY from optionGreek.
CREATE TABLE IF NOT EXISTS option_greeks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    received_ts   TEXT NOT NULL,
    server_ts     TEXT,
    snap_key      TEXT NOT NULL,               -- server_ts or received_ts truncated to the second
    underlying    TEXT NOT NULL,
    expiry        TEXT NOT NULL,
    strike        REAL NOT NULL,
    option_type   TEXT NOT NULL,               -- CE | PE
    session_date_ist TEXT NOT NULL,
    delta REAL, gamma REAL, theta REAL, vega REAL,
    iv REAL,                                   -- decimal fraction (broker % / 100)
    iv_pct REAL,                               -- raw broker percentage, unmodified
    trade_volume REAL,                         -- from the greek endpoint (informational)
    broker_status TEXT NOT NULL,               -- OK | NO_DATA | AB9019 | AUTH_FAILED | ...
    source        TEXT NOT NULL,               -- "ANGELONE_OPTION_GREEK"
    raw_id        INTEGER REFERENCES raw_responses(id),
    flags         TEXT,
    run_id        INTEGER,
    UNIQUE(underlying, expiry, strike, option_type, snap_key)
);
CREATE INDEX IF NOT EXISTS ix_greeks_lookup ON option_greeks(underlying, expiry, strike, option_type, received_ts);
CREATE INDEX IF NOT EXISTS ix_greeks_day    ON option_greeks(session_date_ist, underlying);

-- one row per capture cycle: provenance, counts, integrity summary, errors
CREATE TABLE IF NOT EXISTS capture_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_ts     TEXT NOT NULL,
    ended_ts       TEXT,
    mode           TEXT NOT NULL,              -- "POLL" | "POLL_ONCE" | "HEARTBEAT"
    market_state   TEXT,                       -- JSON {NSE:..,MCX:..}
    auth_ok        INTEGER,                    -- 1/0
    instruments_n  INTEGER,
    candles_written INTEGER DEFAULT 0,
    quotes_written  INTEGER DEFAULT 0,
    greeks_written  INTEGER DEFAULT 0,
    raw_written     INTEGER DEFAULT 0,
    integrity_json TEXT,                       -- {issues:[...], rejected:[...], flagged:N}
    errors_json    TEXT,                       -- [{endpoint, symbol, status, message}]
    notes          TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_started ON capture_runs(started_ts);
"""


def init(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
