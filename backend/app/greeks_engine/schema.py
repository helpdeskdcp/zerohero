"""Append-only tables for derived Greek exposure. Idempotent.

These live in the same `market_history.db` as the histcap capture (one file,
separate tables) so raw broker Greeks (`option_greeks`) and derived metrics
(`greek_exposure`) never share a row.
"""
from __future__ import annotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS greek_exposure (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    computed_ts     TEXT NOT NULL,             -- our wall clock, UTC ISO
    as_of_ts        TEXT NOT NULL,             -- broker Greek snapshot ts the metrics are for
    underlying      TEXT NOT NULL,
    expiry          TEXT NOT NULL,
    session_date_ist TEXT NOT NULL,
    underlying_price REAL,                     -- NULL if unavailable (never estimated)
    underlying_price_src TEXT,                 -- "ANGELONE_QUOTE:FUTURE" | ":INDEX" | NULL
    n_pairs_used    INTEGER,
    n_pairs_expected INTEGER,
    n_pairs_missing INTEGER,
    coverage_pct    REAL,
    stale_sec       REAL,
    quality         TEXT NOT NULL,             -- VALID | STALE | PARTIAL | INVALID | NO_DATA
    ce_oi_total     REAL, pe_oi_total REAL, pcr_oi REAL,
    ce_delta_exp REAL, pe_delta_exp REAL, net_delta_exp REAL, diff_delta_exp REAL,
    ce_gamma_exp REAL, pe_gamma_exp REAL, net_gamma_exp REAL, diff_gamma_exp REAL,
    ce_theta_exp REAL, pe_theta_exp REAL, net_theta_exp REAL, diff_theta_exp REAL,
    ce_vega_exp  REAL, pe_vega_exp  REAL, net_vega_exp  REAL, diff_vega_exp  REAL,
    oi_weighted_iv   REAL, vega_weighted_iv REAL,
    gamma_conc_strike REAL, gamma_conc_pct REAL, gamma_herfindahl REAL,
    per_strike_json  TEXT,                     -- [{strike, ce:{...exp,oi,iv}, pe:{...}}]
    source          TEXT NOT NULL,
    run_id          INTEGER,
    UNIQUE(underlying, expiry, as_of_ts)       -- append-only; a snapshot is written once
);
CREATE INDEX IF NOT EXISTS ix_gexp_lookup ON greek_exposure(underlying, expiry, as_of_ts);
CREATE INDEX IF NOT EXISTS ix_gexp_day    ON greek_exposure(session_date_ist, underlying);

CREATE TABLE IF NOT EXISTS greek_engine_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    started_ts       TEXT NOT NULL,
    ended_ts         TEXT,
    mode             TEXT NOT NULL,            -- "CYCLE" | "ONCE" | "BACKFILL"
    underlying       TEXT,
    expiries_json    TEXT,
    snapshots_written INTEGER DEFAULT 0,
    quality_json     TEXT,
    errors_json      TEXT,
    notes            TEXT
);
CREATE INDEX IF NOT EXISTS ix_gruns_started ON greek_engine_runs(started_ts);
"""


def init(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
