# Historical Market-Data Capture — Implementation

**Date:** 2026-09-02 · Follows `HISTORICAL_CAPTURE_AUDIT.md`. Standalone, append-only
market-data recorder for local backtesting. **No trading/signal logic changed. No live
orders. No second WebSocket. Own DB file.**

---

## The 8 pre-build questions — resolved with production defaults

| # | question | decision |
|---|---|---|
| 1 | capture path | **REST poller.** `market/v1/quote` FULL (incl. 5-level depth) + `historical/v1/getCandleData` + `marketData/v1/optionGreek`. No second WS — the trading feed is never touched. (WS mode-3 worker left as a future option in the audit.) |
| 2 | cadence | quote+greek every **20 s** (`CHANAKYA_HIST_QUOTE_SEC`), candle backfill every **90 s** (`CHANAKYA_HIST_CANDLE_SEC`, last ~10 bars/tf), off-hours **heartbeat** every **300 s** |
| 3 | chain width | **ATM ± 15** default, `CHANAKYA_HIST_CHAIN_WINDOW` (clamped 1–40) |
| 4 | instruments | `NIFTY` (cash index **+ NIFTY FUTIDX** front-month), `NATURALGAS`, `CRUDEOIL` (MCX FUTCOM front-month) + the option band for each. `CHANAKYA_HIST_SYMBOLS` override |
| 5 | storage | separate file **`data/market_history.db`** (`CHANAKYA_HIST_DB_PATH`), WAL, `synchronous=NORMAL`. Append-only, retention = keep everything (manual prune only) |
| 6 | raw payloads | **stored always**, gzip+base64, **deduped by SHA-256** of `endpoint+body`; every normalized row carries a `raw_id` FK |
| 7 | greeks off-hours | `optionGreek` → `AB9019`/`NO_DATA` → **no greek rows written**, the run logs it; never fabricated |
| 8 | credentials | read from the existing `ANGEL_*` env via `broker/angelone` auth (shared JWT, **no second login**). Missing → the worker logs `AUTH_UNAVAILABLE` per run and writes **no** market rows (heartbeats continue) |

---

## Files

| file | role |
|---|---|
| `app/histcap/schema.py` | DDL for 5 append-only tables; idempotent `init()` |
| `app/histcap/store.py` | `HistStore` — WAL connection, `put_raw` (SHA dedup), `write_candles/quotes/greeks` (`INSERT OR IGNORE`), per-cycle `transaction()`, `start_run`/`finish_run`, and **look-ahead-safe** `get_candles/get_quotes/get_greeks` (filter on `exch_ts`), `summary()` |
| `app/histcap/normalize.py` | broker payload → canonical rows; `to_utc_iso` (epoch-ms / ISO+05:30 / IST-naive → UTC `Z`), `norm_quote` (+ derived `basis` for FUTURE only), `norm_candles` (**closed bars only**), `norm_greeks` (delegates to `broker.angelone.greeks`) |
| `app/histcap/integrity.py` | `candle_check` (HARD reject `h<l`; SOFT flag `o<l`/`v<0`/`oi<0`), `quote_check` (`crossed_book`, `oi<0`), `greek_check` (`iv_oob` flag, **never clamped**), `monotonic_flag`, `gap_flag` |
| `app/histcap/worker.py` | `CaptureWorker` — market-hours-aware async loop, per-symbol `run_once`, instrument resolution (1 h cache), batched capture. CLI: `python -m app.histcap [--once]` |
| `app/histcap/api.py` | read-only `APIRouter` — `GET /api/histcap/{status,runs,candles,quotes,greeks}` |
| `app/histcap/__init__.py`, `__main__.py` | package exports + CLI entrypoint |
| `broker/angelone/client.py` | **added** `get_quotes_batch(tokens_by_exchange, mode)` — real multi-token `market/v1/quote` (≤ 50/exchange/POST); existing methods unchanged |
| `app/main.py` | wire `CaptureWorker` into `startup`/`shutdown` (guarded — a capture failure can never break the app) + `include_router` |
| `backend/tests/test_histcap.py` | 19 tests |

---

## Schema — `data/market_history.db`

```
raw_responses      id, received_ts, server_ts, endpoint, request_json, http_status, status,
                   sha256 UNIQUE, gzip_b64, run_id
market_candles     id, received_ts, instrument_key, symbol, kind(INDEX|FUTURE|OPTION), exchange,
                   token, expiry, strike, option_type, tf, bar_start, session_date_ist,
                   o,h,l,c, v(NULL if none), oi, oi_change, source, raw_id, flags, run_id
                   UNIQUE(instrument_key, tf, bar_start)
quote_snapshots    id, received_ts, server_ts, exch_ts, snap_key, instrument_key, symbol, kind,
                   exchange, token, expiry, strike, option_type, session_date_ist,
                   ltp, open, high, low, close, volume, oi, oi_change, avg_price, last_trade_qty,
                   bid, ask, bid_qty, ask_qty, tot_buy_qty, tot_sell_qty, depth_json,
                   net_change, pct_change, lower_circuit, upper_circuit, week52_high, week52_low,
                   basis(FUTURE only, DERIVED), quote_status, source, raw_id, flags, run_id
                   UNIQUE(token, snap_key)
option_greeks      id, received_ts, server_ts, snap_key, underlying, expiry, strike, option_type,
                   session_date_ist, delta, gamma, theta, vega, iv(fraction), iv_pct(raw %),
                   trade_volume, broker_status, source, raw_id, flags, run_id
                   UNIQUE(underlying, expiry, strike, option_type, snap_key)
capture_runs       id, started_ts, ended_ts, mode(POLL|POLL_ONCE|HEARTBEAT), market_state,
                   auth_ok, instruments_n, candles_written, quotes_written, greeks_written,
                   raw_written, integrity_json, errors_json, notes
```

`snap_key` = `exch_ts`/`server_ts` if present, else `received_ts` truncated to the second →
identical polls of an unchanged quote collapse. Closed candles are written once and never
rewritten; an **open** bar is never written (`norm_candles` drops any bar whose
`bar_start + tf > now`).

**Timestamps (never conflated):** `exch_ts` (exchange), `server_ts` (broker server /
`fetched_at`), `received_ts` (our wall clock, always set). All UTC ISO-8601 `Z`;
`session_date_ist` is the IST calendar date for day queries. IST = UTC + 5:30, no naive
conversions.

**Zero look-ahead:** `get_candles`/`get_quotes`/`get_greeks` take `as_of` and filter on
`bar_start` / `COALESCE(exch_ts, received_ts)` / `COALESCE(server_ts, received_ts)` — never
on `id` or `received_ts` alone.

---

## Fields captured (when authenticated + market open)

| dataset | fields | source |
|---|---|---|
| **Spot index** (NIFTY) | LTP, O/H/L/C, `volume` (NULL — a cash index has none), `exch_ts` | `market/v1/quote` FULL |
| **Spot index candles** | 1m/5m/15m OHLC (V NULL) | `historical/v1/getCandleData` |
| **NIFTY future** (FUTIDX) | LTP, OHLC, `volume`, `oi`, `oi_change`, **`basis` = fut − spot (DERIVED)**, circuits, net/pct change | `market/v1/quote` FULL |
| **NIFTY future candles** | 1m/5m/15m OHLCV | `historical/v1/getCandleData` |
| **MCX future** (NG/CRUDE FUTCOM) | LTP, OHLC, `volume`, `oi`, `oi_change`, `basis`, circuits | `market/v1/quote` FULL + candles |
| **Option chain** (ATM ± 15 CE & PE) | strike, expiry, token, LTP, O/H/L/C, `volume`, `oi`, `oi_change`, **bid/ask + qty**, `tot_buy/sell_qty`, **5-level `depth_json`**, circuits | `market/v1/quote` FULL (`get_quotes_batch`) |
| **Option Greeks** (per strike/type) | **Delta, Gamma, Theta, Vega, IV** (decimal fraction) + `iv_pct` (raw %) + `trade_volume` | **`marketData/v1/optionGreek` ONLY** |
| **Every call** | verbatim gzipped payload + request + http/broker status | `raw_responses` |
| **Every cycle** | counts, market state, auth flag, integrity summary, errors | `capture_runs` |

---

## Verification

**Unit / integration:** `pytest tests/test_histcap.py` → **19 passed**. Full backend suite
**349 passed** (was 330). `compileall` clean.

Covered: schema idempotency; `norm_quote` full-field map + missing→NULL + FUTURE-only
`basis` + depth→bid/ask; `to_utc_iso` epoch-ms / ISO+05:30 / IST-naive; `norm_candles`
**drops the open bar**; null volume stays null; greek delegation keeps `iv_pct`;
`candle_check` HARD reject `h<l` + SOFT flags; `quote_check` crossed book; `greek_check`
`iv_oob` **not clamped**; `put_raw` SHA dedup; `write_candles` idempotent on the natural
key (2nd write of the same bar = 0 rows) + rejects a HARD-bad row into `integrity.rejected`;
**full mocked cycle** writes quotes+greeks+candles with `raw_id` provenance; **second cycle
adds nothing** (dedup); **no-auth cycle** writes 0 market rows + a logged run;
**greek `NO_DATA`** writes 0 greeks + logs `AB9019`; `as_of` retrieval excludes bars/quotes
after the cutoff.

**Live CLI one-shot against real AngelOne** (`python -m app.histcap --once`, `.env` loaded,
markets closed 2026-09-02 ~08:40 IST):

```
auth_ok=1  instruments=82  quotes_written=82  candles_written=306  greeks_written=0  raw=18
integrity: {issues:[], rejected:[], flagged:0}
errors: 3 x {stage:"greeks", status:"NO_DATA", message:"AB9019"}   <- correct: no greeks outside market hours
```
- Real data captured: NIFTY future `basis` +34.5, OI 16,051,815; per-strike CE/PE with
  real LTP/OHLC/OI/bid/ask/5-level depth; NG/CRUDE future candles with real volume.
- **Second identical cycle → 0 new candles, ~0 new quotes** (UNIQUE natural keys + INSERT
  OR IGNORE working).
- 0 rows with `h<l`, 0 negative OI, 0 hard-rejected.
- Greeks return `AB9019` only because the market is closed; the `optionGreek` request now
  carries the `X-MACAddress` / client-IP headers it requires (was `AB1012` before the fix),
  so greeks will populate on the first in-hours cycle.

Fixes applied during verification (all data-layer): (1) `optionGreek`/quote/candle requests
now send the full AngelOne auth headers via `client._auth_headers()`; (2) candle window is
session-aware (`instruments.lookback_window`) so an off-hours poll still returns the last
session; (3) `ltp_far_from_book` only flags when a real 2-sided book exists (no false
positive on a cash index); (4) bogus epoch-0 (`1970-01-01`) timestamps normalise to `NULL`.

---

## Still unavailable / pending

| item | why | resolution |
|---|---|---|
| **Live *greek* rows** | market was closed during verification → `AB9019` | confirmed working (header fix); populates on the first in-hours capture cycle |
| **Option per-strike OHLCV candles** | off by default (`CHANAKYA_HIST_OPTION_CANDLES=0`) — one `getCandleData` call per option token is expensive at chain width | set the flag if wanted; `quote_snapshots` already give per-strike OHLC point-in-time |
| **`oi_change` on options via FULL quote** | AngelOne often omits `changeinOpenInterest` in the FULL quote (documented in the audit) | stored as `NULL` when absent; a WS mode-3 worker (future) is the reliable source |
| **rho / 2nd-order greeks** | AngelOne `optionGreek` returns Δ/Γ/Θ/V/IV only | not provided by the broker — no substitute computed |
| **Spot-index candle volume** | an NSE cash index has no traded volume | `v` stored `NULL` (never 0); the NIFTY **FUTIDX** candle carries real volume |

## Safety

| control | state |
|---|---|
| trading/signal logic | **untouched** — no engine, runner, `_persist_snapshot`, or `_autoscalp_chain` change |
| live orders | none — capture is read-only market data |
| trading WebSocket | never subscribed / imported / modified; capture is REST-only |
| trading DB (`chanakya.db`) | never written — capture uses its own `market_history.db` |
| app resilience | worker wired behind try/except in `main.py`; a capture crash cannot break startup or the trading loop |
| fabricated data | none — missing broker field → `NULL`; `AB9019` → no rows; `basis` is arithmetic on two real captured LTPs, labelled `DERIVED` |
| credentials | read from env only; never logged, never hard-coded |
