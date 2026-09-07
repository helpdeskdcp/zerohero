# ORDERFLOW STAGE 9 — TICK / LEVEL-2 DATA AUDIT

**Status: STOPPED AFTER PHASE A (DATA AUDIT).**

> **Stage-9 cannot be validated because the required tick / L2 data is unavailable.**

No `orderflow_stage9_l2.py`, no `data/orderflow_stage9_l2_events.csv`, and no new
research tests were created, because there is no real historical tick-level or
order-book-update data in this environment to build them from. Phases B–E were
**not** run. Fabricating, simulating, or approximating the missing fields was
explicitly out of scope and was not done.

Production trading behaviour is **unchanged** — this stage created one Markdown
file and nothing else. No code path, config, schema, service, or router was
touched. `live_trading` remains `false`, `paper_mode` remains `true`.

Stage-7 and Stage-8 conclusions are preserved verbatim (see
`ORDERFLOW_STAGE7_BOUNDARY.md`, `ORDERFLOW_STAGE8_H7_FADE.md`). The validated
reclaim-distance boundary is untouched. H7 remains **AVOID** (not a fade). H1_CONT
remains research-only and CRUDEOIL-only.

---

## PHASE A — DATA AUDIT

### What was searched

- Every `*.db` / `*.sqlite` under `/root` (both `zerohero` and the sibling
  `oi_dashboard` repo).
- `backend/app/connectors/angel_ws.py` — the live Angel One WebSocket client.
- `backend/app/histcap/` — the REST poll capture worker that owns
  `backend/data/market_history.db`.
- `backend/app/market_hub.py`, `backend/scripts/orderflow_histsrc.py` — the read
  paths the order-flow research already uses.
- `backend/data/*.jsonl` probe/evidence logs
  (`feed_staleness_probe.jsonl`, `session_evidence.jsonl`,
  `autoscalp_monitor_*.jsonl`).
- `raw_responses.endpoint` in `market_history.db` — the ground-truth list of
  every broker endpoint whose payload was ever persisted.
- Repo-wide grep for `depth`, `bid`, `ask`, `tick`, `orderbook`, `l2`,
  `SnapQuote`, `aggressor`, `delta`, `signed_volume`, `footprint`, `absorption`,
  `iceberg`, `imbalance`, `cvd`.

### Market-data feeds that actually exist

| feed | mechanism | mode / endpoint | fields captured | persisted? |
|---|---|---|---|---|
| **Angel One WebSocket** (`app/connectors/angel_ws.py`, a hand-rolled binary client — *not* the `SmartWebSocketV2` SDK class) | binary WS | **mode 1 = LTP only** (hard-coded: `{"action":1,"params":{"mode":1,...}}`, line 200; 51-byte LTP packet, line 15). Larger quote/snap-quote packets are explicitly *ignored* (line 70). | last-traded price + timestamp only. No bid/ask, no sizes, no depth, no volume (synthetic 1m OHLC is built with volume hard-coded to 0, line 180). | **No.** Feeds an in-memory LTP cache + a light 1-minute OHLC for the scalp runner. Nothing written to disk. `feed_staleness_probe.jsonl` logs only `{connected, age, last_tick_ts}` freshness metadata — not tick content. MCX futures (CRUDEOIL/NATURALGAS) are not even WS-subscribed. |
| **REST full-quote poll** (`app/histcap/worker.py`, `store.py`, `normalize.py`) | HTTP GET, cadence `CHANAKYA_HIST_QUOTE_SEC` **default 20 s** (observed spacing ~20–30 s; multi-minute gaps occur) | `market/v1/quote` mode `"FULL"` (hard-coded) → `quote_snapshots` table, `source="ANGELONE_QUOTE_FULL"`. Dedup key `UNIQUE(token, snap_key)`, `snap_key` = ts truncated to the **second** → ≤ 1 row/s/instrument. | ltp, o/h/l/c, cumulative volume, oi, `avg_price`, `last_trade_qty` (size only, **no side**), `bid`, `ask`, `bid_qty`, `ask_qty`, `tot_buy_qty`, `tot_sell_qty` (day-cumulative pending), `depth_json` (5-level buy/sell: price/quantity/orders). `normalize.norm_quote()` does parse the ladder. | **Yes**, `market_history.db`. **But no consumer code reads the book columns** — `market_hub`, `orderflow_histsrc`, and every order-flow script select only OHLC / ltp / oi / volume. `bid`,`ask`,`bid_qty`,`ask_qty`,`tot_buy_qty`,`tot_sell_qty`,`depth_json` are written and never read (grep: 0 consumers, 2 doc-string mentions). |
| **REST candles** (`app/histcap/…`) | HTTP GET | `historical/v1/getCandleData` → `market_candles` | 1m/3m/5m/15m/30m/1h OHLC + volume (volume present for FUTURE, NULL/0 for cash INDEX). **OHLC only — the broker historical API returns nothing else.** | **Yes.** |
| **REST option greeks** | HTTP GET | `marketData/v1/optionGreek` → `option_greeks` | delta/gamma/theta/vega/iv per strike (option risk greeks — **not** order-flow trade delta) | **Yes.** |
| **oi_dashboard `oi_history.db`** (sibling repo, read-only, used in Stage-3..8) | its own poll loop | — | `live_candles` (OHLC, `volume` column is **0** in 184,746 / ~186,000 rows), `cycles` (underlying_ltp, atm), `strikes` (per-strike ltp/oi/vol/greeks) | **Yes**, but no L1/L2/tick content. |

**Only three broker endpoints were ever persisted** (`raw_responses`):
`market/v1/quote` (18,508), `historical/v1/getCandleData` (14,126),
`marketData/v1/optionGreek` (10,600). There is **no** trade-tick endpoint, **no**
market-depth endpoint, **no** order-book-stream persistence anywhere.

### DATA AVAILABILITY MATRIX

`usable?` is judged against the Stage-9 requirement: enough real data, across
enough independent sessions/regimes, to form a **train / validation / OOS /
holdout** split without look-ahead.

| field | source | symbol(s) | hist / live | coverage | usable? | reason |
|---|---|---|---|---|---|---|
| trade price (LTP) | REST full-quote poll | NIFTY, CRUDEOIL, NATURALGAS futures (+ index, options) | historical (persisted) | **3 sessions**: 2026-09-02, -03, -04 (Sep-01 = 99 rows; Aug = 1–4 rows) | **INSUFFICIENT DATA** | 3 sessions, one regime, one week; ~25–30 s snapshots (not ticks) |
| trade price (LTP) | Angel WS mode 1 | same | live only | ephemeral | **NO** | not persisted; in-memory cache only |
| timestamp resolution | REST full-quote poll | futures | historical | `exch_ts` truncated to **1 s**; poll interval ~25–30 s (multi-minute gaps common) | **NO (as tick)** | 1 Hz stamp, sub-0.03 Hz sampling — cannot resolve trade-by-trade events |
| **trade quantity (per trade)** | — | — | — | **none** | **UNOBSERVABLE** | no trade-level record exists anywhere |
| **trade direction / aggressor flag** | — | — | — | **none** | **UNOBSERVABLE** | never captured; Angel full-quote carries no per-trade side; no tick feed |
| last-trade size (`last_trade_qty`, no side) | REST full-quote poll | futures/index/options | historical | 3 sessions | **INSUFFICIENT DATA** | size only, no direction; snapshot cadence |
| bid / ask (L1) | REST full-quote poll `bid`,`ask` | NIFTY, CRUDEOIL, NATURALGAS futures | historical | 3 sessions; per session per symbol ~1,160–1,660 non-null snaps | **INSUFFICIENT DATA** | data is real but 3 sessions / one regime; not an update stream |
| bid size / ask size (L1) | REST full-quote poll `bid_qty`,`ask_qty` | same | historical | 3 sessions | **INSUFFICIENT DATA** | same |
| **market depth — L2 levels** | REST full-quote poll `depth_json` | NIFTY, CRUDEOIL, NATURALGAS futures | historical | 3 sessions; **10,928** future rows with a sane 5-level book (10,927 pass a top-of-book sanity test) | **INSUFFICIENT DATA** | max **5 levels**; **snapshot only** (no add/cancel/modify events between polls); 3 sessions |
| depth quantities | `depth_json[].quantity` | same | historical | 3 sessions | **INSUFFICIENT DATA** | as above |
| **order-book update stream** (adds / cancels / modifies) | — | — | — | **none** | **UNOBSERVABLE** | only periodic snapshots are stored; no event stream is captured at any point |
| tot_buy_qty / tot_sell_qty (day-cumulative pending) | REST full-quote poll | futures/index/options | historical | 3 sessions | **INSUFFICIENT DATA** | day-cumulative, not per-interval; not aggressor volume |
| trade volume (cumulative) | REST full-quote poll + `market_candles.v` | futures | historical | candles ~2026-09-01..04; quotes Sep 02..04 | **INSUFFICIENT DATA** | cumulative only; no by-side breakdown; ~4 sessions |
| bar volume | `market_candles.v` | futures (NIFTY/CRUDEOIL/NATURALGAS + index-future set) | historical | 1m/5m across ~2026-09-01..04 | **INSUFFICIENT DATA** | already used in Stage-5..8; NULL for cash index |
| bar volume (oi_dashboard) | `oi_history.live_candles.volume` | NIFTY/BANKNIFTY/etc | historical | ~2026-07-13..08-28 | **NO** | column is `0` in 99.3 % of rows |
| historical tick / depth backfill | Angel `getCandleData` | — | — | OHLC only | **NO** | broker historical API returns candles only; no tick/depth history purchasable through the wired connector |
| constituent / underlying causation | index vs future LTP (both from the same 25–30 s poll) | NIFTY | historical | 3 sessions | **UNOBSERVABLE (as causation)** | only lagged correlation is computable; true causation needs constituent-level trade flow. Stage-5 already **REJECTED** index/future lead-lag as an edge. |

### Historical vs live, in one line

- **Live:** LTP only (WS mode 1), not stored.
- **Historical / stored:** OHLC candles, ~25–30 s full-quote snapshots (incl. L1 +
  a 5-level depth snapshot), option greeks — for **3 usable sessions**
  (2026-09-02 … 2026-09-04), futures NIFTY / CRUDEOIL / NATURALGAS.
- **Never present:** per-trade ticks, trade side / aggressor flag, order-book
  event stream, depth beyond 5 levels, any tick/depth history before 2026-09-01.

---

## PHASES B–E — NOT RUN

Phase B (causal dataset), Phase C (hypotheses A–G), Phase D (anti-overfitting),
Phase E (classification) all require a real tick / L2-update dataset spanning
multiple independent sessions and regimes. That does not exist here, so running
them would mean inventing the inputs — which is prohibited by the brief.

For the record, the mapping of each requested hypothesis to the data reality:

| # | hypothesis | minimum data required | status in this environment |
|---|---|---|---|
| A | trade delta / signed volume | per-trade price + size + inferred side (tick data) | **UNOBSERVABLE** — no trade-level data |
| B | bid-ask imbalance | L1 bid/ask sizes over time | data exists (3 sessions, 25–30 s snaps) → **INSUFFICIENT DATA** |
| C | depth imbalance | L2 depth quantities over time | data exists (5-level snapshot, 3 sessions) → **INSUFFICIENT DATA** |
| D | aggressive buying / selling | trade side classification | **UNOBSERVABLE** |
| E | absorption | trade flow vs resting-book change at high resolution | **UNOBSERVABLE** — 25–30 s snapshots cannot attribute book changes |
| F | footprint-style concentration | per-price traded volume by side | **UNOBSERVABLE** |
| G | liquidity withdrawal / replenishment | order-book add/cancel event stream | **UNOBSERVABLE** — no event stream captured |

No feature was combined with any other (Phase C rule), because no feature was
tested at all.

---

## FINAL DELIVERABLE SUMMARY

### 1. DATA AVAILABLE
- OHLC candles (1m…1h), FUTURE volume included, ~2026-09-01 … 2026-09-04, for
  NIFTY / CRUDEOIL / NATURALGAS futures (+ a wider index/future set for
  2026-09-04 only).
- REST full-quote snapshots at ~25–30 s cadence for **2026-09-02, -03, -04**:
  LTP, cumulative volume, `avg_price`, `last_trade_qty` (size only),
  L1 `bid`/`ask`/`bid_qty`/`ask_qty`, `tot_buy_qty`/`tot_sell_qty`,
  and a **5-level** `depth_json` book (~10.9k sane future rows).
- Option greeks (delta/gamma/theta/vega/iv) per strike, ~2026-09-01 … -04, plus
  the older `oi_dashboard` strike/greek history (~2026-07-13 … 08-28).

### 2. DATA MISSING
- Per-trade **tick data** (price, size, time) — entirely absent.
- **Trade aggressor / direction** flag — entirely absent, cannot be inferred
  without ticks.
- **Order-book update stream** (adds / cancels / modifies) — absent; only
  periodic snapshots are stored.
- Depth **beyond 5 levels** — absent.
- Any tick or depth data **before 2026-09-01** — absent; the broker's wired
  historical API is OHLC-only.
- More than **3 sessions** of L1/L2 snapshots, and any second market regime —
  absent. No sessions captured after 2026-09-04.
- Live-feed depth: the WebSocket runs in **LTP mode (mode 1)** and would need a
  mode-3 (SnapQuote) subscription *and* a persistence path to ever produce
  stored depth.

### 3. FEATURES TESTED
**None.** The audit gate (Phase A) failed, so Phases B–E were not entered. No
`orderflow_stage9_l2.py`, no events CSV, no new research tests.

### 4. TRAIN / VALIDATION / OOS / HOLDOUT RESULTS
**None produced.** A 4-way chronological split is impossible from 3 same-week
sessions in a single regime.

### 5. CLASSIFICATION OF EACH COMPONENT

| component | classification |
|---|---|
| A. trade delta / signed volume | **UNOBSERVABLE** |
| B. bid-ask imbalance | **INSUFFICIENT DATA** (real L1 exists; 3 sessions, snapshot cadence) |
| C. depth imbalance | **INSUFFICIENT DATA** (real 5-level snapshot exists; 3 sessions, no event stream) |
| D. aggressive buying / selling | **UNOBSERVABLE** |
| E. absorption | **UNOBSERVABLE** |
| F. footprint-style concentration | **UNOBSERVABLE** |
| G. liquidity withdrawal / replenishment | **UNOBSERVABLE** |
| constituent / underlying causation | **UNOBSERVABLE** as causation (Stage-5 already REJECTED lead-lag as an edge) |
| reclaim-distance boundary (Stage-7) | **SUPPORTED** — unchanged, not re-tested here |
| H7 = AVOID (Stage-8: fade REJECTED) | unchanged |
| H1_CONT (CRUDEOIL-only) | **PROMISING**, research-only — unchanged |
| anything | **PROVEN** — nothing |

### 6. EXACT NEXT RESEARCH STEP
Acquire a **real aggressor-classified tick feed + full-depth (L2) order-book
stream** for CRUDEOIL and NIFTY futures — either by:
  (a) subscribing the Angel One WebSocket in **mode 3 (SnapQuote)** *and* adding
      an append-only persistence table (`depth_ticks`: ts, token, 5×bid/ask
      price+qty+orders, ltq, ltt, vol) in the histcap worker — this yields
      best-5 depth + last-trade size at ~1 Hz, still **no true aggressor side**;
      **or**
  (b) obtaining a third-party historical tick/L2 dataset (full-depth, trade side)
      for these instruments spanning **≥ 3 months and ≥ 2 regimes**.
Only after **≥ 40 sessions across ≥ 2 regimes** of such data exists should
Stage-9 Phases B–E be attempted, re-using the frozen Stage-7 / Stage-8 event
labels as the target and a pre-declared parameter grid.

Until then, the price-only model (Stage-5 → Stage-8) stands as the final
conclusion, and the shadow-mode `H1H7StateEngine` integration plan (A–E,
presented separately, not yet built) is the correct next *application* step —
independent of Stage-9.

### 6a. Follow-up — "200 % L2 imbalance confirmation gate" (2026-09-07)

A research-first probe of the specific gate `buy-pressure / sell-pressure ≥ 2.0`
was run (`scripts/orderflow_l2_imbalance_probe.py`, read-only; report
`ORDERFLOW_L2_IMBALANCE_GATE.md`). Outcome, consistent with this audit:

- **Gate as specified = CANNOT IMPLEMENT.** It needs *aggressive-buy /
  aggressive-sell* classified flow — `UNOBSERVABLE`, never synthesised.
- **Passive-book variant** (`bid_qty/ask_qty`, `Σ₅ depth`, `tot_buy/tot_sell` —
  genuine fields) was built as a read-only component with a persistence
  requirement (≥ P consecutive ~30 s snapshots) and the full metric set
  (ratio, direction, persistence, MFE, MAE, target/SL hit, time-to-target/SL).
  On the 3 usable same-week futures sessions it shows a small, threshold-flat,
  MAE-neutral lift that **cannot be walk-forward validated** → **REJECTED at the
  feasibility gate** (untestable, not disproven). 200 % is not a special value
  (threshold sweep flat); more persistence does not help.

### 6b. L2 DATA ACQUISITION REQUIREMENT (spec)

Everything below `INSUFFICIENT DATA` / `UNOBSERVABLE` in §5, plus the 200 %
imbalance gate (§6a), is blocked on the **same** data gap. This is the spec for
closing it. Nothing here is a code change — it is what must be *acquired and
persisted* before Phases B–E or `ORDERFLOW_L2_IMBALANCE_GATE.md` can run for
real.

**Instruments (minimum):** NIFTY and CRUDEOIL futures (the two the frozen
Stage-6/7/8 work is anchored on). NATURALGAS futures desirable. Add BANKNIFTY
futures only if it is cheap to include.

**Fields required, by tier** — a route only unlocks the components it actually
carries:

| tier | fields | unlocks |
|---|---|---|
| **T1 — L2 depth stream** | every book change (or ≥ 4 Hz snapshots) of ≥ 5 levels per side: price, resting qty, order count; plus best-bid/best-ask and their sizes | B (bid-ask imbalance), C (depth imbalance), **passive** side of the 200 % gate, partial G (level qty deltas between updates) |
| **T2 — trade prints with size** | every trade: timestamp (≥ ms), price, size | tighter event timing; still **not** delta without a side |
| **T3 — aggressor side** | per trade: buy/sell aggressor flag **from the feed** (not tick-rule-inferred), OR a full order-book feed from which absorption is directly measurable | A (trade delta / signed volume), D (aggressive buy/sell), **aggressor** side of the 200 % gate, E (absorption), F (footprint) |
| **T4 — L2 event stream** | explicit add / modify / cancel messages with order ids or per-level deltas | full G (liquidity withdrawal / replenishment) |

Tick-rule inference (uptick ⇒ buy) is **not** an acceptable substitute for T3 —
it is an estimate, and estimating missing L2 is prohibited by the brief. If only
T1+T2 are obtainable, only B / C / passive-gate can be researched; A / D / E / F
stay `UNOBSERVABLE` and must be reported as such.

**Resolution:** native event stream, or ≥ 4 Hz if snapshot-only. The current
~25–30 s cadence is ~100–1000× too coarse — at that spacing `R` = spread is
smaller than a between-sample move and MFE/MAE saturate (see
`ORDERFLOW_L2_IMBALANCE_GATE.md` §6).

**Coverage (the hard gate):** ≥ **40 independent trading sessions** spanning
≥ **2 distinct volatility regimes** (e.g. a low-VIX drift stretch and a
high-VIX / event stretch), contiguous enough to form a chronological
**TRAIN 45 % / VALIDATION 20 % / OOS 17 % / HOLDOUT 18 %** split with the
HOLDOUT genuinely untouched. 3 same-week sessions (what exists now) cannot move
any verdict.

**Persistence / storage:** append-only, immutable, its own table(s) in a
separate research DB (do **not** touch `market_history.db` schema or the frozen
capture path). Suggested `depth_events` (ts_utc, exch_ts, token, side, level,
price, qty, orders, event_type) and `trade_prints` (ts_utc, exch_ts, token,
price, qty, aggressor). Preserve raw feed payloads (gzip + sha256) exactly as
histcap does. No synthetic rows, ever; a gap is a gap.

**Two routes (from §6, with what each does NOT give):**

- **(a) Angel One WS mode 3 (SnapQuote) + a new append-only persistence path in
  the histcap worker.** Yields **T1** (best-5 depth) + `last_trade_qty` at
  ~1 Hz. Does **NOT** give T3 (no aggressor flag) or T4 (no event stream). ⇒
  unlocks B / C / passive-gate only, after ≥ 40 sessions accumulate — i.e. a
  ~2-month forward capture. Cheapest, but the aggressor components stay blocked.
- **(b) Third-party historical tick + full-depth L2 dataset** (T1–T4, with a
  real aggressor side) for these instruments, ≥ 3 months, ≥ 2 regimes. Unlocks
  **all** of A–G immediately with no waiting. This is the only route that makes
  the 200 % gate *as specified* testable.

**Acceptance criteria before Phases B–E / the 200 % gate are run:**
1. ≥ 40 sessions, ≥ 2 regimes, HOLDOUT slice never inspected during design.
2. Feed-native aggressor side for anything in tiers A / D / E / F (else those
   stay `UNOBSERVABLE`).
3. A **pre-declared** parameter grid (thresholds, persistence, horizons,
   geometry) and pre-declared success gates, registered before the OOS/HOLDOUT
   slices are touched.
4. Target labels = the **frozen** Stage-7 / Stage-8 event set (reclaim-distance
   boundary, H7 = AVOID). The gate is judged only as a *confirmation filter* on
   those — it never redefines them.
5. Reject any component that does not show a **statistically stable**
   improvement across VALIDATION **and** OOS **and** HOLDOUT. `PROVEN` stays
   reserved for a later, second untouched multi-month holdout.

### 7. PRODUCTION TRADING BEHAVIOUR — CONFIRMATION
**Unchanged.** This stage added exactly one file — this Markdown report. No
change to any engine, adapter, service, router, schema, config, or frozen
AutoScalp parameter. No BUY/SELL/order/entry_signal logic was written. No
option-premium inference. No weighted / AI / confidence score. `/api/health`
still reports `"live_trading": false, "paper_mode": true`.
