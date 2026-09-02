# Historical Market-Data Capture — Audit (no implementation)

**Date:** 2026-09-02 · **Goal:** a local, append-only historical dataset (spot/future
OHLC+LTP, futures LTP/OI/OI-change, per-strike CE/PE LTP/volume/OI/OI-change/bid-ask/depth,
IV + Delta/Gamma/Theta/Vega) so Pivot / S-R / OI-pressure / PCR / breakout / reversal /
Greek formulas can be backtested locally without Ask-Angel or external history.

**This document is the audit only.** Nothing below is implemented. Constraints for the
eventual build: real AngelOne API/WS data only; never fabricate a missing broker field
(store `NULL`); Greeks only from `marketData/v1/optionGreek`; keep raw + normalized
separately where practical; append-only, no overwrite; integrity checks + dup protection;
no trading/signal-logic change.

---

## 1. What is persisted TODAY

**One market-data table exists: `live_market_snapshots`** (13,832 rows, 2026-08-31 →
2026-09-02, 5,614 open-market). Written by `runner._persist_snapshot()` →
`db.insert_live_snapshot()`, one row per symbol per decision cycle (~30 s) **but only when
a full evaluation ran** (≥ 20 closed 5-minute bars). Warm-up / early session → no row.
Market closed → a `regime='MARKET_CLOSED'` heartbeat with all analytics `NULL`.

| stored column | what it is | for backtesting |
|---|---|---|
| `ts` (UTC ISO), `session_date`, `symbol`, `source`, `provenance` | decision-time metadata; `provenance` = `{"feed":"angel_ws","owner":…}` only | ok |
| **`index_ltp`** | the underlying's **last price, a single scalar** | ⚠️ **no OHLC, no series granularity** |
| `atm` | ATM strike | derived |
| `vwap`, `vwap_status`, `atr`, `pcr`, `max_pain`, `momentum`, `state_score`, `gex_flip/pin/regime_sign/sigma`, `support/resistance/*_strength`, `regime`, `mtf_alignment`, `signal_*`, `ev`, `rr` | **engine outputs** — already-computed indicator values | these are *what we want to re-derive*, not raw inputs |
| `feed_age_sec` | staleness of the LTP at decision time | ok |
| **`chain_json`** | JSON blob, **≤ 5 strikes (ATM ± 2)**, ~2.6 KB (20 KB cap never hit) | ⚠️ narrow + greeks NULL (below) |

### `chain_json` per-leg contents (real, sampled 2026-09-02)
Keys present: `ltp, oi, oi_chg, vol_delta, token, tradingsymbol, expiry, exchange_type,
delta, gamma, theta, vega, iv`.

| leg field | populated? | source |
|---|---|---|
| `ltp` | ✅ | `market/v1/quote` FULL per token |
| `oi` | ✅ | `market/v1/quote` FULL (`opnInterest`) |
| `oi_chg` | ⚠️ **mostly NULL** | `market/v1/quote` FULL (`changeinOpenInterest`) — broker rarely fills it in FULL quote |
| `vol_delta` | ⚠️ present, semantics unclear (it is `ce_volume` from the snapshot, not a verified cumulative volume) | `market/v1/quote` FULL (`tradeVolume`) |
| `delta, gamma, theta, vega, iv` | ❌ **always NULL** — `app/main._autoscalp_chain` hard-codes them to `None` | see §4 |
| `bid, ask, depth` | ❌ **absent** — never requested/kept | see §4 |

### Duplicate / integrity protection — **none structural**
`live_market_snapshots`: PK `id AUTOINCREMENT`; indexes `(symbol, ts)` and
`(session_date, symbol)` — **both non-unique**. A replay or a second writer could
double-insert (0 duplicate `(symbol, ts)` today only because there is a single writer). No
OHLC-ordering / monotonic-timestamp / OI-non-negative / strike-grid checks anywhere.

---

## 2. Built in memory, then DISCARDED (never written to any DB)

| producer | what it holds | why it's lost |
|---|---|---|
| `AngelMarketFeed._candles` | per-token **1-minute OHLC**, `deque(maxlen=240)`, **volume always 0** (built from LTP ticks) | in-RAM only; `get_candles()` is read by the aggregator seed and never persisted |
| `CandleAggregator._bars` | per-symbol **1m/3m/5m/15m/30m OHLC**, `deque(maxlen=400)`, **volume only from the one startup `seed_from_ohlc`** | in-RAM only; nothing writes `agg.snapshot()` to the DB |
| `angelone.fetch_candles()` results | broker **`historical/v1/getCandleData`** OHLCV (1m…1d) | fetched at startup seed + on-demand by `scalper.py` / `orchestrator.py` / `mcp_server.py`; consumed and dropped |
| every FULL quote fetched during chain build | `_quote_fields` extracts `ltp/open/high/low/close/volume/oi/oi_change/timestamp` | only `ltp/oi/oi_change/volume` are copied into `chain_json`; OHLC + timestamp dropped; no time series kept |

---

## 3. What is COMPLETELY missing for the stated goal

1. **A timestamped OHLCV candle store** for every tracked instrument (spot index, NIFTY
   future, MCX front-month future, and — optionally — each ATM-band option). Nothing like
   this table exists.
2. **NIFTY *futures* data.** `_underlying_ref("NIFTY")` tracks the **cash index**
   (`99926000`, AMXIDX) → **no OI, no volume, no futures LTP series**. The NFO FUTIDX
   contract (`68407` / 29SEP2026) is resolved only inside the VWAP path (`e34bcce`) and is
   not persisted.
3. **Futures OI + OI-change time series** (NIFTY FUTIDX, NG/CRUDE FUTCOM). Not captured at
   all.
4. **Bid / ask / 5-level depth** for options and futures. Never requested (the adapter can
   now read `depth` after `0f6af8e`, but nothing persists it).
5. **Option Greeks (IV, Δ, Γ, Θ, V)** — always `NULL` in `chain_json`. The adapter can now
   fetch them (`get_option_greeks`, `0f6af8e`) but they are not wired into
   `_autoscalp_chain` or persistence.
6. **A wider option chain.** Only ATM ± 2 (5 strikes) is captured; PCR / OI-wall / max-pain
   back-tests need the full liquid band (≈ ATM ± 10–15).
7. **Raw broker responses.** Everything stored is normalized-only; there is no way to
   re-derive a value or audit a normalization bug from history.
8. **Continuous capture.** Rows exist only when the strategy evaluated; multi-hour gaps
   during aggregator warm-up (measured 132 min on 2026-09-01) and no capture pre-eval.

---

## 4. Field → exact AngelOne source (every missing field)

`REST` = HTTPS endpoint · `WS mode 1` = LTP (51-byte packet, **currently used**) ·
`WS mode 2` = Quote (~123 B) · `WS mode 3` = SnapQuote (~379 B). The binary parser
(`angel_ws.parse_binary`) decodes **only the 51-byte LTP packet** and explicitly drops
larger packets — modes 2/3 need a parser extension.

| dataset · field | captured now | AngelOne source(s) | notes |
|---|---|---|---|
| **Spot index — LTP** | ✅ scalar (`index_ltp`) | WS mode 1 (`99926000`); `market/v1/quote` LTP mode | no series, only the latest value |
| **Spot index — OHLC 1m/5m/…** | ❌ (RAM only) | **REST `historical/v1/getCandleData`** (history); **WS mode 2** (live-built) | NSE cash index → volume always 0 |
| **NIFTY future — LTP** | ❌ | `market/v1/quote` FULL on FUTIDX `68407`; WS mode 1/2 on that token | resolve via `instruments.resolve_index_future` |
| **NIFTY future — OI** | ❌ | `market/v1/quote` FULL (`opnInterest`) on FUTIDX; **WS mode 2/3** | |
| **NIFTF future — OI change** | ❌ | `market/v1/quote` FULL (`changeinOpenInterest`, intermittent); **WS mode 3** (`oiChange`) | broker often omits in FULL quote → mode 3 is the reliable one |
| **NIFTY future — OHLCV 1m/5m/…** | ❌ | **REST `historical/v1/getCandleData`** on FUTIDX; WS mode 2 | future carries real volume |
| **MCX future (NG/CRUDE) — LTP** | ⚠️ `index_ltp` (for MCX this IS the front-month future) | WS mode 1 on FUTCOM | ok as latest value; no series |
| **MCX future — OHLCV** | ❌ (RAM only) | **REST `historical/v1/getCandleData`** on FUTCOM; WS mode 2 | |
| **MCX future — OI / OI-change** | ❌ | `market/v1/quote` FULL; **WS mode 2/3** | |
| **Option — strike / CE-PE / token / expiry** | ✅ (`chain_json`) | instrument master (`OpenAPIScripMaster.json`) | |
| **Option — LTP** | ✅ (`chain_json`) | `market/v1/quote` FULL per token; WS mode 1 | |
| **Option — volume** | ⚠️ `vol_delta` (semantics unverified) | `market/v1/quote` FULL (`tradeVolume`); WS mode 2 | confirm cumulative vs delta |
| **Option — OI** | ✅ (`chain_json`) | `market/v1/quote` FULL (`opnInterest`); WS mode 2/3 | |
| **Option — OI change** | ⚠️ `oi_chg` mostly NULL | `market/v1/quote` FULL (`changeinOpenInterest`); **WS mode 3** (`oiChange`) | |
| **Option — bid / ask / 5-level depth** | ❌ | `market/v1/quote` **FULL** `depth` (best-5 `{price, quantity, orders}` each side); **WS mode 3** SnapQuote | adapter reads `depth` since `0f6af8e`, not persisted |
| **Option — IV** | ❌ (NULL) | **REST `marketData/v1/optionGreek`** (`impliedVolatility`, a %) | ONLY source; store fraction + raw % |
| **Option — Delta / Gamma / Theta / Vega** | ❌ (NULL) | **REST `marketData/v1/optionGreek`** | ONLY source; **not in any WS mode** |
| **Option — per-strike OHLCV series** | ❌ (not built even in RAM) | **REST `historical/v1/getCandleData`** per option token; WS mode 2 on that token | one candle call per option token — expensive at chain width; capture selectively (ATM band) |
| **Depth level quantities / order counts** | ❌ | `market/v1/quote` FULL `depth[].{price, quantity, orders}` (5 levels each side); WS mode 3 | store the full 5-level structure, not just best bid/ask |
| **Futures basis** (`future_ltp − spot_ltp`) | ❌ | **DERIVED** — not a broker field | store in its own column, `source="DERIVED"`; requires the spot LTP + the future LTP captured at the same tick. This is arithmetic on two real fields, not a fabricated broker value. |
| **All instruments — extra quote fields** (open/high/low/close, `netChange`, `percentChange`, `upper/lowerCircuit`, `52WeekHigh/Low`, `totBuyQuan/totSellQuan`, `avgPrice`, `lastTradeQty`) | ❌ | `market/v1/quote` FULL; WS mode 2 (OHLC, circuits) / mode 3 (+circuits, 52wk) | pass-through, useful for regime/vol context |

### AngelOne endpoints in play
| endpoint | method | gives | cadence limit |
|---|---|---|---|
| `market/v1/quote/` | POST | LTP / OHLC / `tradeVolume` / `opnInterest` / `changeinOpenInterest` / 5-level `depth` / circuits / 52wk — per token, up to 50 tokens/call (mode LTP/OHLC/FULL) | ~1 req/s advisory |
| `marketData/v1/optionGreek` | POST | Δ/Γ/Θ/V/IV for **all** live strikes of one `{name, expirydate}` — live contracts only, `AB9019` outside hours | one call per underlying+expiry (adapter already caches 15 s, `0f6af8e`) |
| `historical/v1/getCandleData` | POST | OHLCV candles 1m/3m/5m/10m/15m/30m/1h/1d for one token + date range | ~3 req/s advisory; ~2000 candles/call |
| WS `smart-stream` | binary | per-subscribed-token push; mode 1 LTP, mode 2 +OHLC/volume/OI, mode 3 +depth/circuits/oiChange | one socket; ≤ ~1000 tokens |

---

## 5. WS mode reality (why "on every market update" is not free today)

The feed subscribes **mode 1 only** (`{"params":{"mode":1,…}}`). Each tick delivers
`token, exchange_type, ltp, ts_ms` — nothing else. To capture OHLC/volume/OI/depth **from
the socket** the feed must move to mode 2 or 3, which means:
- extend `parse_binary` to decode the 123-byte (mode 2) and 379-byte (mode 3) packets;
- the feed is **shared with live trading monitoring** (`get_ltp` for position marks) — a
  mode/parser change touches that path and needs its own regression pass.

**Alternative that avoids the shared-feed risk:** a **separate capture worker** that (a)
subscribes its own mode-3 socket for the tracked tokens, or (b) polls `market/v1/quote`
FULL + `historical/v1/getCandleData` + `marketData/v1/optionGreek` on a fixed cadence and
writes straight to the capture DB. This keeps the trading feed untouched (recommended;
see §7 open questions).

---

## 6. Proposed target schema — DESIGN ONLY, not implemented

Append-only. Suggest a **separate SQLite file** (`data/market_history.db`, `CHANAKYA_HIST_DB_PATH`)
so capture volume never bloats or locks the trading DB. All times UTC ISO.

**Three timestamps kept on every captured row** (never conflated):
- `exch_ts` — the exchange's own timestamp from the broker payload (`exchangeTimestamp` /
  candle bar time). `NULL` if the payload omitted it.
- `server_ts` — the broker/API server time (`server_timestamp` in a quote/greek response,
  else the HTTP response `Date`). `NULL` if absent.
- `received_ts` — wall-clock in our process when the packet/response was decoded. Always
  set (this is ours, not the broker's).

```
market_candles              -- one row per (instrument, timeframe, bar_start); UNIQUE(instrument_key, tf, bar_start)
  id, received_ts, exch_ts (bar time), instrument_key (e.g. "NSE:99926000" / "NFO:68407" / "MCX:<fut>" / "NFO:<opt token>"),
  symbol, kind (INDEX|FUTURE|OPTION), exchange, token, expiry, strike, option_type,
  tf ("1m".."1d"), bar_start, o, h, l, c, v (NULL if broker gave none), oi, oi_change,
  source ("ANGELONE_CANDLES" | "ANGELONE_WS_M2"), ingest_run_id

quote_snapshots             -- point-in-time FULL quote per token; UNIQUE(token, exch_ts)  (fallback: received_ts)
  id, received_ts, server_ts, exch_ts, instrument_key, symbol, token, exchange, kind, expiry, strike, option_type,
  ltp, open, high, low, close, volume, oi, oi_change, avg_price, last_trade_qty,
  bid, ask, bid_qty, ask_qty, tot_buy_qty, tot_sell_qty,
  depth_json (5-level buy/sell: [{price, quantity, orders} x5] each side),
  net_change, pct_change, lower_circuit, upper_circuit, week52_high, week52_low,
  basis (future_ltp - spot_ltp, FUTURE rows only; source="DERIVED"),
  source ("ANGELONE_QUOTE_FULL" | "ANGELONE_WS_M3"), raw_ref (-> raw_broker_responses.id)

option_greeks_ts            -- one optionGreek pull, expanded per strike/type; UNIQUE(underlying, expiry, strike, option_type, received_ts)
  id, received_ts, server_ts, underlying, expiry, strike, option_type,
  delta, gamma, theta, vega, iv (decimal fraction), iv_pct (raw %), trade_volume,
  broker_status ("OK"|"NO_DATA"|"AB9019"|...), raw_ref

raw_broker_responses        -- verbatim payloads for audit / re-normalization
  id, received_ts, endpoint, request_json, http_status, response_headers_json, response_json (or gzip blob), ingest_run_id

capture_runs                -- provenance + integrity summary per capture cycle
  id, started_ts, ended_ts, mode ("WS_M3" | "POLL"), tokens_n, rows_written,
  errors_json, integrity_json (checks below)
```

Every normalized row carries a `raw_ref` FK to the verbatim payload it was parsed from, so
raw and normalized are stored **separately** and a normalization bug can be replayed
against history.

**Duplicate protection:** `UNIQUE` constraint per natural key + `INSERT OR IGNORE`
(candles keyed on `bar_start`, so re-fetching a session is idempotent; a *closed* bar is
never rewritten, an *open* bar is only inserted once its `bar_start` bucket has rolled).

**Automatic integrity checks (write to `capture_runs.integrity_json`, never mutate data):**
`l ≤ min(o,c) ≤ max(o,c) ≤ h`; `v ≥ 0`; `oi ≥ 0`; monotonic non-decreasing `bar_start`
per (instrument, tf); no gap > 2× the timeframe during market hours (flag, don't fill);
strike ∈ the master's strike grid; `expiry ≥ session_date`; `iv` within a sane band with
the actual value flagged, not clamped; greek row count vs strikes requested.

---

## 7. Open questions for you before implementation

1. **Capture path** — separate mode-3 WS worker, or a fixed-cadence REST poller
   (recommended: REST poller; zero risk to the trading feed). Which?
2. **Cadence** — quote/greek/OI snapshot every N seconds (e.g. 15 s), candles backfilled
   every 1–5 min from `historical/v1/getCandleData`. Acceptable?
3. **Chain width** — ATM ± how many strikes for capture (10? 15?). Wider = more quote
   calls per cycle.
4. **Instruments** — NIFTY (spot **+ FUTIDX**), BANKNIFTY?, NG + CRUDE FUTCOM, plus the
   option band for each. Confirm the list.
5. **Storage** — separate `market_history.db` file (recommended) vs new tables in
   `chanakya.db`. Retention / rotation policy?
6. **Raw payloads** — keep verbatim JSON for every call (storage cost), or only on a
   normalization mismatch / error?
7. **Greeks outside market hours** — `optionGreek` returns `AB9019`; capture just records
   `broker_status='NO_DATA'` and moves on — confirm that's the wanted behavior.
8. **Credentials** — capture needs authenticated REST; `ANGEL_*` are currently blank in
   the env (see `broker/angelone/ADAPTER_UPGRADE.md`). Restore before any live capture.

---

## 8. Constraints check (for the eventual build)

| constraint | how it will be met |
|---|---|
| real AngelOne data only | every field maps to a named endpoint/WS mode in §4; no third source |
| never fabricate a missing field | absent broker field → column `NULL`; `_f()` in `broker/angelone/greeks.py` already does string→`None` |
| Greeks from the official endpoint only | `marketData/v1/optionGreek` via `client.get_option_greeks` (`0f6af8e`) |
| raw + normalized kept separately | `raw_broker_responses` + `raw_ref` FK on every normalized row |
| append-only, no overwrite | `INSERT OR IGNORE` on natural-key `UNIQUE`; closed bars never rewritten |
| integrity checks + dup protection | §6 checks → `capture_runs.integrity_json`; `UNIQUE` keys |
| no trading/signal-logic change | capture is a **new** module/worker + **new** tables/DB; `runner._persist_snapshot`, `_autoscalp_chain`, and every engine stay byte-identical |
