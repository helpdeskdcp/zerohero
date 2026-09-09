# ORDER FLOW ENGINE — COMPLETE TECHNICAL SPECIFICATION (pre-code)

**Status: specification only. No strategy code is written from this document.**
Purpose: reproduce genuine institutional-style order-flow mechanics on paper,
map every component onto the data this repo actually has, and mark precisely
what is real vs. approximated vs. impossible.

Nothing in this document is "100% accurate" or "never fails". Order flow is a
*probabilistic edge in context*, not a certainty.

---

## 0. EXECUTIVE VERDICT (read first)

| Layer | Needs | We have | Verdict |
|---|---|---|---|
| **True order flow** (per-trade price+size+**aggressor**) | tick/time-and-sales feed with trade side, or L1 bid/ask at each trade | **nothing** — no trade-tick endpoint was ever captured (`ORDERFLOW_STAGE9_L2_RESEARCH.md` Phase A) | **TRUE ORDER FLOW NOT AVAILABLE** |
| **L2 / depth** | order-book add/cancel/modify event stream | `app/l2capture/` parser exists (Angel mode-3 SnapQuote: best-5 book, LTQ, tot_buy/sell) + `quote_snapshots.depth_json`; but **polled snapshots**, ~20–30 s cadence, **~6 FUTURE sessions / ~14 clustered OPTION sessions**, INDEX book is empty | **INSUFFICIENT + SNAPSHOT-ONLY** — usable for feasibility probes, not a train/OOS split |
| **Candle order-flow approximations** | OHLCV bars | `market_candles` (1–60 m, ~5–8 recent sessions, FUTURE has volume, INDEX volume = 0/NULL); Kaggle 1 m NIFTY/BANKNIFTY multi-year, **volume = 0** | **AVAILABLE but weak** — geometry + profile + VWAP + structure + regime only; **no delta, no CVD, no footprint, no absorption from cash-index bars** |
| **Options premium "flow"** | option bid/ask/LTQ/tot_buy_sell snapshots | `quote_snapshots` OPTION: 775 k rows w/ bid over 14 clustered sessions (2023-02 … 2026-09) | **PARTIAL** — this is *premium* flow (convexity-distorted, see `orderflow-h1h7-options-research`), **not** underlying order flow |

**Consequence:** an `ORDERFLOW_ENGINE v1` on **NIFTY 50 index** can only be a
**candle-approximation engine** (geometry / volume-profile / VWAP / structure /
regime). Delta, CVD, footprint, imbalance, absorption, sweeps, exhaustion —
the components that make order flow "order flow" — **cannot be computed for the
cash index** from any data we hold. They become available *only* on the
**index future**, and only once ≥ 40–60 independent sessions of mode-3
SnapQuote capture exist.

Prior related results, preserved: order-pressure candle engine → **NO-GO**;
H1/H7 option-premium orderflow → **NOT VALIDATED**; L2 imbalance gate →
aggressor form **impossible**, passive-book form **REJECTED at feasibility**.

---

## 1. RAW DATA — what a real order-flow engine ingests

### 1.1 The full requirement (time-and-sales + book)

Per **trade print**:

| field | meaning | why it matters |
|---|---|---|
| `ts` | exchange timestamp, ms or µs | sequencing; without sub-second order you cannot tell which side moved first |
| `price` | trade price | footprint bin, VWAP, structure |
| `qty` | trade size (contracts/shares) | the unit everything sums |
| `aggressor_side` | BUY / SELL / UNKNOWN | **the single most important field** — defines delta |
| `bid`, `ask` at trade time | top of book | aggressor classification when side is not stamped |
| `bid_size`, `ask_size` | top-of-book resting size | absorption, sweep, imbalance |
| `trade_condition` | auction / block / cross / regular | exclude non-continuous prints |

Per **book update** (L2/L3):

| field | meaning |
|---|---|
| level `price`, `size`, `orders` (5–20 levels) | resting liquidity shape |
| event type: `ADD` / `CANCEL` / `MODIFY` / `TRADE` | liquidity *withdrawal* vs *addition* |
| `ts` per event | to align pulls/spoofs with trades |

Per **bar** (derived, not raw): `O,H,L,C,V`, and — if the feed provides it —
`buy_volume`, `sell_volume`, `delta`, `high_delta`, `low_delta`, `poc`.

### 1.2 A) TRUE ORDER FLOW vs B) CANDLE APPROXIMATION

**A) True order flow** = every component below is derived from *individual
trade prints with a known aggressor side* (or reconstructed via bid/ask at
trade time). Delta, CVD, footprint, stacked imbalance, absorption, sweep
detection **require A**.

**B) Candle approximation** = inferring "pressure" from bar geometry
(body/wick/close-location), distributing bar volume across the price range,
using tick-rule on close-to-close, or diffing polled snapshot counters. These
are *proxies*. They correlate weakly with true delta and **break in exactly
the situations order flow is supposed to catch** (absorption, stop runs,
one-tick-range high-volume bars).

> **RULE:** an OHLC-derived quantity is **never** labelled "delta", "CVD",
> "footprint", or "true order flow". It is labelled `*_proxy` with its method
> string (e.g. `method: "OHLCV_RANGE_DISTRIBUTION"`), exactly as
> `app/orderflow/profile.py` already does.

### 1.3 IN OUR SYSTEM

| field | source | reality |
|---|---|---|
| trade price (LTP) | Angel WS mode 1 (live, not persisted) / `quote_snapshots.ltp` (polled ~20–30 s) | **snapshot, not tick** |
| trade qty per print | — | **UNOBSERVABLE** |
| **aggressor side** | — | **UNOBSERVABLE anywhere, ever** |
| bid/ask (L1) | `quote_snapshots.bid/ask` — FUTURE 6 sessions; **INDEX: none** | snapshot, sparse |
| bid/ask size | `quote_snapshots.bid_qty/ask_qty` | same |
| L2 depth (5 lvl) | `quote_snapshots.depth_json`, `app/l2capture/snapquote.py` (mode-3 best-5) | **snapshot only**, no event stream |
| `last_trade_qty` | `quote_snapshots.last_trade_qty` | **size only, no side**; last print at poll instant |
| `tot_buy_qty`,`tot_sell_qty` | `quote_snapshots` | **day-cumulative pending limit qty**, *not* executed aggressor volume |
| bar OHLCV | `market_candles`, Kaggle | FUTURE has `v`; **cash INDEX `v` = 0/NULL** |
| OI / OI-change | `market_candles.oi`, `option_greeks` | positioning proxy, not flow |

---

## 2. AGGRESSOR CLASSIFICATION

Goal: label every trade `BUY_AGGRESSOR` (initiated by a market buy / buy-stop,
lifting the offer) or `SELL_AGGRESSOR` (hitting the bid).

### 2.1 Deterministic rules (in priority order)

Let `P` = trade price, `B` = prevailing bid, `A` = prevailing ask at (or
immediately before) the trade, `m = (B+A)/2`.

1. **Exchange-stamped side present** → use it. (Best; many feeds don't give it.)
2. **Quote rule (Lee–Ready core):**
   - `P >= A` → `BUY_AGGRESSOR`
   - `P <= B` → `SELL_AGGRESSOR`
   - `B < P < A` (inside spread): go to rule 3.
3. **Midpoint sub-rule:**
   - `P > m` → `BUY_AGGRESSOR`
   - `P < m` → `SELL_AGGRESSOR`
   - `P == m` → go to rule 4.
4. **Tick rule (fallback, needs only trade prices):**
   - `P > P_prev` (uptick) → `BUY_AGGRESSOR`
   - `P < P_prev` (downtick) → `SELL_AGGRESSOR`
   - `P == P_prev` (zero tick) → inherit the last non-zero tick's direction
     (zero-uptick = buy, zero-downtick = sell).
5. **Still tied / no prior tick / first trade** → `UNKNOWN`. Counted in
   `unknown_volume`, excluded from delta.

**Lee–Ready timing correction:** if bid/ask are timestamped, compare the trade
to the quote *prevailing ≥ 1 s before* the trade (quotes often lag on the
tape). With only snapshot quotes this correction is not possible — note it.

### 2.2 Quality of each rule

| rule | accuracy vs. true side (literature, liquid futures) | our ability to run it |
|---|---|---|
| exchange-stamped | ~100% | **no** (never captured) |
| quote rule | ~85–92% | **no** (no per-trade bid/ask) |
| midpoint | folded into quote rule | **no** |
| tick rule only | ~70–78% (worse in fast/one-way tape) | **only on close-to-close** on bars → very coarse |

### 2.3 IN OUR SYSTEM

- No per-trade record → **aggressor classification cannot be run at trade
  level for any instrument.**
- The only executable variant is the **tick rule on bar closes**
  (`sign(C_t − C_{t−1})`), which is a *bar-direction* proxy, not trade
  aggression. Label it `bar_tick_dir`, never "aggressor".
- If mode-3 SnapQuote capture is run forward on the **future**, a *snapshot*
  quote rule becomes possible: classify `last_trade_qty` at each poll using
  the book in the same packet. This is ~1 classified "trade" per 20–30 s —
  a heavily *sampled* delta, with large aliasing error. Feasibility only.

---

## 3. BUY / SELL VOLUME

### 3.1 Definitions

For a set of classified trades `T` in a window:

```
buy_volume(T)     = Σ qty_i  for i where side_i = BUY_AGGRESSOR
sell_volume(T)    = Σ qty_i  for i where side_i = SELL_AGGRESSOR
unknown_volume(T) = Σ qty_i  for i where side_i = UNKNOWN
total_volume(T)   = buy_volume + sell_volume + unknown_volume
```

### 3.2 Aggregation scopes

| scope | window `T` | use |
|---|---|---|
| per tick | the single trade | raw stream |
| **per price level** | all trades at exactly price `p` within a bar | footprint cell (§6) |
| **per candle/bar** | all trades with `bar_start ≤ ts < bar_end` | bar delta (§4) |
| per session | all trades 09:15–15:30 | session delta, cumulative reset anchor |
| rolling | last `N` trades or last `k` seconds | flow momentum |

### 3.3 Candle-approximation proxies (when true classification is absent)

- **Range-distribution:** split bar `V` across price bins in `[L,H]`
  (uniform, or triangular weighted to close). Gives a *volume* profile per
  bar, **not** a buy/sell split.
- **Body-sign split:** `buy_proxy = V · close_location`,
  `sell_proxy = V · (1 − close_location)` where
  `close_location = (C − L)/(H − L)`. Cheap, and wrong precisely on
  absorption bars (big V, tiny range, close mid → 50/50 when the truth may
  be 90/10).
- **Snapshot-counter diff:** `Δtot_buy_qty`, `Δtot_sell_qty` between polls —
  these are *pending limit* quantities, not executions; a rising `tot_buy_qty`
  can mean *passive* bids stacking (bearish absorption setup), the opposite of
  aggressive buying.

### 3.4 IN OUR SYSTEM

- **Cash index:** `V = 0`. No buy/sell volume, no proxy. Full stop.
- **Future:** bar `V` exists (~5 sessions) → range-distribution profile and
  body-sign proxy are computable but unvalidatable (sample too small).
- **Options:** `V` exists per strike; but it is premium turnover on one strike,
  not underlying flow.

---

## 4. DELTA

### 4.1 Definition

```
delta(T) = buy_volume(T) − sell_volume(T)
```

Per bar: `delta_t = buy_volume_t − sell_volume_t`. Units = contracts.

### 4.2 Derived quantities (all deterministic)

| quantity | formula | meaning |
|---|---|---|
| sign | `sign(delta_t)` | net aggressor direction of the bar |
| **delta strength** (normalised) | `delta_t / total_volume_t` ∈ [−1, 1] | how one-sided the bar was (a.k.a. delta %) |
| **delta percentage** | `100 · delta_t / total_volume_t` | same, in % |
| **delta acceleration** | `delta_t − delta_{t−1}` (1st diff); `delta_t − 2·delta_{t−1} + delta_{t−2}` (2nd diff) | flow speeding up / rolling over |
| rolling delta | `Σ_{k=t−n+1}^{t} delta_k` | short-horizon pressure |
| **min/max delta in bar** (needs intrabar path) | running delta low / high within the bar | delta *excursion* — a bar can close +200 delta but have dipped to −500 (hidden sellers) |
| bar delta vs price | `corr(delta_k, ΔC_k)` over a window | is flow "working" (price follows delta) |

### 4.3 Interpretation (context-dependent, never absolute)

- **Positive delta + price up** = healthy buy trend (continuation bias).
- **Positive delta + price flat/down** = **buyers absorbed** (reversal watch).
- **Negative delta + price up** = short covering / hidden bid absorption
  (weak up-move OR strong reversal-up depending on structure).
- **Extreme delta at a structural extreme + no follow-through** = exhaustion
  (§9).

### 4.4 IN OUR SYSTEM

`delta` is **UNCOMPUTABLE for the cash index** (needs §3, which needs §2, which
needs per-trade side). Any "delta" field for NIFTY spot would be fabricated →
**forbidden**. The nearest legitimate proxy is bar-geometry net pressure
(`app/research_engines/order_pressure/formulas.pressure`), which must be named
`pressure_proxy`, scored 0–100, and never called delta.

---

## 5. CUMULATIVE DELTA / CVD

### 5.1 Definition

```
CVD_t = CVD_{t−1} + delta_t          (reset CVD = 0 at session open)
```

Optionally maintain **session CVD** (resets daily) and **running CVD** (never
resets) separately.

### 5.2 Derived

| quantity | formula |
|---|---|
| CVD slope | `(CVD_t − CVD_{t−k}) / k` (or OLS slope over the window) |
| CVD acceleration | 2nd difference of CVD, or 1st difference of the slope |
| CVD trend state | `UP` if slope > +ε and last `m` bars mostly rising; `DOWN` mirror; else `FLAT` (ε from a volatility-scaled threshold, §7.3) |
| **price–CVD divergence** | §10 (structural, on swing pivots) |
| CVD vs VWAP-delta | compare CVD path to a VWAP-anchored expectation |

### 5.3 Interpretation

- Price makes a new high, **CVD does not** → buyers weakening → bearish
  divergence.
- Price ranges, **CVD trends up** → accumulation → breakout-up bias.
- CVD "staircase" (steady slope) = orderly trend; CVD "vertical" = climactic /
  exhaustion risk.

### 5.4 IN OUR SYSTEM

CVD **requires delta (§4)** → **UNCOMPUTABLE for the cash index.** A
`CVD_proxy` from cumulative `pressure_proxy` or cumulative `bar_tick_dir · V`
(future only) may be built for research, clearly labelled, and is expected to
be a poor substitute (it inherits every §3.3 failure).

---

## 6. FOOTPRINT (bid × ask per price, per bar)

### 6.1 Construction

For each bar, and each price level `p` the bar traded at:

```
ask_vol(p)   = Σ qty  of BUY_AGGRESSOR trades at price p   (executed into the ask)
bid_vol(p)   = Σ qty  of SELL_AGGRESSOR trades at price p   (executed into the bid)
delta(p)     = ask_vol(p) − bid_vol(p)
total(p)     = ask_vol(p) + bid_vol(p)
```

The bar's footprint = the ordered list `{ (p, bid_vol(p), ask_vol(p),
delta(p), total(p)) }` for `p` from `L` to `H` on the instrument tick grid.

### 6.2 Bar-level footprint aggregates

| field | formula |
|---|---|
| bar delta | `Σ_p delta(p)` (must equal §4 `delta_t`) |
| **POC (bar)** | `argmax_p total(p)` — the most-traded price in the bar |
| **delta POC** | `argmax_p |delta(p)|` |
| high/low delta | running cumulative delta max / min across `p` in trade order |
| **finishing move** | delta at the extreme `p` in the direction of the close |
| unfinished auction | `H` or `L` where both `bid_vol` and `ask_vol` > 0 (no single-print extreme) → often revisited |

### 6.3 IN OUR SYSTEM

Footprint **requires per-price aggressor volume** → **NOT AVAILABLE** for any
instrument (no trades, no side). `quote_snapshots.depth_json` is *resting book*,
not *executed volume* — it cannot be turned into a footprint. A **TPO / volume
profile** (§13) is the closest we can build and is a different object.

---

## 7. IMBALANCE

### 7.1 Diagonal (footprint) imbalance — the professional definition

Compare **ask volume at price `p`** to **bid volume at price `p − 1 tick`**
(the diagonal, because a market buy at `p` meets a resting offer, and the
comparison partner is the bid one level down):

```
buy_imbalance(p)  :  ask_vol(p)     ≥ R · bid_vol(p − tick)      and ask_vol(p)     ≥ Vmin
sell_imbalance(p) :  bid_vol(p)     ≥ R · ask_vol(p + tick)      and bid_vol(p)     ≥ Vmin
```

`R` = imbalance ratio (commonly tried 2×, 3×, 4×), `Vmin` = a volume floor so
one-lot noise doesn't qualify.

### 7.2 Both ratio orientations (test, don't assume)

- `ask/bid` orientation flags **buy** imbalance when large.
- `bid/ask` orientation flags **sell** imbalance when large.
  Implement one canonical function `imb(p, side)` and derive both; report the
  distribution of `ask_vol/bid_vol` and pick `R` from data (§7.3), not from a
  blog.

### 7.3 Threshold selection (statistical, not arbitrary)

1. Build the empirical distribution of the ratio over a large in-sample set.
2. Candidate `R` = a high percentile (e.g. P80/P90) **and** grid {1.5,2,3,4}.
3. For each `R`, measure **forward outcome** of the flagged event
   (e.g. sign and size of the next `k`-bar return, MFE/MAE in R) on
   *in-sample only*.
4. Keep `R` only if the effect is (a) monotonic in `R` up to a point,
   (b) economically meaningful after costs, (c) **survives OOS + walk-forward**
   with a similar effect size. Never pick `R` for the best in-sample number.

### 7.4 Stacked imbalance

`≥ N` consecutive price levels (default N = 3) all flagged the *same* side →
`stacked_buy` / `stacked_sell`. Stacked zones tend to act as support/resistance
on revisit (the "imbalance shelf").

### 7.5 IN OUR SYSTEM

- **Footprint imbalance: NOT AVAILABLE** (no per-price aggressor volume).
- **Book (L1/L2) imbalance** *is* definable from `depth_json`:
  `book_imb = (Σ bid_size_levels − Σ ask_size_levels) / (Σ bid + Σ ask)`.
  This was **tested and REJECTED at feasibility**
  (`orderflow-l2-imbalance-gate`: 3 same-week sessions, MAE-neutral, 200% not
  special). Snapshot book imbalance is also the easiest field to *spoof*, so
  its predictive value is inherently low.

---

## 8. ABSORPTION

### 8.1 Concept

A large amount of **aggressive** volume hits a price (or a tight band) and
price **fails to move** — a passive participant is *absorbing* every market
order with resting size. Bullish absorption: heavy selling into a level, price
holds → sellers are being absorbed by a large bid → reversal-up watch. Bearish
absorption: mirror.

### 8.2 Measurable conditions (ALL must hold)

Over a short window `W` (e.g. 1–3 bars) at a price band `[p−δ, p+δ]`:

1. **High aggressive volume:**
   `vol(W) ≥ P90` of `vol` over a trailing reference (volume percentile spike).
2. **Limited price progress:**
   `|C_end − C_start| ≤ α · ATR` with `α` small (e.g. 0.25), **and**
   `range(W) ≤ β · ATR` (e.g. 0.6).
3. **One-sided aggression:**
   `|delta(W)| / total_volume(W) ≥ γ` (e.g. 0.35) — the crowd *is* pushing.
4. **Repeated interaction:** `≥ κ` distinct touches of the band in `W`
   (or `≥ κ` bars whose `[L,H]` intersect the band).
5. **Direction of absorption** = **opposite** to `sign(delta(W))`
   (aggressive sellers absorbed → bullish).
6. Optional confirm: on the next `1–2` bars, price closes *away* from the band
   in the absorption direction with `delta` flipping sign.

### 8.3 What absorption is NOT

- Not "a big candle". A big directional candle with matching delta is
  *continuation*, the opposite of absorption.
- Not a single high-volume bar with a wide range (that's a *drive*).

### 8.4 IN OUR SYSTEM

Conditions 1, 3 need volume + delta → **cash index: NOT AVAILABLE.**
Condition 2, 4 (range vs ATR, repeated touches) *are* computable from bars —
that's the `app/orderflow/h1h7_state.py` structural layer's territory, but on
its own it is only "price stalled at a level", which is **not** absorption
without the volume/delta evidence. The engine must return
`absorption: UNOBSERVABLE` for the index, exactly as h1h7_state already lists it.

---

## 9. EXHAUSTION

### 9.1 Concept

The last aggressive push into a move that has *no fuel left*: extreme delta and
volume, price stretched far from value, immediately fails to extend, reverses.

### 9.2 Conditions (combination, weighted — not all mandatory)

At a swing extreme:

1. **Extreme delta:** `|delta_bar|` or `|delta(p_extreme)|` ≥ P95 of the
   trailing distribution.
2. **Volume climax:** bar `V` ≥ P90–P95 trailing.
3. **Price extension:** distance from VWAP (or from the 20-EMA / value area)
   ≥ `θ · ATR` (e.g. 2.5).
4. **Failure to continue:** next 1–2 bars do **not** make a new extreme in the
   push direction (or make one by < `0.15·ATR` and close back inside).
5. **Reversal confirmation:** a close back through the prior bar's 50% level /
   the broken structural level, with `delta` flipping sign.
6. (Footprint) **finishing move** at the extreme with no follow-through print.

Score = weighted sum of the satisfied conditions; flag `buying_exhaustion` /
`selling_exhaustion` above a threshold set on in-sample forward-return data.

### 9.3 IN OUR SYSTEM

Conditions 1–2 (delta, volume climax) → **cash index: NOT AVAILABLE.**
Conditions 3–5 (VWAP distance, failure, reversal) are the validated
`ORDERFLOW_STAGE7_BOUNDARY` / reclaim-distance material and *are* usable. So the
index engine can flag a **structural exhaustion proxy** (extension + failure +
reclaim) but must not claim it saw delta/volume exhaustion.

---

## 10. DELTA / CVD DIVERGENCE

### 10.1 Deterministic definition (on confirmed swing pivots)

Identify swing pivots with a fixed fractal rule: `pivot_high` at `i` iff
`H_i > H_{i±1..±w}`; `pivot_low` mirror (`w` = pivot half-width, e.g. 2–3).

Let two consecutive pivot lows be `(i1, L_price1, CVD1)` and
`(i2, L_price2, CVD2)` with `i2 > i1`:

```
BULLISH divergence :  L_price2 < L_price1   AND   CVD2 > CVD1        (lower low in price, higher low in flow)
```

Two consecutive pivot highs `(H_price1, CVD1)`, `(H_price2, CVD2)`:

```
BEARISH divergence :  H_price2 > H_price1   AND   CVD2 < CVD1        (higher high in price, lower high in flow)
```

Add magnitude gates so a 1-tick "lower low" with a 1-contract CVD wiggle does
not qualify: require `|L_price2 − L_price1| ≥ d_min·ATR` and
`|CVD2 − CVD1| ≥ c_min·σ(CVD_diff)`.

Same construction works with **bar delta** instead of CVD (`delta` at the pivot
bar), and with **price vs cumulative pressure_proxy** as the degraded index
version.

### 10.2 IN OUR SYSTEM

- CVD divergence → **needs CVD → NOT AVAILABLE for the index.**
- **Price vs cumulative `pressure_proxy` divergence** is buildable and is the
  honest degraded form; expect it to be noisy (pressure_proxy ≠ delta).
- Price vs **OI-change** divergence (positioning, not flow) is a *separate*
  legitimate index-available signal — do not conflate it with delta divergence.

---

## 11. LIQUIDITY / DOM

### 11.1 Definitions

| term | definition |
|---|---|
| bid liquidity | Σ resting buy size over the tracked depth (5–20 levels) |
| ask liquidity | Σ resting sell size |
| liquidity imbalance | `(bid_liq − ask_liq) / (bid_liq + ask_liq)` ∈ [−1,1] |
| large resting order | a single level whose size ≥ `R_L ×` the median level size (candidate iceberg if it refills after prints) |
| liquidity withdrawal | resting size at/near touch **falls sharply** just before price reaches it (pulled liquidity → move accelerates) |
| sweep | one aggressive order (or burst) trades *through* ≥ `k` levels, consuming resting size (see §12) |
| market-order aggression | rate of `total_volume` relative to resting size being posted; high aggression + thinning book = impulsive move |

### 11.2 Backtestable vs not

| quantity | needs | historically backtestable here? |
|---|---|---|
| static book shape at a snapshot | one L2 snapshot | **only for the ~6 FUTURE sessions**; INDEX has no book |
| **liquidity withdrawal / refill** | book **event stream** (add/cancel/modify) | **NO** — we only have polled snapshots; between two 25 s polls the book could have been pulled and replaced many times |
| iceberg detection | trades vs. displayed size over time, high-frequency | **NO** |
| sweep | trade burst + multi-level book delta | **NO** (needs the event stream / tick burst) |
| resting-size imbalance at touch | snapshot at the right instant | **weak** — poll cadence rarely lands on the touch; already **REJECTED** at feasibility |

### 11.3 IN OUR SYSTEM

DOM analytics are **live-only and future/option-only**, snapshot-sampled, and
**cannot be backtested** to a train/OOS standard. Treat as a *live confirmation
overlay* at best, never a historical signal source.

---

## 12. SWEEP

### 12.1 Detection (needs tick + book)

A **buy-side sweep** (stop run up / liquidity grab above a level `L`):

1. Price trades **through** `L` (a prior swing high / equal highs / round
   number) by ≥ `s·ATR`.
2. In a short burst (≤ `w` seconds / ≤ `n` trades): `total_volume` ≥ P90 and
   `delta` strongly positive (`delta/total ≥ 0.6`).
3. Book above `L` **thins** (aggregate ask size across swept levels drops ≥ `x%`).
4. **Follow-through vs rejection** on the next 1–3 bars:
   - closes and holds above `L` with continued + delta → *breakout sweep*.
   - snaps back below `L` with delta flipping → **sweep reversal** (the classic
     "stop hunt then reverse").

Sell-side sweep = mirror below a support / equal lows.

### 12.2 IN OUR SYSTEM

Steps 2–3 need tick volume/delta and a book stream → **NOT AVAILABLE.**
Step 1 + step 4 (price pierces a level then reclaims / fails) = the
**reclaim-distance boundary** already validated in Stage 7 (knee ≈ 0.60,
shallow ≤ 0.28). So the index engine can flag a **structural liquidity-grab
proxy** (`pierce + reclaim`), and must label it as structure, not a
volume-confirmed sweep.

---

## 13. VALUE AREA / VOLUME PROFILE

### 13.1 Definitions

Over a chosen window (session / balance area / composite):

| term | definition |
|---|---|
| **volume profile** | volume traded at each price bin |
| **POC** (Point of Control) | the price bin with the **most** volume |
| **Value Area** | the contiguous price band around POC containing `value_pct` of total volume (convention 70%), grown by repeatedly adding whichever adjacent bin (top+1 or bottom−1) holds more volume |
| **VAH / VAL** | Value Area High / Low = the band edges |
| naked POC | a prior POC price not yet revisited (acts as a magnet) |
| TPO profile (Market Profile) | count of 30-min brackets that touched each price — needs only bar H/L + time, so it is **exact from bars** |

### 13.2 Use with order flow

- Trend day: price accepts away from prior VA; look for **continuation** flow
  signals in the direction of the break.
- Balance day: fade VAH/VAL back toward POC unless flow confirms a breakout.
- POC / naked POC / HVN act as magnets; LVN (low-volume nodes) as fast-move
  zones and rejection points.

### 13.3 IN OUR SYSTEM

- **TPO profile: EXACT** from `market_candles` (bar H/L + timestamp).
- **Volume profile: APPROXIMATE** — `app/orderflow/profile.py` already does
  `OHLCV_RANGE_DISTRIBUTION` for instruments that carry volume (future/option).
  **Cash index has no volume → volume profile is undefined; only the TPO
  profile is available for NIFTY spot.**
- POC/VAH/VAL from the TPO profile are legitimate and usable.

---

## 14. VWAP

### 14.1 Definitions

```
VWAP_t   = (Σ_{k≤t} price_k · vol_k) / (Σ_{k≤t} vol_k)          [session-anchored; reset at open]
```

With only bars: `price_k = hlc3_k` (typical price), `vol_k = V_k`.
**Bands:** `VWAP ± n · σ_vwap` where `σ_vwap` is the volume-weighted std of
`price − VWAP` (or an ATR-scaled proxy).

| quantity | formula / meaning |
|---|---|
| VWAP deviation | `(C_t − VWAP_t)` and `(C_t − VWAP_t)/ATR_t` (normalised) |
| price vs VWAP state | `ABOVE` / `AT` (`|dev| ≤ ε`) / `BELOW` |
| band tag | which σ-band the price is in (−2,−1,0,+1,+2) |
| VWAP slope | `(VWAP_t − VWAP_{t−k})/k` |

### 14.2 Order-flow + VWAP confirmation

- Longs only when `price ≥ VWAP` (or reclaiming it) **and** flow (delta/CVD or
  proxy) is positive; shorts mirror. VWAP is the "fair value" filter that
  stopped the SSL Hybrid engine bleeding in the open/close (see
  `ssl-hybrid-nogo`).
- VWAP-band mean-reversion: fade a +2σ tag **only** if flow shows exhaustion /
  absorption at the band, else it is a trend day and the band is support.

### 14.3 IN OUR SYSTEM

- **Cash index VWAP: volume = 0 → true VWAP is undefined.** Use a
  **session-anchored cumulative HLC3 mean** as a disclosed *price-VWAP proxy*
  (equal per-bar weight). This is the same substitution the SSL work used.
  Label `vwap_method: "PRICE_PROXY_EQUAL_WEIGHT"`.
- **Future VWAP: real** (volume present) but ~5 sessions only.
- The repo already computes a VWAP field in `sr_engine` / snapshots; verify its
  method string before relying on it for the index.

---

## 15. MARKET STRUCTURE

### 15.1 Swing labelling (deterministic)

Pivots via the fractal rule (§10.1). Then, walking pivots in time:

- **HH** (higher high): `pivot_high > prev pivot_high`
- **HL** (higher low): `pivot_low > prev pivot_low`
- **LH**, **LL**: mirrors
- **Uptrend** = sequence of HH + HL; **downtrend** = LH + LL; **range** =
  alternating without new extremes.
- **BOS** (break of structure): close beyond the last opposing pivot in the
  trend direction → continuation.
- **CHoCH** (change of character): first BOS against the prevailing trend →
  possible reversal.
- **Retest:** price returns to the broken level (± `r·ATR`) and holds/rejects.

### 15.2 Why order flow is not used alone

- Flow signals fire constantly; **most are noise**. Structure says *where* a
  flow event matters (at a level, a VA edge, a prior POC, a swing pivot).
- A delta spike mid-range ≈ nothing. The *same* delta spike at a retest of a
  broken level with VWAP confluence ≈ a setup.
- Flow answers "who is winning right now"; structure answers "does that change
  the path". You need both plus regime (§16).

### 15.3 IN OUR SYSTEM

Fully available — this is the validated core (`h1h7_state.py`,
`ORDERFLOW_STAGE7_BOUNDARY.md`). Structure is the **spine** the index engine
hangs everything on, because it is the only layer with real multi-year data.

---

## 16. REGIME DETECTION

### 16.1 Classify each bar / window into one of

`TRENDING` · `RANGING` · `CHOP` · `HIGH_VOLATILITY` · `LOW_VOLATILITY`
(trend and volatility are two axes; a state is a pair, e.g. TRENDING+HIGH_VOL).

### 16.2 Measurable inputs and rules

| variable | formula | trend/vol read |
|---|---|---|
| **ADX(14)** | Wilder DMI | ADX ≥ 25 → trending; ≤ 18 → range/chop |
| **ATR(14) percentile** | `ATR_t` vs its own trailing distribution | ≥ P70 → HIGH_VOL; ≤ P30 → LOW_VOL |
| **range expansion** | `range_t / mean(range_{t−n..t−1})` | > 1.5 sustained → expansion/trend |
| **efficiency ratio** (Kaufman) | `|C_t − C_{t−n}| / Σ|C_k − C_{k−1}|` | → 1 trending, → 0 chop |
| **VWAP distance** | `|C − VWAP|/ATR` | large & rising → trend day; oscillating around 0 → balance |
| **BB width** | `(upperBB − lowerBB)/mid` | squeeze → LOW_VOL pre-expansion |
| volume trend (where available) | rising vol on expansion bars | confirms trend legitimacy |

Combine into a small rule table or a scored classifier; **hysteresis** (require
`m` consecutive bars to switch state) to avoid regime flip-flop.

### 16.3 Why regime gates everything

- Delta **continuation** signals only work in `TRENDING`.
- Absorption / divergence **reversal** signals only work at range edges /
  `RANGING`, and are traps in `TRENDING+HIGH_VOL`.
- `CHOP` → **NO TRADE** regardless of flow score.

### 16.4 IN OUR SYSTEM

Fully available from bars (ADX, ATR, ER, VWAP-dist, BB). The repo already has
`app/engines/state_classifier.py` / regime detection in `sr_engine` — reuse,
don't re-invent.

---

## 17. ORDER-FLOW SIGNAL FAMILIES (defined separately, not combined yet)

| # | family | core trigger | required data | index-available? |
|---|---|---|---|---|
| A | **Delta continuation** | trend regime + `delta` (and CVD slope) aligned with price + pullback holds | delta, CVD | **NO** (proxy only) |
| B | **Delta reversal** | delta extreme against price at a level + delta flip | delta | **NO** |
| C | **CVD divergence** | §10 bullish/bearish divergence on pivots | CVD | **NO** (pressure_proxy divergence only) |
| D | **Absorption reversal** | §8 absorption at a level + confirm | delta, volume, book | **NO** (structural stall proxy only) |
| E | **Imbalance breakout** | §7 stacked imbalance shelf broken with flow | footprint | **NO** (book-imb rejected) |
| F | **Liquidity-sweep reversal** | §12 pierce of a level + snap-back | tick burst + book | **NO** (reclaim-distance proxy — this one *is* validated as structure) |
| G | **Order flow + VWAP** | flow direction confirmed by price-vs-VWAP + band context | delta + VWAP | **partial** (proxy VWAP + pressure_proxy) |
| H | **Order flow + market structure** | flow event *at* a BOS/CHoCH/retest | delta + pivots | **partial** (structure real, flow proxy) |
| I | **Order flow + S/R (+ OI)** | flow event at a validated S/R / OI wall | delta + S/R | **partial** (S/R + OI real, flow proxy) |
| J | **Multi-confirmation** | ≥ k of {A..I} agree in the same window & direction | all | only as far as the weakest input allows |

For NIFTY spot **today**, only **F/H/I** have a real, validated backbone
(structure + reclaim + S/R/OI), with the "order flow" half degraded to
`pressure_proxy`. A/B/C/D/E are **not honestly buildable** for the index.

---

## 18. ORDER-FLOW QUALITY SCORE (0–100)

### 18.1 Design principles

- **Additive, bounded, transparent** (same shape as `hcs/score.py`): each
  category contributes `w_c · s_c` with `s_c ∈ [0,1]`, `Σ w_c = 100`.
- **No double-counting.** Delta, CVD, and footprint delta are the *same*
  information at different aggregations — pick **one** as the "flow" input, not
  three. Same for "ADX regime" and "efficiency ratio" (correlated → one slot).
- Every category carries a **status**: `OK` / `PROXY` / `UNOBSERVABLE`. An
  `UNOBSERVABLE` category contributes **0** and its weight is **redistributed**
  to the observable ones (so the score stays 0–100 and is honest about how much
  evidence it actually had — expose `evidence_coverage = Σ w_observable / 100`).

### 18.2 Category slate (weights are a *starting proposal*, to be fitted)

| category | input (choose ONE per row) | proposed w | index status |
|---|---|---|---|
| Flow direction & strength | `delta%` **or** `pressure_proxy_net` | 22 | PROXY |
| Flow persistence | `CVD slope` **or** `cum pressure_proxy slope` | 12 | PROXY |
| Flow vs price (divergence/confirmation) | §10 on chosen flow series | 12 | PROXY |
| Absorption / exhaustion at level | §8/§9 score | 10 | UNOBSERVABLE (index) → 0 |
| Structure alignment | BOS/CHoCH/retest state (§15) | 15 | OK |
| Regime fit | §16 (signal family must match regime) | 10 | OK |
| VWAP context | §14 deviation + band | 8 | PROXY (index) |
| Value-area context | POC/VAH/VAL proximity (§13) | 6 | OK (TPO) |
| Liquidity / sweep | §12 reclaim-distance proxy | 5 | OK (structure) / UNOBS (true sweep) |
| Momentum | ROC / ATR-normalised thrust | — folded into "flow strength", not separate | — |

### 18.3 Scoring model

```
raw   = Σ_c  w_c · s_c · 1[status_c ≠ UNOBSERVABLE]
avail = Σ_c  w_c        · 1[status_c ≠ UNOBSERVABLE]
score = 100 · raw / max(avail, ε)          # renormalised to the evidence we had
OFQ   = score                              # Order-Flow Quality, 0..100
```

Plus hard vetoes (regardless of score): `regime == CHOP`, `data_quality_score
< q_min`, `evidence_coverage < 0.5`, structure conflicts with flow direction.

### 18.4 Calibration

`OFQ` is **not** a probability. Map `OFQ → P(win)` per `regime × signal_family`
with the same closed-form logistic + isotonic recalibration the repo already
uses (`app/backtest/calibration.py`), fit on **in-sample resolved trades
only**, and treat it as `NOT VALIDATED` until it beats base-rate OOS (every
prior engine has failed this — expect the same until proven otherwise).

---

## 19. SIGNAL LIFECYCLE (deterministic, causal)

```
RAW TICKS  (or, for us: polled snapshots / bars)
  └─ DATA QUALITY GATE (§20)  ── fail → emit NO_TRADE(reason=DATA)
       └─ AGGRESSOR CLASSIFICATION (§2)          [index: SKIPPED — UNOBSERVABLE]
            └─ BUY / SELL VOLUME (§3)            [index: SKIPPED]
                 └─ DELTA (§4)                   [index: pressure_proxy only]
                      └─ CVD (§5)                [index: cum pressure_proxy]
                           └─ FOOTPRINT (§6)     [index: SKIPPED — profile/TPO instead]
                                └─ IMBALANCE (§7)[index: SKIPPED]
                                     └─ ABSORPTION / EXHAUSTION (§8/§9)  [index: structural proxy]
                                          └─ DIVERGENCE (§10)            [index: proxy]
                                               └─ LIQUIDITY / SWEEP (§11/§12)  [index: reclaim proxy]
                                                    └─ MARKET STRUCTURE (§15)  [OK]
                                                         └─ VWAP (§14)         [index: price proxy]
                                                              └─ VALUE AREA (§13)  [TPO OK]
                                                                   └─ REGIME (§16)  [OK]
                                                                        └─ ORDER-FLOW SCORE (§18)
                                                                             └─ SIGNAL FAMILY MATCH (§17)
                                                                                  └─ CONFIRMATION (bar close / N+1 hold)
                                                                                       └─ BUY / SELL / NO_TRADE
```

**Confirmation rule:** a candidate detected on bar/interval `N` is only
actionable after `N` **closes** and `N+1` satisfies the family's confirmation
(e.g. hold above the trigger, delta/proxy not flipping). Entry timestamp =
`N+1` open (or the confirming close). Never `N` intrabar.

---

## 20. DATA QUALITY

### 20.1 Pre-flight checks (run before any calculation)

| check | rule | action on fail |
|---|---|---|
| missing bars/ticks | gap `> expected_interval · g` (e.g. g = 2) | mark window `DEGRADED`; do not span the gap for CVD/structure |
| duplicate | identical `(ts, price, qty)` or `(token, snap_key)` repeat | drop duplicate |
| bad timestamp | non-monotonic, future-dated, pre-open | drop / clamp; if > `x%` of window → `BAD` |
| out-of-order | `ts_i < ts_{i−1}` | stable-sort; count; if many → feed suspect |
| zero volume | `V == 0` on an instrument expected to have volume | if index → *expected*, set `volume_available = false`; if future → `DEGRADED` |
| crossed / locked book | `bid ≥ ask` | drop that snapshot's book fields |
| invalid price | `≤ 0`, outside circuit limits, `> k·ATR` jump vs neighbours | drop / winsorise; flag |
| stale snapshot | `now − exch_ts > s_max` | `feed_age` penalty |
| session boundary | trades stamped outside 09:15–15:30 (NSE) | exclude from session aggregates |

### 20.2 DATA QUALITY SCORE

```
DQS = 100 · Π_j (1 − penalty_j)          # multiplicative, each penalty ∈ [0,1)
```
with per-check penalties (missing 0.1–0.4 by severity, crossed-book 0.1,
staleness scaled by age, etc.). Also emit the **capability flags** the engine
needs downstream:
`{ has_trades, has_aggressor, has_L1, has_L2_snapshots, has_L2_stream,
   volume_available, n_sessions, cadence_sec }`.

### 20.3 The mandatory honesty gate

If `has_trades == false` **or** `has_aggressor == false`, the engine header
**must** state:

> **TRUE ORDER FLOW NOT AVAILABLE — delta / CVD / footprint / imbalance /
> absorption / sweep are approximated or omitted. See capability flags.**

**Never fabricate bid/ask, trade side, or per-trade size from OHLC.**
`buy_proxy`/`sell_proxy` from candle geometry are allowed only with the
`method` string attached and never under the names delta/CVD/footprint.

---

## 21. BACKTEST SAFETY

| rule | enforcement |
|---|---|
| **no look-ahead** | every feature at time `t` uses only data with `ts ≤ t`; indicators seeded left-to-right; percentile/threshold references use **trailing** windows only |
| **no future ticks / future bars** | the bar under evaluation is `N`; its `H/L/C` are not known until it closes → detection uses `N−1` and earlier; action on `N+1` |
| **no repainting** | no `var` state that rewrites on the developing bar; pivots confirmed only after `w` bars pass (a pivot at `i` is not known until `i+w`) |
| **train / validation / OOS / holdout** | split by **session blocks**, chronological; expanding-window walk-forward; **purge + embargo** around each boundary so a trade opened pre-boundary and closed post- does not leak |
| **thresholds fixed from in-sample** | `R`, `Vmin`, percentiles, score weights, calibration curve — all frozen from the train split; OOS is scored **once** |
| **costs** | per round-trip: spread + slippage + fees, in points **and** in R; sensitivity at 0 / realistic / 2× |
| **survivorship / selection** | test every signal family and every threshold that was considered, report all; never present only the surviving cherry |
| **confirmation lag counted** | entry at `N+1`, not at the `N` signal price — the lag *is* part of the edge test |
| **verdict rule** | GO only if OOS **and** walk-forward both clear: expectancy > costs, PF ≥ 1.3, positive across ≥ 2 independent years/regimes, `n_OOS ≥ 40`. Anything else → NO-GO / NOT VALIDATED |

---

## 22. WHAT WE WILL DO LATER (not now)

1. `ORDERFLOW_ENGINE v1` — build to the architecture in §23, **candle-approx
   only**, structure-spine, on **NIFTY 50** first.
2. Validate v1 standalone (§21). Expect NO-GO on the pure candle-approx form —
   proceed anyway to establish the baseline and the harness.
3. In parallel: **start persisting mode-3 SnapQuote on the NIFTY future**
   (`app/l2capture/`) so that in ~2–3 months a real (if snapshot-sampled)
   delta/book dataset exists for a *future-only* v2.
4. Only if a component validates OOS: combine with SSL Hybrid PRO, SuperTrend,
   Donchian, VWAP/ORB, EMA-pullback, QQE → **RK MULTI-STRATEGY ENGINE**
   (a voting / gating layer, each sub-strategy independently validated first).

---

## 23. PROPOSED ORDERFLOW_ENGINE ARCHITECTURE (design, not code)

### 23.1 Module map (mirrors `research_engines/` conventions — READ-ONLY, no live wiring)

```
app/research_engines/orderflow/
  __init__.py        SAFETY banner: research only, no broker, no order path
  config.py          all thresholds/weights; every filter default OFF/neutral
  data.py            loaders: market_candles (RO), Kaggle 1m (RO), quote_snapshots
                     book (RO); returns bars + capability flags; NEVER fabricates
  quality.py         §20 checks + DQS + capability flags
  classify.py        §2 aggressor rules (runs only when has_aggressor/has_L1);
                     else returns UNOBSERVABLE
  volume.py          §3 buy/sell volume + the labelled proxies (method strings)
  delta.py           §4/§5 delta, CVD, slopes, acceleration (or *_proxy)
  footprint.py       §6 (only when per-price aggressor volume exists; else N/A)
  imbalance.py       §7 diagonal (footprint) + book (L2 snapshot) + threshold
                     search harness
  absorption.py      §8/§9 measurable conditions; structural-proxy variant
  divergence.py      §10 pivot-based, deterministic
  liquidity.py       §11/§12 DOM analytics (live/future only) + reclaim proxy
  profile.py         reuse app/orderflow/profile.py (TPO exact / volume approx)
  vwap.py            §14 true (future) / price-proxy (index), method-tagged
  structure.py       reuse/wrap app/orderflow/h1h7_state.py pivots + BOS/CHoCH
  regime.py          reuse app/engines/state_classifier.py (ADX/ATR/ER/BBW)
  score.py           §18 additive OFQ with UNOBSERVABLE-aware renormalisation
  families.py        §17 A..J detectors, each independent, each regime-gated
  signal.py          §19 lifecycle orchestrator -> BUY / SELL / NO_TRADE + why
  backtest.py        §21 walk-forward harness, GO/NO-GO verdict, points + R
  tests/             causality, determinism, no-live-import, no-fabrication,
                     threshold-search reproducibility, capability-flag honesty
```

### 23.2 Layered data contract

```
L0  raw            bars / polled snapshots / (future) SnapQuote frames
L1  quality        DQS + capability flags   ── gate everything below
L2  primitives     [true] classify→volume→delta→CVD→footprint
                   [proxy] pressure_proxy, cum_pressure_proxy, tpo/volume profile
L3  events         imbalance, absorption, exhaustion, divergence, sweep/reclaim
L4  context        structure (BOS/CHoCH/retest), VWAP, value area, regime
L5  score          OFQ 0..100 + evidence_coverage + status per category
L6  families       A..J candidate detectors (regime-gated, level-gated)
L7  decision       confirmation (N+1) → BUY / SELL / NO_TRADE (+ full reason set)
L8  backtest       walk-forward, costs, GO/NO-GO — never touches live
```

### 23.3 Configuration philosophy

Every threshold (`R`, `Vmin`, percentile cutoffs, score weights, regime
bounds, confirmation window) lives in `config.py`, defaults to the
neutral/OFF value, is **fitted only on the train split**, and is frozen before
OOS. No magic numbers in the detectors.

### 23.4 Output contract (per evaluated interval)

```
{
  ts, symbol, tf,
  decision: BUY | SELL | NO_TRADE,
  ofq_score: 0..100,
  evidence_coverage: 0..1,
  data_quality_score: 0..100,
  capability: { has_trades, has_aggressor, has_L1, has_L2_snapshots,
                has_L2_stream, volume_available },
  regime: TRENDING|RANGING|CHOP|HIGH_VOL|LOW_VOL (pair),
  structure_state: HH|HL|LH|LL|BOS|CHoCH|RETEST|NONE,
  families_fired: [ ... ],           # which of A..J and their direction
  flow: { method: "TRUE"|"PRESSURE_PROXY"|"UNOBSERVABLE", delta|proxy, cvd|proxy, ... },
  vwap: { method: "VOLUME"|"PRICE_PROXY_EQUAL_WEIGHT", dev_atr, band },
  levels: { poc, vah, val, nearest_sr, nearest_oi_wall },
  reasons: [ ... ], vetoes: [ ... ],
  entry_ref, stop_ref, t1, t2, t3,   # only when decision != NO_TRADE
}
```

---

## 24. DELIVERABLE CHECKLIST (the 10 points requested)

| # | item | where |
|---|---|---|
| 1 | Required data | §1, §1.1, §1.3 |
| 2 | Mathematical formulas | §2–§16, §18.3, §20.2, §21 |
| 3 | Detection rules | §7–§12, §15, §16, §17 |
| 4 | Signal types | §17 (families A–J), §19 |
| 5 | Scoring architecture | §18 (additive OFQ, UNOBSERVABLE-aware renorm, calibration) |
| 6 | Data limitations | §0, §1.3, §11.2, §20.3, and every "IN OUR SYSTEM" block |
| 7 | Backtest methodology | §21 (+ §19 confirmation lag) |
| 8 | Needs true tick/bid/ask | §2 (aggressor), §3–§7 (volume/delta/CVD/footprint/imbalance), §8/§9 (volume side), §11/§12 (book stream) — **all NOT AVAILABLE** |
| 9 | Computable from OHLCV | geometry `pressure_proxy` (§3.3), TPO profile + approx volume profile (§13), VWAP price-proxy (§14), market structure / BOS / CHoCH / retest (§15), reclaim-distance / structural sweep & exhaustion proxies (§9/§12), regime ADX/ATR/ER/BBW (§16) |
| 10 | Proposed engine architecture | §23 (module map, L0–L8 layers, config philosophy, output contract) |

---

## 25. WHAT IS MISSING FROM *OUR* ORDER FLOW TODAY (gap list)

1. **Aggressor side** — the defining field. Not captured anywhere, ever.
2. **Per-trade size** — no time-and-sales record.
3. **Any tick feed** — the WS runs mode 1 (LTP only); nothing tick-level is
   persisted.
4. **A book *event* stream** — only ~20–30 s polled snapshots exist; no
   add/cancel/modify → no liquidity-withdrawal, iceberg, or true sweep.
5. **Cash-index volume** — `0`/`NULL` everywhere → no delta/CVD/footprint/
   volume-profile/true-VWAP for NIFTY spot.
6. **L2 history depth** — ~6 FUTURE sessions, ~14 clustered OPTION sessions,
   one regime → cannot form train/OOS.
7. **Consumer code for the book columns** — `bid/ask/bid_qty/ask_qty/
   tot_buy_qty/tot_sell_qty/depth_json` are *written and never read*.
8. **A footprint object** — nothing constructs per-price bid/ask volume
   (impossible without §1–2).
9. **A statistically-fitted imbalance threshold** — the one L2 probe was
   feasibility-rejected, not fitted.
10. **A unified order-flow score + calibration** — pieces exist
    (`order_pressure` pressure, `h1h7_state` structure, `profile`, `sr_engine`
    regime/VWAP) but they are not composed into one OFQ with capability-aware
    renormalisation and a per-regime calibration.

**Bottom line:** we can build a *market-structure + candle-pressure + profile +
regime* engine and call it what it is. We **cannot** build true order flow for
NIFTY spot from current data, and must say so in the engine header. The only
path to real order flow is forward capture of mode-3 SnapQuote on the **NIFTY
future** for enough sessions to validate — and even then it is snapshot-sampled,
not a true tape.
