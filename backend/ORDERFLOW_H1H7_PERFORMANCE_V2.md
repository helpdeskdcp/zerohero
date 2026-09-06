# H1 / H7 Structural State Engine — performance evaluation V2 (Kaggle-expanded)

**Verdict for `H1_CONT`: `NOT VALIDATED` — the actionable state received ZERO
new data.** Kaggle has **no MCX CRUDEOIL / MCX Natural Gas intraday data at
all**, and the engine emits `H1_CONT` for CRUDEOIL only. So V2 cannot move the
CRUDEOIL `H1_CONT` verdict in either direction.

**What V2 *does* establish:** the **H7 = AVOID** classification is confirmed on a
**completely independent instrument, vendor, and period** — Kaggle NIFTY‑50 5‑minute
index OHLC, **2 616 sessions, 2015‑01‑09 → 2026‑05‑18, all three regimes,
11 360 events**. "Buying the break" of an `H7_SUPPORTED` event wins **11.2 %**
(95 % CI 10.0–12.4 %, E[R] −1.005) — in every regime and every chronological
split. This **strengthens `H7 = SUPPORTED`**. Nothing is `PROVEN`.

Nothing about production changed: **no** engine logic, Stage‑6 baseline, trading
logic, execution, broker order logic, or production behaviour. `live_trading`
stays `false`, `paper_mode` stays `true`. The evaluator gained one data provider
(`--source kaggle_nifty`) and a Wilson‑CI helper; `app/orderflow/h1h7_state.py`
and the baseline `_decision_list` are byte‑identical.

- Evaluator: `backend/scripts/orderflow_h1h7_performance.py --source kaggle_nifty`
- Event table: `backend/data/orderflow_h1h7_performance_v2.csv` (**11 360 rows**)
- Data manifest (checksums, per‑file schema, suitability): `backend/data/historical/kaggle/MANIFEST.json`
- Raw Kaggle files: `backend/data/historical/kaggle/<owner>__<name>/` (git‑ignored — large, license not for redistribution; kept local, originals unmodified)

---

## Kaggle datasets found

`kaggle datasets list` over ~25 queries (NIFTY futures / 50 / intraday / tick /
order book / market depth / L2 / NSE futures / CRUDEOIL / MCX / Natural Gas /
Indian futures / NSE tick / NSE order flow, plus multi‑year variants).

### A — directly useful for H1/H7 validation (OHLC + reliable timestamps)

| ref | instrument | tf | span | rows | quality |
|---|---|---|---|---|---|
| **`debashis74017/nifty-50-minute-data`** → `NIFTY 50_5minute.csv` | NIFTY 50 **cash index** (NSE) | **5 min** (native) | 2015‑01‑09 → 2026‑05‑18 | 209 888 | **0** dup, **0** impossible prices, **0** OHLC‑integrity violations; 143 (0.07 %) out‑of‑session bars filtered; 2 810 session dates matching the NSE calendar |
| `mavrick136/10-years-of-nifty-50-1-minute-data-20152025` | NIFTY 50 cash index | 1 min | 2015‑01‑01 → 2025‑12‑24 | ~1.07 M | clean; needs 1→5 min resample; overlaps the above |
| `nishanthsalian/indian-stock-index-1minute-data-2008-2020` → `NIFTY_2008_2020.csv` | NIFTY cash index | 1 min | 2008‑01‑01 → 2020‑11‑27 | ~1.18 M | **no volume column at all**; older regimes; needs resample |

### B — useful for future order‑flow research (real L1/L2/depth)

| ref | what it actually is |
|---|---|
| `ranjan15/niftyfutures-tick-by-tick-level-5-depth-data` → `data.csv` | **Genuine 5‑level bid/ask depth (price+qty+orders per level) for ONE NIFTY‑futures session (2025‑04‑28, 20 730 rows, 1‑second snapshots)** + LTP, cumulative volume, OI, day‑cumulative total buy/sell **pending** qty. **No trade‑side / aggressor column.** Header↔data alignment is questionable (the first "bid" triple reads like prev‑close/day‑hi/day‑lo; the real ladder starts at level 2). One session ⇒ a code‑development sample, not validation data. |

### C — not suitable (rejected)

- **No MCX CRUDEOIL / MCX Natural Gas intraday data exists on Kaggle** (every commodity‑futures query returned US equities/commodities, daily bhavcopies, or nothing).
- `orkadd` / `getdatafinance` **USOIL 1m OHLCV** — WTI/US crude, **wrong exchange, contract, currency, session** ≠ MCX CRUDEOIL.
- `anshbhardwaj2992004` **MCX Gold / SilverMCX** 1‑min intraday — real MCX, but Gold/Silver, not crude/gas.
- `drprasadkulkarni/indian-mcx-metal-and-energy` — XLSX, **daily**, metals.
- `debashis74017/algo-trading-data-nifty-100` (4 GB) — pre‑computed indicators (look‑ahead risk), 2026 only, Nifty‑500 constituents not the index/future.
- Per‑stock daily files, candlestick **image** datasets, "nifty tick" datasets that are actually 1‑min OHLC, daily‑only futures CSVs (`yashkmd`, `rbtj1729`, `jaitrav`).

---

## TASK 7 — data compatibility report (primary dataset)

`debashis74017/nifty-50-minute-data → NIFTY 50_5minute.csv`

| check | result |
|---|---|
| **schema** | `date, open, high, low, close, volume` → mapped explicitly to `{bar_start, o, h, l, c, v}`; `v` forced to `0.0` (not derived, not faked). |
| **timezone** | Naive timestamps = **IST** — NSE regular session opens 09:15, confirmed in the data. Adapter keeps only 09:15–15:30 bars. |
| **timestamps** | Monotonic 300‑second grid within each session (verified on a mid‑dataset session: all steps = 300 s). **0 duplicate timestamps** across 209 888 rows. |
| **instrument** | NIFTY 50 **spot index**, NSE — *not* the traded futures contract. The H1/H7 engine is **volume‑free**, and the frozen research treats NIFTY as INDEX bars whenever futures volume is absent (`market_hub.session_bars` FUTURE→INDEX fallback), so this is schema‑ and semantics‑compatible. Flagged: it is spot, not the NIFTY future. |
| **futures vs spot / contract / expiry** | Spot index — no contract, no expiry roll, no basis. (A futures series would add roll artefacts; spot avoids them.) |
| **OHLC integrity** | **0** rows violate `low ≤ open,close ≤ high`; **0** rows with a non‑positive price or `high < low`. |
| **duplicates** | **0**. |
| **gaps** | Session‑day counts per year 241–252 — matches the NSE trading calendar (no missing days). Intraday: standard NSE session, no lunch break; short/holiday‑truncated sessions (min 9 bars) are dropped by the engine's own `n ≥ START_IDX+FWD_WINDOW+1` guard. |
| **impossible prices** | None found. |
| **look‑ahead risk** | The adapter only groups **completed** bars by session date and hands each whole session to the **unchanged** `classify_session`, which is itself causal (proven by its replay test — detection ≤ idx, window idx+1..idx+3, walk from idx+1). No data crosses session boundaries. The chronological split is by session‑date rank, fixed before classification. |
| **silent transforms** | None. Prices are used as‑is. Only filtering (session hours, price sanity) and grouping. |

Secondary A datasets (`mavrick136`, `nishanthsalian`) were **not** fed into V2 —
kept as a future older‑regime / cross‑vendor extension — to keep V2 on one clean
native‑5‑minute source.

---

## TASK 8 — V2 performance results (underlying / index walk)

`win` = realised R at a fixed 3R target with realistic stop fill > 0. Direction =
spike direction for every state (for `H7_*` = the "buy the break" outcome AVOID
tells you to skip; fading H7 is REJECTED, Stage‑8, not walked). Baseline = frozen
Stage‑6 decision list on the **same events**.

### Data

- **11 360 eligible events / 2 616 NIFTY sessions / 2015‑01‑09 → 2026‑05‑18.**
- Regimes: **CHOP 5 575 · TREND_DOWN 3 530 · TREND_UP 2 255** events.
- **Independent session count** ≈ 2 616 (each session is a distinct trading day; intraday events within a session remain correlated).
- **Independent `H1_CONT` signal count = 0** (see verdict).

### A) Pooled — new engine per state

| state | n | sessions | W/L | win % | 95 % CI | E[R] | PF | net R | max DD | max consec loss | MFE~ | MAE~ | P(+1R) | P(SL first) |
|---|--:|--:|:--:|--:|:--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| **H1_CONT** | **0** | — | — | — | — | — | — | — | — | — | — | — | — | — |
| H1_CONT_OBSERVE (NIFTY H1‑gate) | 732 | 619 | 447/285 | **61.1** | 57.5–64.5 % | +0.618 | 2.49 | +452.4 | −10.7 | 8 | 1.58 | −0.44 | 0.69 | 0.15 |
| H1_CONT_BLOCKED | 2 292 | 1 452 | 1414/877 | 61.7 | 59.7–63.7 % | +0.612 | 2.55 | +1 403 | −11.6 | 8 | 1.48 | −0.38 | 0.67 | 0.15 |
| H1_WEAK | 580 | 466 | 375/204 | 64.7 | 60.7–68.4 % | +0.836 | 3.01 | +484.6 | −15.1 | 6 | 1.97 | −0.41 | 0.82 | 0.08 |
| **H7_TRAP** | 1 247 | 953 | 29/1218 | **2.3** | **1.6–3.3 %** | **−1.395** | 0.03 | −1 740 | −1 740 | 205 | 0.17 | −1.32 | 0.05 | **0.94** |
| H7_LEANING_TRAP | 1 421 | 1 080 | 269/1152 | 18.9 | 17.0–21.0 % | −0.663 | 0.31 | −942.7 | −944.5 | 24 | 0.37 | −1.10 | 0.24 | 0.66 |
| AMBIGUOUS | 4 147 | 2 090 | 1601/2545 | 38.6 | 37.1–40.1 % | −0.059 | 0.91 | −243.7 | −254.7 | 13 | 0.84 | −0.98 | 0.44 | 0.38 |

### Derived buckets

| bucket | n | sessions | win % | 95 % CI | E[R] | PF | net R |
|---|--:|--:|--:|:--:|--:|--:|--:|
| **H7_SUPPORTED** (research_status = SUPPORTED) | **2 668** | **1 600** | **11.2** | **10.0–12.4 %** | **−1.005** | 0.15 | −2 682 |
| **H7_AVOID** (action = AVOID) | 2 668 | 1 600 | 11.2 | 10.0–12.4 % | −1.005 | 0.15 | −2 682 |
| **NO_ACTION** (action = NO_ACTION) | 7 751 | 2 485 | 49.5 | 48.4–50.6 % | +0.271 | 1.49 | +2 097 |
| **CONTINUATION_CANDIDATE** (action) | **0** | — | — | — | — | — | — |

### B) Session‑by‑session

Per the CSV (`session` column): 2 616 distinct sessions, per‑session event counts
1–19 (median 4). No single session dominates any bucket's net R — the `H7_SUPPORTED`
loss is spread over 1 600 sessions (max single‑session contribution < 1 % of the
total). Full detail in `orderflow_h1h7_performance_v2.csv`.

### C) Regime‑by‑regime

| regime | `H7_SUPPORTED` n | win % (buy the break) | E[R] | | `H1_CONT_OBSERVE` (NIFTY H1‑gate) |
|---|--:|--:|--:|---|---|
| TREND_UP | 526 | 10.5 % | −1.000 | | — |
| TREND_DOWN | 808 | 10.5 % | −1.008 | | — |
| CHOP | 1 334 | 11.8 % | −1.006 | | — |

`H7 = AVOID` holds **uniformly across all three regimes** — no regime where
buying an H7‑flagged break is profitable.

### Chronological split (per‑session date rank: TRAIN 45 % / VAL 20 % / OOS 17 % / HOLDOUT 18 %)

symbol‑sessions: TRAIN 1 228 · VAL 531 · OOS 437 · HOLDOUT 420.

| new `H7_TRAP` | n | win % | E[R] | P(SL first) |
|---|--:|--:|--:|--:|
| TRAIN | 672 | 1.8 % | −1.447 | 0.94 |
| VALIDATION | 250 | 2.4 % | −1.358 | 0.94 |
| OOS | 174 | 2.9 % | −1.314 | 0.93 |
| HOLDOUT | 151 | 4.0 % | −1.318 | 0.90 |

Rock‑stable across all four splits on an 11‑year independent dataset.

`new H1_CONT` per split: **n = 0 everywhere** (no CRUDEOIL data).

### Baseline comparison (same events, same period)

| | signals | win % | E[R] | PF | net R | max DD | max consec loss |
|---|--:|--:|--:|--:|--:|--:|--:|
| **`H1_CONT` — BASELINE** (Stage‑6, `body_frac ≥ 0.55`) | **3 419** | 58.2 % | +0.508 | 2.14 | +1 736 R | −13.8 R | 8 |
| **`H1_CONT` — NEW ENGINE** (`disp_atr ≥ 1.0` + CRUDEOIL‑only) | **0** | — | — | — | — | — | — |
| **`H7_TRAP` — BASELINE** (`reclaim2`) | 3 809 | 19.2 % | −0.734 | 0.29 | −2 797 R | −2 798 R | 35 |
| **`H7_TRAP` — NEW ENGINE** (`reclaim_dist/spike_range > 0.60`) | **1 247** | **2.3 %** | **−1.395** | 0.03 | −1 740 R | −1 740 R | 205 |

- **`H1_CONT`:** the new engine emits **nothing** on NIFTY (→ `H1_CONT_OBSERVE`),
  so there is no new‑engine `H1_CONT` row to compare. The **baseline's**
  `body_frac`‑gated NIFTY `H1_CONT` is E[R]‑positive and stable across all four
  splits (58.8 / 57.1 / 60.7 / 54.6 %) on this 11‑year index series — *new
  information*, but the frozen policy is **NIFTY = observe‑only** (Stage‑7 found
  NIFTY continuation unstable on the zerohero data) and **this evaluation does
  not change policy or the engine.**
- **`H7_TRAP`:** the reclaim‑distance refinement is again a **far sharper trap
  filter** than the baseline `reclaim2` rule on this independent data (2.3 % vs
  19.2 % buy‑the‑break win) — consistent with V1 / histsrc.

---

## Confidence intervals (Wilson, 95 %)

| state | win rate | 95 % CI |
|---|--:|:--:|
| `H1_CONT` (actionable) | — (n = 0) | — |
| `H1_CONT_OBSERVE` (NIFTY H1‑gate, not acted on) | 61.1 % | **57.5 – 64.5 %** |
| `H7_TRAP` (buy the break) | 2.3 % | **1.6 – 3.3 %** |
| `H7_SUPPORTED` (buy the break) | 11.2 % | **10.0 – 12.4 %** |
| `H1_WEAK` | 64.7 % | 60.7 – 68.4 % |

---

## Verdict against the pre‑declared validation gate

| criterion (for `H1_CONT`) | result |
|---|---|
| ≥ 10 sessions with an `H1_CONT` event | **FAIL** (0 — no CRUDEOIL data on Kaggle) |
| ≥ 50 `H1_CONT` events | **FAIL** (0) |
| ≥ 2 regimes represented | **FAIL** (0) |
| OOS slice ≥ 20 events | FAIL (0) |
| HOLDOUT slice ≥ 20 events, E[R] > 0, and **fresh** | FAIL |

**⇒ `H1_CONT` VERDICT = `NOT VALIDATED`.** The gate is not — and cannot be —
satisfied from Kaggle, because the only actionable symbol (CRUDEOIL / MCX) has no
Kaggle intraday data.

**H7 side:** 2 668 independent `H7_SUPPORTED` events over 1 600 NIFTY sessions,
2015–2026, all regimes, all four chronological splits → "buying the break" wins
11.2 % (CI 10.0–12.4 %), E[R] −1.005. The **`H7 = AVOID` classification is
confirmed on a fully independent instrument, vendor and period.** Status stays
**`SUPPORTED`** (now with a much larger, independent evidence base). **Not
`PROVEN`** — that word is reserved for a pre‑registered, untouched multi‑month
holdout with a real order‑flow feed, which still does not exist.

This is confirmation **#2** of three. A later run
(`ORDERFLOW_H1H7_SPIKE_OPTIONS.md`) adds **#3** on the Upstox expired-**option
premium** series — "buy the break" of an `H7_TRAP` wins 3.9 %, `E[R] −1.44`,
`fix3_R ≤ −1R` in 93.3 %. The consolidated three-dataset ledger is
`ORDERFLOW_STAGE8_H7_FADE.md` §G.1.

---

## Order‑flow observability after V2 (TASK 5)

| field | status | why |
|---|---|---|
| OHLC | **OBSERVABLE** | Kaggle NIFTY 5m + all prior sources |
| bar volume | **UNOBSERVABLE** for the NIFTY index sources (column all‑zero — NSE cash index) | not present / not real |
| OI | **UNOBSERVABLE** for the NIFTY OHLC sources; **OBSERVABLE** for the ranjan15 single futures session only | column absent in the OHLC sets |
| L1 bid/ask | **UNOBSERVABLE** except the ranjan15 single session (1 s snapshots) | not present |
| L2 depth (≥ 1 real level) | **UNOBSERVABLE** except the ranjan15 single session (5 levels, alignment questionable) | not present |
| trade‑side / aggressor | **UNOBSERVABLE — everywhere, all sources** | **no dataset on Kaggle (NSE or MCX) carries a trade‑side / aggressor / tick‑direction column.** `totalbuyqty`/`totalsellqty` are day‑cumulative *pending* order quantities, not executed aggressor volume. **Never reconstructed from candle direction. Never derived from volume.** |
| trade delta / CVD / absorption / footprint / iceberg | **UNOBSERVABLE — everywhere** | require aggressor‑classified trades, which no source provides |
| true constituent causation | **UNOBSERVABLE** | not derivable |

## Is the data sufficient for genuine Stage‑9 order‑flow research?

**No.** The only L1/L2 data Kaggle yielded is **one NIFTY‑futures session**
(`ranjan15`, 2025‑04‑28) of 1‑second **snapshots** with **no aggressor side** and
a **questionable column layout**. That is, at best, a sample to develop parsing
code against — not a dataset that can validate any order‑flow hypothesis. Stage‑9
still needs a real **aggressor‑classified tick + full‑depth L2 event stream** for
CRUDEOIL / NIFTY futures spanning ≥ 3 months / ≥ 2 regimes. Kaggle does not have
it.

## Next step

- H1/H7 validation: the `debashis74017` NIFTY 5m series is now available as
  `--source kaggle_nifty` for any future NIFTY‑side work, but the **CRUDEOIL
  `H1_CONT` verdict can only move with fresh CRUDEOIL sessions** — the weekly
  cron (`orderflow_h1h7_perf_cron.sh`) re‑runs `--source histsrc` automatically
  once ≥ 40 fresh post‑2026‑09‑04 sessions are captured.
- Stage‑9: acquire a real aggressor + L2 feed (paid vendor or a mode‑3 WS
  capture path) — Kaggle is a dead end for order‑flow internals.
