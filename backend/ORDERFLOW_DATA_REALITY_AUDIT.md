# ORDER FLOW — DATA-REALITY AUDIT  +  v2 DATA SPEC

_2026-09-09. v1 NO-GO is accepted. This document does NOT optimize v1. It audits
every order-flow feature we compute, classifies each as TRUE ORDER FLOW vs a
proxy, and establishes exactly what real order-flow data Angel One can and
cannot give us — before any v2 code._

---

## PART 1 — FEATURE INVENTORY & CLASSIFICATION

Classes:
- **TRUE ORDER FLOW** — derived from individual trade prints with a known (or
  book-inferred) aggressor side.
- **OHLC/PRICE PROXY** — an approximation of an order-flow quantity built from
  bar geometry, price path, or polled snapshot counters.
- **PLAIN PRICE / STRUCTURE** — not order flow at all; a price or structure
  indicator (listed because it sits in the order-flow modules).
- **EXACT, NON-ORDER-FLOW** — mathematically exact from bars, but a different
  object than order flow.

### 1a. ORDERFLOW_ENGINE v1 (`app/research_engines/orderflow/`) — FROZEN

| feature | module | class | would need |
|---|---|---|---|
| `flow_proxy` (candle pressure: body dir + wick defence + close loc) | `orderflow._pressure` | **OHLC/PRICE PROXY** | tick trades + aggressor side |
| `cvd_proxy` (session cumulative `flow_proxy`) | `orderflow.build` | **OHLC/PRICE PROXY** | tick trades + aggressor (→ true delta → true CVD) |
| `cvd_slope` (OLS slope of `cvd_proxy`) | `orderflow._ols_slope` | **OHLC/PRICE PROXY** | as above |
| `flow_vs_price` (proxy divergence on pivots) | `orderflow.build` | **OHLC/PRICE PROXY** | true CVD on pivots |
| `structure_state` (HH/HL/LH/LL, BOS, CHoCH) | `orderflow.build` | **PLAIN PRICE / STRUCTURE** | nothing (price only) |
| `trend` / `regime_trend` / `regime_vol` / `eff_ratio` | `orderflow.build` | **PLAIN PRICE / STRUCTURE** | nothing (ADX/ATR%/ER) |
| `vwap` (session cumulative HLC3 mean) | `indicators.session_vwap_proxy` | **OHLC/PRICE PROXY** | volume-at-price (true VWAP = Σpv/Σv) |
| `vwap_dev_atr` | `orderflow.build` | **OHLC/PRICE PROXY** | volume (via true VWAP) |
| `value_area` (POC / VAH / VAL from the **TPO** count) | `orderflow._tpo_profile` | **EXACT, NON-ORDER-FLOW** | nothing for TPO; a *volume* value area needs volume-at-price |
| `reclaim` (pierce of a pivot then reclaim; Stage-7 knee) | `orderflow.build` | **PLAIN PRICE / STRUCTURE** (labelled "structural sweep/exhaustion proxy") | tick volume + DOM for a *true* sweep |
| `adx` `rsi` `atr` `ema_fast/slow/trend` | `indicators` | **PLAIN PRICE / STRUCTURE** | nothing |
| RK score, entry-timing, entry-quality, exit | `signal` / `entry_timing` / `exit_engine` | consumers of the above — **no new order-flow content** | — |

**v1 contains ZERO true-order-flow features.** Every "flow" field is an
OHLC/price proxy; the rest is price structure / regime. This is stated in the
engine header and every `method` tag — nothing is called delta / CVD /
footprint.

### 1b. Legacy order-flow modules (`app/orderflow/`, scripts, order_pressure)

| feature / artefact | where | class | would need |
|---|---|---|---|
| H1/H7 structural state — abnormal spike (range pctl ≥ 0.90), broken level, reclaim-distance boundary (knee ≈ 0.60), H1_CONT gate | `app/orderflow/h1h7_state.py` (Stage 3–8 research) | **PLAIN PRICE / STRUCTURE** | — (this is the *validated* part; it never claimed to be order flow) |
| TPO / Market-Profile counts | `app/orderflow/profile.py` | **EXACT, NON-ORDER-FLOW** | — |
| Volume profile (POC/VAH/VAL) | `app/orderflow/profile.py` — `method: "OHLCV_RANGE_DISTRIBUTION"` | **OHLC/PRICE PROXY** | volume-at-price (real trade sizes per price) |
| "buyer / seller / net pressure" 0–100 | `app/research_engines/order_pressure/formulas.py` | **OHLC/PRICE PROXY** | tick trades + aggressor side |
| ENH volume weighting of pressure (options only) | `order_pressure/formulas._vol_weight` | **OHLC/PRICE PROXY** (bar volume, no side) | per-trade side |
| smart-money / Kaggle "orderflow" features | `app/orderflow/smart_money.py`, `scripts/orderflow_smartmoney_kaggle_nifty.py` | **OHLC/PRICE PROXY** | tick + aggressor |
| option-premium walk / H1H7 on premium | `app/orderflow/premium_walk.py`, `ORDERFLOW_H1H7_PERFORMANCE*.md` | **OHLC/PRICE PROXY** of *premium* flow (convexity-distorted) — **not underlying order flow** | option tape with aggressor |
| L2 imbalance probe (passive book) | `scripts/orderflow_l2_imbalance_probe.py` — **REJECTED at feasibility** | book snapshot (real but 3 sessions) | book **event stream** |
| `absorption`, `momentum_acceleration` | `app/hcs/evidence.py` | **explicitly `UNOBSERVABLE` — never faked** | tick volume + DOM |

---

## PART 2 — WHICH FEATURES REQUIRE WHICH REAL DATA

| order-flow feature | tick trades | bid/ask (L1) | aggressor side | market depth / DOM | volume-at-price |
|---|:--:|:--:|:--:|:--:|:--:|
| buy_volume / sell_volume | ✅ | ✅ (to classify) | ✅ | — | — |
| **delta** (buy−sell) | ✅ | ✅ | ✅ | — | — |
| **CVD** (cumulative delta) | ✅ | ✅ | ✅ | — | — |
| delta strength / % / acceleration | ✅ | ✅ | ✅ | — | — |
| **footprint** (bid×ask per price) | ✅ | ✅ | ✅ | — | ✅ (per-price) |
| diagonal / stacked **imbalance** | ✅ | ✅ | ✅ | — | ✅ |
| **absorption** (aggr. vol + no progress) | ✅ | ✅ | ✅ | ✅ (resting size absorbing) | ✅ |
| **exhaustion** (extreme delta + fail) | ✅ | ✅ | ✅ | ~ | ✅ |
| **CVD/delta divergence** | ✅ | ✅ | ✅ | — | — |
| book / liquidity **imbalance** | — | ✅ | — | ✅ | — |
| **liquidity withdrawal / iceberg** | ✅ | ✅ | — | ✅ **event stream** (add/cancel/modify) | — |
| **sweep** (through N levels) | ✅ (burst) | ✅ | ✅ | ✅ event stream | — |
| **volume profile / true VWAP / value area** | ✅ | — | — | — | ✅ |
| TPO / market profile | — | — | — | — | — (bar H/L + time only) |
| market structure (HH/HL/BOS/CHoCH), regime (ADX/ATR), reclaim distance | — | — | — | — | — |

Everything above the "volume profile" row **requires true order flow** and is
therefore **impossible today**. The last two rows are what v1 legitimately does.

---

## PART 3 — ANGEL ONE DATA REALITY

Grounded in the repo's connectors + `ORDERFLOW_STAGE9_L2_RESEARCH.md` Phase A +
a fresh 2026-09-09 DB audit.

### A. What real data we CAN obtain

| data | source | how |
|---|---|---|
| **1-minute OHLCV** (years of history) | `historical/v1/getCandleData` (REST) | already captured; volume present for **FUTURES/options**, `0`/NULL for cash INDEX |
| **LTP stream** (live) | WebSocket `smart-stream` **mode 1** | already used by the scalp runner; not persisted |
| **SnapQuote stream** (live, mode 3): LTP, **last traded qty (size only)**, avg price, cumulative day volume, OI, **best-5 bid/ask (price/qty/#orders)**, day-cumulative `tot_buy_qty`/`tot_sell_qty` (pending limit qty), circuit limits, 52-wk | WebSocket `smart-stream` **mode 3** | parser `app/l2capture/snapquote.py` + worker `app/l2capture/worker.py` **exist and are enabled (`.env: L2_CAPTURE_ENABLED=1`)** — but `snapquote_ticks` currently has **0 rows** (broker auth failing this week; enabled only recently). Mechanism ready; data not yet accruing. |
| **FULL-quote snapshot** (polled ~20–30 s): same fields as SnapQuote | `market/v1/quote` mode FULL (REST) → `quote_snapshots` | already captured — **FUTURES: NIFTY 7 sessions, MCX 6, BANKNIFTY 4** (2026-09-01…09); median gap 31 s, **max gap ~75 min**; `depth_json` on ~88 % of rows; **cash INDEX has no book (0 bid rows)** |
| Option greeks per strike (δ γ θ ν IV) | `marketData/v1/optionGreek` (REST) | captured; **risk greeks, not trade flow** |

### B. What CANNOT be obtained (from Angel One, at any tier we have)

| data | why |
|---|---|
| **Per-trade time-and-sales** (every print: price, size, ts) | Angel exposes no trade-tick endpoint or stream. Mode-3 SnapQuote gives only the **last** print's size at the packet instant; prints between packets are lost. |
| **Aggressor / trade-direction flag** | Never provided by Angel in any mode. Can only be **inferred** (Lee–Ready: LTP vs the best-5 book in the same packet) and only at snapshot resolution. |
| **Order-book event stream** (add / cancel / modify) | Only periodic **snapshots** of the book. No iceberg detection, no liquidity-withdrawal timing, no true sweep. |
| **Depth beyond 5 levels** | SmartAPI returns **best-5** only. Full 20-level NSE market-by-price is a separate paid exchange feed we do not have. |
| **Historical bid/ask, depth, or tick — any of it** | `getCandleData` returns OHLCV only. No historical L1/L2/tick endpoint exists. Every book row we own was captured live by our own poller since 2026-09-01. |
| **Cash-index volume / book** | An index is not traded; NIFTY/BANKNIFTY spot have `volume = 0` and no order book. Order flow on the index is only ever **indirect** (via its future or option chain). |

### C. Exact data resolution (what we could actually get, going forward)

| stream | timestamp | update cadence (liquid future) | book | volume field |
|---|---|---|---|---|
| WS mode 1 (LTP) | exch ms | on every LTP change (multiple/sec) | none | none |
| **WS mode 3 (SnapQuote)** | exch ms | **on every exchange update — typically 1–4 packets/s for NIFTY/BANKNIFTY front-month future; slower for MCX** | **best-5** | cumulative day volume (Δ between packets ≈ interval volume) + last-trade size |
| REST FULL-quote poll | exch ms, truncated to 1 s | our poll interval **~20–30 s**, with multi-minute gaps | best-5 | cumulative day volume |

**Effective order-flow resolution with mode-3 capture ≈ 0.25–1 s per observation
on index futures.** That is *snapshot-sampled*, **not** a true tape: within one
packet interval many trades and many book changes are collapsed into one
before/after picture. Aliasing error is real and unavoidable.

### D. Historical availability

| data | history we hold | usable for TRAIN/VAL/OOS? |
|---|---|---|
| 1m OHLCV, cash index (Kaggle) | NIFTY 2008–2025, BANKNIFTY 2015–2026 | yes for price/structure/regime; **no volume** |
| 1m OHLCV, futures (`market_candles`) | ~2026-09-01 … 09 (≈ 7 sessions) | **no** — days, not months |
| L1/L2 snapshots, futures (`quote_snapshots`) | ~6–7 sessions, one week, one regime | **no** — feasibility probes only (already done, rejected) |
| SnapQuote mode-3 ticks (`l2_capture.db`) | **0 rows** | **no — capture not yet accruing** |
| Option chain snapshots (bid/ask/greeks) | ~14 clustered sessions 2023-02 … 2026-09 | premium-flow feasibility only; **not** underlying flow |

**There is no historical true-order-flow dataset. Building one requires forward
capture** of mode-3 SnapQuote on the chosen instrument for enough independent
sessions to split TRAIN / VALIDATION / OOS without look-ahead — realistically
**40–60 sessions ≈ 3 months** of reliable capture, ideally spanning ≥ 2 vol
regimes. **No fabrication, no simulation, no approximation of the missing
fields.**

### E. Exact order-flow calculations that BECOME possible with mode-3 capture

All *snapshot-sampled* on a **future** (never the cash index), all first labelled
`*_snap` with the capture cadence attached:

1. **Signed volume (per interval):** `Δvolume_cum` since the last packet, split
   by Lee–Ready on `LTP` vs the packet's own best-5 (`LTP ≥ ask → buy`,
   `LTP ≤ bid → sell`, inside → midpoint, tie → tick rule on `LTP`). Error =
   the whole interval attributed to one side.
2. **Delta_snap = buy_vol_snap − sell_vol_snap**; **CVD_snap** = its session
   cumulative. Slope / acceleration as in v1 but on real (sampled) delta.
3. **Book imbalance:** `(Σ bid_qty_5 − Σ ask_qty_5) / (Σ bid_qty_5 + Σ ask_qty_5)`
   at each packet; and its change. (Feasibility-rejected once on 3 sessions —
   re-test properly with real thresholds on the fitted sample.)
4. **Book pull / refill:** level-5 aggregate size drop ≥ x % between consecutive
   packets near the touch price — a *coarse* liquidity-withdrawal proxy (misses
   sub-interval pulls).
5. **Volume-at-price_snap:** bucket `Δvolume_cum` at the packet's `LTP` →
   session **volume profile / POC / VAH-VAL** and a **true VWAP** — all
   snapshot-sampled but volume-real.
6. **CVD / delta divergence** on confirmed pivots, using `CVD_snap`.
7. **Absorption_snap:** `Δvolume_cum` percentile-high **and** `|ΔLTP| ≤ α·ATR`
   over W packets **and** book imbalance opposing the price → the first
   *evidence-backed* absorption flag we could ever compute (still weakened by
   sampling and no per-print sequence).

**Not** possible even with mode-3: true footprint (needs per-price aggressor
volume, not one LTP/packet), diagonal stacked imbalance, iceberg detection,
true sweep sequencing, 20-level DOM.

### F. Data limitations (must appear in the v2 engine header)

1. **Snapshot, not tape.** 0.25–1 s sampling collapses many trades/book events.
2. **Aggressor is inferred, not given** (~85–92 % accurate on liquid futures
   with L1 at trade time; worse here because the L1 is the *packet's* book, not
   the book at each intervening trade).
3. **Best-5 only.** No view beyond 5 levels; easy to spoof, so book-imbalance
   predictive value is inherently limited.
4. **Cumulative volume can gap/reset** on feed hiccups → interval volume must be
   sanity-checked (`Δ ≥ 0`, `< k·median`), bad intervals dropped.
5. **Future ≠ index.** Basis, roll, and expiry effects; a signal validated on
   the future is not automatically valid for index-option execution.
6. **Forward-only history** → early v2 will have few sessions and one regime;
   verdict stays NOT VALIDATED until the OOS window is real and multi-regime.
7. **Capture reliability is a prerequisite,** not a given — the worker exists but
   has 0 rows; broker auth, leader lease, and market-hours gating must all hold
   for weeks.

### G. Recommended instrument for testing

**NIFTY front-month FUTURE (NSE).** Rationale: deepest liquidity of any Indian
index derivative → tightest spread, most mode-3 packets/second, cleanest Lee–Ready
inference; real volume; the entity NIFTY options are priced off, so an edge maps
to CE/PE execution. **Secondary:** BANKNIFTY future (more volatile, fewer strikes
now weekly). **Also capture** MCX **CRUDEOIL** + **NATURALGAS** futures (already
in the `l2capture` symbol set, different session + regime → regime diversity for
OOS). **Never** the cash index (no book, no volume).

---

## PART 4 — WHAT STAYS FROZEN / PRESERVED / NEXT

**Frozen (research baseline, unchanged):** `ORDERFLOW_ENGINE v1`
(`app/research_engines/orderflow/`). Verdict NO-GO. **Not added to the RK
production score.** Not optimized for win rate.

**Preserved and reused as-is for v2** (these validated fine — they are not the
part that failed):
- `SIGNAL ≠ ENTRY` two-stage state machine (`entry_timing.run`)
- No-chase filter (extension vs signal origin / VWAP / baseline / fast EMA)
- RR gate (reject RR-to-T1 < min)
- Entry Quality Score (0–100, capability-aware, non-double-counted)
- TRAIN → VALIDATION → OOS split (by session block) + the VALIDATION gate
- Walk-forward (expanding fold) harness
- The learning-dataset (`.jsonl`) format for continuous re-calibration
- The capability-flag / method-tag honesty convention

**Next (spec only, no code):** `ORDERFLOW_ENGINE_V2_SPEC.md` — builds §2–§7 order
flow on **snapshot-sampled real data** from forward-captured mode-3 SnapQuote on
the **NIFTY future**, feeding the *same* preserved two-stage entry + calibration
framework. v2 cannot be backtested until ≥ ~40–60 captured sessions exist; until
then it is a capture plan + a math spec, not a result.

**Constraints carried forward:** no 100 %-win-rate claims; never optimize for win
rate alone; OOS is never used for calibration; the goal is a statistically
defensible edge, not a backtest number.
