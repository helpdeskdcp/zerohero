# ORDERFLOW_ENGINE v2 — SPECIFICATION (pre-code)

_2026-09-09. Spec only. No v2 code is written from this document, and none can be
usefully written yet: v2 requires a forward-captured real order-flow dataset that
does not exist today (`l2_capture.db` has 0 rows). This spec is therefore two
things — a **capture plan** and a **math + architecture spec** — gated on
milestones (Part 10)._

**Read first:** `ORDERFLOW_ENGINE_SPEC.md` (the general professional order-flow
reference) and `ORDERFLOW_DATA_REALITY_AUDIT.md` (what data Angel One can and
cannot give us). v2 does not restate those; it builds on them.

**Carried-forward constraints (non-negotiable):** no 100 %-win-rate claims;
never optimize for win rate alone; OOS is never used for calibration; no
fabrication or simulation of missing fields; no live broker wiring until an OOS +
walk-forward verdict is GO.

---

## 0. WHAT CHANGES FROM v1, AND WHAT DOES NOT

| layer | v1 | v2 |
|---|---|---|
| **instrument** | NIFTY **cash index** | NIFTY front-month **FUTURE** (+ BANKNIFTY / MCX for regime diversity) |
| **data** | 1m OHLCV, volume = 0 | forward-captured **mode-3 SnapQuote** (best-5 book, LTP, last-trade size, cumulative volume, OI) at ~0.25–1 s |
| **flow features** | `flow_proxy` = candle geometry (an OHLC PROXY) | **snapshot-sampled REAL order flow**: signed volume, delta, CVD, volume-at-price, book imbalance — all `*_snap`, all method-tagged |
| RK score shape | additive, capability-aware, 0–100 | **same shape**, new inputs, real (not proxy) status for the flow categories |
| **SIGNAL ≠ ENTRY** two-stage entry | preserved | **reused verbatim** (`entry_timing.run`) |
| no-chase filter / RR gate / Entry-Quality score | preserved | **reused verbatim** |
| TRAIN → VALIDATION → OOS + VALIDATION gate | preserved | **reused verbatim** + a snapshot-robustness test (Part 8.4) |
| walk-forward harness / learning-dataset `.jsonl` | preserved | **reused verbatim** |
| exit engine | preserved | reused; costs re-parameterised for a future + separate option leg |
| option execution | advisory `option_map.py` | still a **separate downstream layer**; premium noise never feeds the index/future signal |

**v1 stays frozen.** v2 is a new engine (`app/research_engines/orderflow_v2/`)
that imports v1's preserved entry/calibration modules and replaces only the
feature layer.

---

## PART 1 — CAPTURE PIPELINE (the prerequisite)

### 1.1 What to capture

Mode-3 SnapQuote, via the existing `app/l2capture/` worker
(`L2_CAPTURE_ENABLED=1`, already set) → `data/l2_capture.db` →
`snapquote_ticks` (schema already defined in `app/l2capture/store.py`; no schema
change needed for v2 — all derived quantities are compute-time).

Per packet we already persist: `exch_ts_ms`, `seq`, `token`, `ltp`, `ltq`,
`atp`, `volume` (cumulative day), `tot_buy_qty`, `tot_sell_qty`, `oi`,
`oi_change_pct`, `bid`, `ask`, `bid_qty`, `ask_qty`, `depth_json` (5-level
buy/sell price/qty/orders), circuit + 52-wk, `snap_key` (dedup on
`token:seq:exch_ts_ms`).

### 1.2 Instruments (capture set)

| priority | instrument | why |
|---|---|---|
| **P0** | NIFTY front-month FUTURE (NSE) | deepest book → most packets/s → cleanest Lee–Ready; NIFTY options price off it |
| P1 | BANKNIFTY front-month FUTURE | more volatile; second regime |
| P1 | MCX **CRUDEOIL** + **NATURALGAS** FUTURE | different session (evening) + macro regime → OOS diversity (already in the `l2capture` symbol set) |
| — | cash INDEX | **never** — no book, no volume |

Roll rule: switch each symbol's captured token to the next expiry on the
Monday of expiry week (avoid thin, gappy front-month data on expiry day).

### 1.3 Capture-quality requirements before ANY modeling

| gate | threshold |
|---|---|
| packets/session (P0 instrument) | ≥ 8 000 (≈ ≥ 0.5 Hz effective over 375 min) |
| session coverage | ≥ 95 % of 09:15–15:30 with gaps < 60 s |
| clean 5-level book rows | ≥ 90 % pass the top-of-book sanity test (`bid < ask`, monotone ladders) |
| cumulative-volume monotonic | ≥ 99 % of consecutive packets have `Δvolume ≥ 0` |
| **independent sessions** | **≥ 60**, spanning **≥ 2 volatility regimes** (an ATR-percentile split) and **≥ 1 expiry cycle boundary** |

Until all gates pass, v2 is spec-only. **Do not model on < 60 sessions. Do not
concatenate across a data-gap.**

---

## PART 2 — DATA CONTRACT (per instrument, per session)

Input to v2 = the ordered packet stream for one contiguous session:
`P = [p_0, p_1, …, p_n]`, sorted by `exch_ts_ms` then `seq`, deduped.

Derived, compute-time only:

| symbol | definition |
|---|---|
| `dt_k` | `(exch_ts_ms[k] − exch_ts_ms[k−1]) / 1000` — inter-packet seconds |
| `dV_k` | `volume[k] − volume[k−1]` — interval executed volume (contracts). If `< 0` or `> κ · median(dV)` → interval flagged BAD, excluded from flow sums (κ ≈ 20) |
| `mid_k` | `(bid[k] + ask[k]) / 2` |
| `spread_k` | `ask[k] − bid[k]` |
| `Bq_k`, `Aq_k` | `Σ` of the 5 bid / 5 ask `quantity` in `depth_json[k]` |

**Capability flags (v2):** `has_trades=false` (still no per-trade tape),
`has_aggressor=false` (inferred, not given), `has_L1=true`, `has_L2_snapshots=true`,
`has_L2_stream=false`, `volume_available=true`, `sampling="SNAPSHOT"`,
`cadence_sec_median`, `n_sessions`, `regimes_covered`.

> v2 header MUST read: *"Order flow is SNAPSHOT-SAMPLED from mode-3 SnapQuote.
> Aggressor side is INFERRED (Lee–Ready on the packet book), not exchange-given.
> Sub-interval trades and book events are collapsed. Not a true tape."*

---

## PART 3 — ORDER-FLOW PRIMITIVES: EXACT MATH (snapshot-sampled)

All quantities suffixed `_snap`. All causal: value at `k` uses only packets
`≤ k`. All deterministic.

### 3.1 Interval aggressor inference (Lee–Ready on the packet book)

For interval `k` (from packet `k−1` to `k`), classify the whole `dV_k` to one
side using `LTP_k = ltp[k]` against the book of packet `k` (best available; the
book at each intervening trade is unknown):

1. `LTP_k ≥ ask[k]`  → **BUY**
2. `LTP_k ≤ bid[k]`  → **SELL**
3. `bid[k] < LTP_k < ask[k]`:
   - `LTP_k > mid_k` → **BUY**
   - `LTP_k < mid_k` → **SELL**
   - `LTP_k == mid_k` → rule 4
4. Tick rule on LTP: `LTP_k > LTP_{k−1}` → BUY; `< ` → SELL; `==` → inherit the
   last non-zero LTP tick direction.
5. No prior tick / first packet / `dV_k == 0` / interval BAD → **UNKNOWN**
   (counted in `unknown_vol_snap`, excluded from delta).

**Accuracy note:** ~85–92 % vs true side on liquid futures *when L1 is measured
at the trade*; here it is the packet's L1, so expect worse, especially in fast
one-way tape. This is a known, bounded error — never presented as exact.

### 3.2 Signed volume

```
buy_vol_snap_k   = dV_k         if side_k = BUY   else 0
sell_vol_snap_k  = dV_k         if side_k = SELL  else 0
unknown_vol_snap_k = dV_k       if side_k = UNKNOWN else 0
```

Aggregate to the working bar (default **30 s** and **1 min** bars built from
packets; the working timeframe is a calibrated choice, not fixed):
`buy_vol_bar = Σ buy_vol_snap` over the bar's packets, likewise sell/unknown.

### 3.3 Delta

```
delta_snap_bar   = buy_vol_bar − sell_vol_bar
delta_pct_bar    = delta_snap_bar / max(1, buy_vol_bar + sell_vol_bar)          ∈ [−1, 1]
delta_accel_bar  = delta_snap_bar − delta_snap_{bar−1}                          (1st diff)
                 = delta_snap_bar − 2·delta_snap_{bar−1} + delta_snap_{bar−2}   (2nd diff, alt)
```

Intrabar delta excursion (min/max running delta within a bar) is **not**
available — packet count per bar is small and prints are collapsed. Report
`delta_excursion: UNOBSERVABLE`.

### 3.4 Cumulative delta (CVD)

```
CVD_snap_k = CVD_snap_{k−1} + (buy_vol_snap_k − sell_vol_snap_k)     ; CVD_snap = 0 at session open
```

Maintain **session CVD** (resets daily) and **running CVD** (never resets)
separately.

| derived | formula |
|---|---|
| CVD slope | OLS slope of `CVD_snap` over the last `L` bars (`L` calibrated) |
| CVD acceleration | 1st diff of the slope, or 2nd diff of `CVD_snap` |
| CVD trend state | `UP` if slope > +ε and `m`/`m` recent bars rising; `DOWN` mirror; else `FLAT`. `ε` from a volatility-scaled floor (`ε = c · σ(ΔCVD)`) |

### 3.5 Volume-at-price → profile, POC, value area, true(sampled) VWAP

Bucket each interval's `dV_k` at price `LTP_k` on a fixed tick grid
(`tick_grid` = 1–2 index points for NIFTY future):

```
vap_snap[p]  = Σ dV_k   over intervals with round(LTP_k / grid)·grid = p
POC_snap     = argmax_p vap_snap[p]
Value Area   = grow outward from POC_snap, at each step adding the heavier
               adjacent bin, until Σ ≥ 0.70 · Σ_p vap_snap[p]  → VAH_snap / VAL_snap
VWAP_snap_k  = ( Σ_{j≤k} LTP_j · dV_j ) / ( Σ_{j≤k} dV_j )       ; session-anchored
```

This is a **real** volume VWAP / profile, snapshot-sampled (each interval's
volume is pinned to one `LTP`). It replaces v1's `PRICE_PROXY_EQUAL_WEIGHT`
VWAP. `method: "SNAPSHOT_VOLUME"`.

### 3.6 Book imbalance (best-5)

```
book_imb_k        = (Bq_k − Aq_k) / (Bq_k + Aq_k)                 ∈ [−1, 1]
book_imb_slope    = OLS slope over last L packets
depth_ratio_k     = Bq_k / max(1, Aq_k)                            (report both orientations)
```

Threshold selection is statistical (Part 5.3 of `ORDERFLOW_ENGINE_SPEC.md`):
build the empirical distribution of `depth_ratio` on TRAIN, test candidate
cutoffs against forward return, keep only what survives OOS + walk-forward.
**Prior:** a 3-session probe of passive-book imbalance was feasibility-rejected;
re-test properly, but expect low predictive value (best-5 is spoofable).

### 3.7 Book pull / refill (coarse liquidity-withdrawal proxy)

Near a reference price `L` (a pivot / round number), between consecutive packets:

```
pull_k  =  ( Aq_{k−1} − Aq_k ) / max(1, Aq_{k−1})   ≥ x     with |ask[k] − L| ≤ δ   → ask-side pull  (bullish break fuel)
           ( Bq_{k−1} − Bq_k ) / max(1, Bq_{k−1})   ≥ x     with |bid[k] − L| ≤ δ   → bid-side pull  (bearish)
```

`x` ≈ 0.4. Misses any pull-and-replace that completes inside one interval.
`method: "SNAPSHOT_BOOK_DELTA"` — a proxy, not event-stream truth.

### 3.8 CVD / delta divergence (deterministic, pivot-based)

Confirmed swing pivots via the fractal rule (half-width `w`, known only at
`i+w`). Two consecutive pivot lows `(iA, priceA, CVD_A)`, `(iB, priceB, CVD_B)`:

```
BULLISH divergence :  priceB < priceA − d_min·ATR   AND   CVD_B > CVD_A + c_min·σ(ΔCVD)
```

Two consecutive pivot highs:

```
BEARISH divergence :  priceB > priceA + d_min·ATR   AND   CVD_B < CVD_A − c_min·σ(ΔCVD)
```

Same construction with `delta_snap_bar` at the pivot bar instead of CVD.

### 3.9 Absorption (first evidence-backed version)

Over a window `W` of consecutive bars at a price band `[p−δ, p+δ]`, ALL of:

1. `Σ (buy_vol_bar + sell_vol_bar)` over `W` ≥ P90 of a trailing reference.
2. `|C_end(W) − C_start(W)| ≤ α·ATR` (`α ≈ 0.25`) AND `range(W) ≤ β·ATR` (`β ≈ 0.6`).
3. `|Σ delta_snap_bar| / Σ total_vol` over `W` ≥ γ (`γ ≈ 0.35`) — the crowd *is* pushing.
4. `≥ κ` bars in `W` whose `[L,H]` intersect the band (repeated interaction).
5. **Direction** = opposite of `sign(Σ delta_snap_bar)` (sellers absorbed → bullish).
6. Optional confirm: next 1–2 bars close away from the band in the absorption
   direction with `delta_snap` flipping sign.

`method: "SNAPSHOT_ABSORPTION"` — weakened by sampling and by the absence of a
per-print sequence; **not** presented as certain.

### 3.10 Exhaustion

Weighted score (not all mandatory) at a swing extreme: `|delta_snap_bar|` ≥ P95
trailing; bar `dV` ≥ P90 trailing; `|C − VWAP_snap| ≥ θ·ATR` (`θ ≈ 2.5`); next
1–2 bars fail to extend; a close back through the prior-bar 50 % / the broken
level with `delta_snap` flipping. Flag `buying_exhaustion` / `selling_exhaustion`
above a TRAIN-fitted threshold.

### 3.11 Still IMPOSSIBLE even with mode-3 capture

| not buildable | why |
|---|---|
| true **footprint** (bid×ask volume per price) | needs per-price aggressor volume; we have one `LTP` per interval, not every print |
| **stacked diagonal imbalance** | needs the footprint |
| **iceberg** detection | needs trades vs displayed size at high frequency |
| **true sweep** sequencing (through N levels in a burst) | needs the trade burst + the book event stream |
| **20-level DOM** / full market-by-price | SmartAPI returns best-5 only |
| intrabar delta path / delta low-high | packets per bar too few; prints collapsed |

For these, v2 returns `UNOBSERVABLE` (never a proxy dressed as the real thing).

---

## PART 4 — DATA-QUALITY SCORE (snapshot-specific)

Pre-flight, per session, before any feature:

| check | rule | action |
|---|---|---|
| gap | `dt_k > 60 s` | do not span the gap for CVD / structure; mark the window `DEGRADED` |
| cadence | `median(dt) > 2 s` for a P0 instrument | session `DEGRADED` (too coarse for flow) |
| volume monotonic | `dV_k < 0` | that interval BAD; if > 1 % of intervals → session `BAD` |
| volume spike | `dV_k > κ·median(dV)` | interval BAD (feed glitch / block print) |
| crossed / locked book | `bid[k] ≥ ask[k]` | drop that packet's book fields |
| stale packet | `now − exch_ts_ms > s_max` (live only) | `feed_age` penalty |
| session boundary | packet stamped outside 09:15–15:30 IST | exclude from session aggregates |
| roll artefact | first/last session of a captured token | exclude or down-weight |

```
DQS = 100 · Π_j (1 − penalty_j)      # multiplicative
```

Emit the capability flag set (Part 2) alongside `DQS`. A session with
`DQS < q_min` or `n_good_intervals < n_min` is **excluded from TRAIN/VAL/OOS**.

---

## PART 5 — ORDER-FLOW QUALITY SCORE v2 (OFQ, 0–100)

Same additive, capability-aware, no-double-counting model as v1's RK score;
now the flow categories have status **`SNAP`** (real, sampled) instead of
`PROXY`. `UNOBSERVABLE` categories contribute 0 and redistribute weight;
`evidence_coverage = Σ w_observable / 100` is exposed.

| category | input (ONE per row) | proposed w | v2 status |
|---|---|---|---|
| Flow direction & strength | `delta_pct_bar` | 22 | **SNAP** |
| Flow persistence | `CVD_snap slope` | 12 | **SNAP** |
| Flow vs price | §3.8 divergence on chosen flow series | 12 | **SNAP** |
| Absorption / exhaustion at level | §3.9 / §3.10 score | 10 | **SNAP** (weak) |
| Structure alignment | BOS / CHoCH / retest (reused from v1) | 15 | OK |
| Regime fit | ADX / ATR% / efficiency (reused) | 10 | OK |
| VWAP context | `C − VWAP_snap` (real volume VWAP now) | 8 | **SNAP** |
| Value-area context | POC / VAH / VAL from `vap_snap` | 6 | **SNAP** |
| Liquidity / book | `book_imb` + `pull` (best-5) | 5 | **SNAP** (low ceiling — spoofable) |

```
raw   = Σ_c w_c · s_c · 1[status_c ≠ UNOBSERVABLE]
avail = Σ_c w_c       · 1[status_c ≠ UNOBSERVABLE]
OFQ   = 100 · raw / max(avail, ε)
```

Hard vetoes (regardless of OFQ): `regime == CHOP`; `DQS < q_min`;
`evidence_coverage < 0.6`; structure conflicts with flow direction;
`spread > s_veto · median(spread)` (illiquid moment).

`OFQ` is not a probability. Map `OFQ → P(win)` per `regime × signal_family` with
the repo's closed-form logistic + isotonic recalibration, **fit on TRAIN
resolved trades only**, `NOT VALIDATED` until it beats base-rate OOS.

---

## PART 6 — SIGNAL FAMILIES FOR v2

| # | family | v1 (index) | **v2 (future, snapshot flow)** |
|---|---|---|---|
| A | Delta continuation | proxy only | **buildable** — trend regime + `delta_pct` & `CVD_snap` slope aligned, pullback holds |
| B | Delta reversal | proxy only | **buildable** — `delta_snap` extreme against price at a level + flip |
| C | CVD divergence | proxy only | **buildable** — §3.8 on `CVD_snap` |
| D | Absorption reversal | structural stall only | **buildable (weak)** — §3.9 + confirm |
| E | Imbalance breakout | rejected | **partial** — `book_imb` shelf + `pull`; low ceiling |
| F | Liquidity-sweep reversal | reclaim-distance proxy (validated) | **reclaim proxy + `pull` + `delta_snap`** confirm |
| G | Order flow + VWAP | proxy VWAP | **real** `VWAP_snap` + `delta_snap` |
| H | Order flow + market structure | structure real, flow proxy | **structure real + flow SNAP** |
| I | Order flow + S/R (+ OI) | S/R real, flow proxy | **S/R + OI real + flow SNAP** |
| J | Multi-confirmation | limited | ≥ k of {A…I} agree — as far as `evidence_coverage` allows |

Each family is detected independently, regime-gated, level-gated. Combining
(family J, and later the RK multi-strategy vote) happens only after each family
independently clears OOS.

---

## PART 7 — ENGINE ARCHITECTURE

```
app/research_engines/orderflow_v2/          # NEW engine; v1 stays frozen
  __init__.py         SAFETY banner + the snapshot-sampling disclosure
  capture_reader.py   RO loader of l2_capture.db snapquote_ticks -> per-session
                      packet streams + capability flags + DQS (Part 4)
  intervals.py        §3.1 aggressor inference + §3.2 signed volume + BAD-interval gate
  flow.py             §3.3-3.10 delta / CVD / vap / VWAP_snap / book_imb / pull /
                      divergence / absorption / exhaustion  (all *_snap, method-tagged)
  ofq.py              §5 Order-Flow Quality score, capability-aware renorm
  families.py         §6 A-J detectors, independent, regime + level gated

  # --- REUSED VERBATIM from v1 (imported, not copied) ---
  from app.research_engines.orderflow import signal        as _v1_rk       # RK-score shape
  from app.research_engines.orderflow import entry_timing  as _v1_entry    # SIGNAL != ENTRY two-stage
  from app.research_engines.orderflow import exit_engine   as _v1_exit
  #   -> no-chase filter, RR gate, Entry-Quality score, leg-based retracement
  #      zones, momentum-recovery, all preserved

  calibrate.py        TRAIN -> VALIDATION -> OOS zone calibration (v1 logic) +
                      Part 8.4 sampling-robustness test
  backtest.py         walk-forward harness (v1) ; costs re-parameterised (Part 8.3) ;
                      writes report .md/.json + learning_v2_*.jsonl
  option_map.py       reuse v1's advisory layer (future signal -> CE/PE); the
                      future basis is added as an explicit offset, still advisory
  tests/              causality, determinism, no-live-import, no-fabrication,
                      aggressor-rule table, DQS honesty, capability flags,
                      sampling-robustness reproducibility
```

**Data flow:** `capture_reader → intervals → flow → ofq → families → _v1_rk
(direction + strength) → _v1_entry (two-stage) → RR gate → _v1_exit → learning
dataset → backtest verdict`. The **only new content is the feature layer**;
everything from RK-score downward is the preserved, already-working v1 code.

**Separation rule (unchanged):** decide `FUTURE_DIRECTION` + `OPTIMAL_FUTURE_ENTRY`
first; `option_map` then maps to CE/PE + strike + advisory premium levels. Option
premium / IV / greeks never feed back into the future signal.

---

## PART 8 — BACKTEST & VALIDATION PROTOCOL

### 8.1 Splits

By **session block**, chronological: TRAIN 55 % / VALIDATION 20 % / OOS 25 %.
**Purge + embargo** one session around each boundary (a trade opened pre-boundary
and closed post- must not leak). Sessions failing the Part-4 quality gate are
dropped *before* splitting.

### 8.2 Walk-forward

Expanding-window folds (≥ 5). Thresholds frozen from each fold's train portion;
the fold's test portion scored once. A family is "walk-forward stable" only if
its per-fold expectancy is positive in **≥ ⌈2·folds/3⌉** folds **and** no single
fold supplies > 50 % of total net.

### 8.3 Cost model

| leg | cost (points, per round trip) | sensitivity |
|---|---|---|
| NIFTY future | spread (`median(spread_k)`) + slippage 1 tick + brokerage/taxes in points | 0× / 1× / 2× |
| option execution (advisory only, reported separately) | separate: option spread + IV-move + 1 tick; **not** netted into the future P&L |

The future backtest P&L is in **future points**. The option translation is
reported alongside for context, never as the primary metric.

### 8.4 Sampling-robustness test (v2-specific)

The edge must not be an artefact of the capture cadence. Re-run the full
backtest on the **same signals** but with the packet stream **decimated**:
keep every 2nd, every 4th, every 8th packet (→ ~0.5–4 s effective cadence).
A GO requires the OOS expectancy and PF to **degrade gracefully** (monotone,
< ~35 % drop from full to /4). A sharp collapse ⇒ the "edge" lives in
micro-timing we cannot reliably capture ⇒ NO-GO.

### 8.5 GO / NO-GO

GO **only if ALL**:
- OOS `n ≥ 60` trades, on the P0 instrument.
- OOS expectancy in R > cost, **and** profit factor ≥ 1.3.
- Positive in **≥ 2 volatility regimes** (ATR-percentile split) and **≥ 2
  quarters**; no single quarter/regime > 50 % of net.
- Walk-forward stable (8.2).
- Survives the sampling-robustness test (8.4).
- Calibration (`OFQ → P(win)`) beats base-rate on OOS (Brier, reliability).

Anything else → **NO-GO / NOT VALIDATED**. Report the full table, every family,
every threshold considered — no cherry-picking the survivor.

---

## PART 9 — NON-GOALS & HONESTY RULES

1. No 100 %-win-rate or "never fails" language, anywhere.
2. Win rate is never the objective; expectancy + PF + robustness + calibration are.
3. OOS data is never used to choose a threshold, a zone, a weight, or a family.
4. Missing fields (per-trade tape, aggressor flag, book event stream, 20-level
   DOM) are **never fabricated, simulated, or approximated under a real name**.
   Proxies keep their `method` tag and their `SNAP` / `PROXY` / `UNOBSERVABLE`
   status.
5. No live broker wiring, no addition to the production RK score, until an
   OOS + walk-forward GO per Part 8.5. v1 remains frozen throughout.
6. If v2 also returns NO-GO, that is an acceptable, publishable result — the
   goal is a defensible edge, not a number.

---

## PART 10 — MILESTONES (gating)

| M | milestone | exit criterion |
|---|---|---|
| **M0** | capture actually accrues | `snapquote_ticks` gains ≥ 8 000 rows/session for P0, ≥ 5 consecutive sessions, quality gates (Part 1.3) green. *(Today: 0 rows — broker auth + leader lease + market-hours gating must hold.)* |
| **M1** | dataset sufficiency | ≥ 60 quality sessions, ≥ 2 vol regimes, ≥ 1 expiry cycle |
| **M2** | primitives sane | `delta_snap` / `CVD_snap` / `VWAP_snap` reconstruct known session facts (e.g. `CVD` end-of-day sign vs the day's return correlation is sensible; `VWAP_snap` within ticks of the exchange VWAP where available); aggressor-rule UNKNOWN fraction < ~15 % |
| **M3** | first OOS verdict | run Part 8 end-to-end; GO or NO-GO per 8.5 |
| **M4** | integration (only if M3 = GO) | combine with SSL Hybrid / SuperTrend / Donchian / VWAP-ORB / EMA-pullback / QQE into the **RK MULTI-STRATEGY ENGINE** as one independently-validated voter — each sub-strategy validated first |

**Code is written starting at M1** (capture_reader + primitives, testable on the
first real sessions), but **no verdict and no integration before M3**.
