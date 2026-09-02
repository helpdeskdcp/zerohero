"""
Historical market-data capture — a standalone, append-only recorder for
quantitative backtesting.

Design (production defaults, auto-approved 2026-09-02):
- **Separate DB file** `data/market_history.db` (`CHANAKYA_HIST_DB_PATH`), WAL. Never
  writes the trading DB.
- **REST poller only.** `market/v1/quote` FULL (incl. 5-level depth) +
  `historical/v1/getCandleData` + `marketData/v1/optionGreek`. No second WebSocket;
  the live trading feed is never touched, subscribed, or imported here.
- **Real AngelOne data only.** A field the broker did not send is stored `NULL` /
  `"UNAVAILABLE"` — never estimated or back-filled. Greeks come *only* from the
  official optionGreek endpoint.
- **Append-only + idempotent.** `INSERT OR IGNORE` on UNIQUE natural keys; a closed
  candle is written once and never rewritten; an open bar is never written.
- **Raw + normalized kept separately.** Every normalized row has a `raw_id` FK to the
  gzipped verbatim payload it was parsed from.
- **Three timestamps, never conflated:** `exch_ts` (exchange), `server_ts` (broker
  server), `received_ts` (our wall clock, always set). All stored as UTC ISO-8601;
  `session_date_ist` carried for day queries.
- **Zero look-ahead:** read helpers filter on `exch_ts` (fallback `received_ts` only
  when `exch_ts IS NULL`, flagged), never on `id`.
- **Integrity checked, never mutated:** impossible OHLC (`h < l`) is rejected + logged;
  soft issues (crossed book, gap, negative OI) are stored with a `flags` marker.

Nothing in this package imports or calls trading/signal logic.
"""
from .store import HistStore, hist_store
from .worker import CaptureWorker

__all__ = ["HistStore", "hist_store", "CaptureWorker"]
