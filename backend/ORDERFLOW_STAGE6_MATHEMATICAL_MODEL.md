# Order-Flow Stage-6 — Causal Market-State-Machine Reconstruction

**Status:** RESEARCH ONLY. No production code changed. No signal enabled. No
orders. No weighted / AI score. Conservative language throughout
(REJECTED / PROMISING / SUPPORTED / PROVEN).

**Scope:** independently reverse-engineer the market mechanics behind the
H1 (genuine participation) vs H7 (trap / reversal) separation found in
Stages 1–5, and express it as the simplest deterministic causal state
machine that survives a chronological **TRAIN / VALIDATION / OOS / FINAL
HOLDOUT** split.

**Data:** `orderflow_histsrc` — `/root/oi_dashboard/oi_history.db` (read-only)
+ zerohero histcap. 2026-07-13 … 09-04. NIFTY 132 spike events / 35 sessions,
NATURALGAS 1141 / 35, CRUDEOIL 967 / 38; 3 regimes each.
Script: `backend/scripts/orderflow_stage6.py` →
`data/orderflow_stage6_events.csv` (2240 rows),
`data/orderflow_stage6_report_2026-09-06.txt`.

**Chronological split (per symbol, by session date):** train 45 % · validation
20 % · OOS 17 % · **FINAL HOLDOUT 18 % — evaluated once, after the model was
frozen; no threshold was chosen on it.**

---

## 1. The market state machine

```
STATE 0  NORMAL
   │   range percentile of the completed bar < P90 (symbol-adaptive)
   ▼
STATE 1  ABNORMAL SPIKE            (bar T0)
   │   pct(T0) := rank of (high−low) among all PRIOR-session completed bars ≥ 0.90
   ▼
STATE 2  BROKEN LEVEL              (bar T0)
   │   ∃ prior structural level L with  high(T0) > L  (LONG)  |  low(T0) < L  (SHORT)
   │   L := max{prev-bar high, nearest prior swing high, developing VAH, session high}  (LONG; mirror for SHORT)
   ▼
STATE 3  ACCEPTANCE vs FAILURE     (bars T+1 … T+k, k∈{1,2,3})
   ├── 3A ACCEPTANCE(k):  close(T0) beyond L  AND  close(T+j) beyond L for all j=1..k
   └── 3B FAILED / RECLAIM(k): ∃ j≤k with close(T+j) back through L
   ▼
STATE 4  OUTCOME  (NOT known at entry — used only to label the training data)
   ├── 4A CONTINUATION:  MFE_R ≥ 2.0  AND  the trade did not stop out
   └── 4B TRAP/REVERSAL: MAE_R ≤ −1.0  OR  stopped out before reaching +1R
   (4X AMBIGUOUS otherwise)
```

**Derived labels** (definitions frozen from Stage-5, used to build the
training set — not the live classifier):

- **H1 GENUINE PARTICIPATION** := `acc1  ∧  n1_agree  ∧  body_frac ≥ 0.55  ∧  ¬reclaim2`
- **H7 TRAP / REVERSAL**       := `reclaim3  ∧  (MAE_R ≤ −1.0  ∨  state4 = 4B)`

Observed shares (non-holdout): H1 ≈ **26–32 %** of abnormal spikes, H7 ≈
**34–44 %**. The two are near-disjoint and together cover ~60–75 % of events;
the rest are ambiguous.

---

## 2. Exact equations (all causal; every input is a completed candle ≤ the
decision bar)

Let bar `T0` be the spike, `rng = high−low`, `o, c` its open/close, `ATR` =
14-bar ATR through `T0`, `base = median prior-bar range`.

| # | quantity | equation |
|---|----------|----------|
| 1 | **spike test** | `spike := pctile(rng among prior-session bar ranges) ≥ 0.90` |
| 2 | **body strength** | `body_frac := |c − o| / rng` ; "strong" ⇔ `body_frac ≥ 0.55` |
| 3 | **wick fractions** | `uw_frac := (high − max(o,c))/rng` ; `lw_frac := (min(o,c) − low)/rng` |
| 4 | **displacement** | `disp_atr := |c − o| / ATR` |
| 5 | **broken level** | `L` as defined in §1; `dist_L := (c − L)` for LONG (signed; `>0` ⇒ closed beyond) |
| 6 | **n1 agreement** | `n1_agree := sign(close(T+1) − open(T+1)) == trade_direction` |
| 7 | **acceptance(k)** | `acc_k := (dist_L > 0) ∧ ⋀_{j=1..k} [close(T+j) beyond L]` |
| 8 | **reclaim(k)** | `reclaim_k := ∃ j≤k : close(T+j) back through L` ; `reclaim_dist_atr := max_j(back-through distance)/ATR` |
| 9 | **structural stop** | see §6 — reference = spike extreme ± `0.03·rng` |
| 10 | **R** | `R := |entry − stop|` (underlying points) |
| 11 | **available_R** | `avail_R := |entry − L_next| / R`, `L_next` = nearest opposing structural barrier known at entry |
| 12 | **MFE_R / MAE_R** | favourable / adverse excursion since entry, ÷ R, **capped at the invalidation bar** |
| 13 | **realised fixed-3R** | `+3 − tick/R` if `high ≥ entry+3R` first; else on stop, exit at the **breaching bar's extreme ± 1 tick** → `(fill − entry)/R` |

### The classifier — a deterministic decision list (the Stage-6 model)

Uses only information available by **bar T+2**:

```
if reclaim2:                                  →  H7_TRAP     (fade / do-not-buy)
elif acc2 ∧ n1_agree ∧ body_frac ≥ 0.55:      →  H1_CONT     (continuation candidate)
elif acc1 ∧ n1_agree:                          →  H1_WEAK     (weak candidate)
else:                                          →  AMBIG       (no action)
```

A logistic `P(continuation) = σ(b + Σ wᵢ·zᵢ)` on the same five core features
(`range_pctile, body_frac, n1_agree, acc2, reclaim2`, standardised) was fitted
on TRAIN and evaluated OOS: its AUC and its `P ≥ 0.60` subset did **not** beat
the decision list, so the deterministic list — which needs no weights — is the
model of record.

---

## 3. Results by split (decision list, spike-close entry, spike-extreme stop,
realistic fill)

### H1_CONT  — `E[fixed-3R]` per trade

| symbol | train | validation | OOS | **FINAL HOLDOUT** | verdict |
|--------|:-----:|:----------:|:---:|:-----------------:|---------|
| NIFTY  | +0.07 (n14) | **−0.73** (n11) | +0.21 (n10) | +1.28 (n9) | **UNSTABLE** (val negative, tiny n) |
| NATGAS | +0.04 (n129) | **−0.47** (n49) | +0.54 (n58) | **−0.16** (n47) | **REJECTED** (negative on val AND holdout) |
| CRUDE  | **+0.53** (n155) | **+0.47** (n47) | **+0.36** (n44) | **+0.49** (n44) | **PROMISING** (positive on all 4 incl. holdout) |

Pooled non-holdout: NIFTY E −0.14 / cont 37 % / trap 54 %; NATGAS E +0.06 /
36 % / 58 %; CRUDE E +0.49 / 44 % / 53 %. **Note H1_CONT trap rate is still
> 50 % on every symbol** — it is *less bad*, not a clean winner.

### H7_TRAP  — `E[fixed-3R]` long-side (should stay strongly negative)

| symbol | train | validation | OOS | **FINAL HOLDOUT** |
|--------|:-----:|:----------:|:---:|:-----------------:|
| NIFTY  | −1.56 | −1.37 | −0.61 | +0.01 (n7 — noise) |
| NATGAS | **−1.30** | **−1.47** | **−1.22** | **−1.34** |
| CRUDE  | **−0.89** | **−0.86** | **−1.12** | **−0.53** |

**NATGAS and CRUDE H7_TRAP stays strongly negative on every split including
the untouched holdout.** H7_TRAP: ~8–12 % continuation, ~84–88 % trap,
~40 % of all abnormal spikes.

### The H7 trap detector, early form (Part 5)

`reclaim_N` = "broke L then a close back through it within N bars":

| N | fires (% of events) | P(trap/reversal) | continuation % | known at |
|---|:-------------------:|:----------------:|:--------------:|:--------:|
| 1 | 25–29 % | **79–89 %** | 10–12 % | bar T+1 |
| 2 | 34–42 % | 84–88 % | 8–12 % | bar T+2 |

**At bar T+1 you can flag ~1 in 4 abnormal spikes as ~85 % likely to
reverse.** This is the strongest, earliest, most robust result.

### Available-R gate (Part 7)

Within H1_CONT, requiring `avail_R ≥ 1.0`:

| symbol | n | E[fixed-3R] | P3R |
|--------|--:|:-----------:|:---:|
| NIFTY  | 7  | −0.40 (too few) | 29 % |
| NATGAS | 79 | **+0.23** | 34 % |
| CRUDE  | 72 | **+0.35** | 40 % |

The gate lifts NATGAS/CRUDE H1_CONT into positive territory — **PROMISING** as
a hard filter.

### Structural stop (Part 6)

Compared spike-extreme / reaction-candle / broken-level / max(spike,reaction)
/ 1·ATR, all from the **spike-close entry**. The reaction-candle stop gives
the highest P3R but a tiny R (Stage-5 already flagged this inflates
R-multiples); the **1·ATR volatility stop** is the least-bad honest choice
(NIFTY E −0.14 vs −0.53 for the spike stop). No stop produced a 5R/8R tail —
**the "small SL + LARGE R" thesis is REJECTED** (P5R ≈ 0–1 %, P8R ≈ 0 %).

### Incremental features (Part 2 — within H1_CONT, does it add OOS E?)

| feature | verdict |
|---------|---------|
| Market-Profile location, POC migration | INSUFFICIENT / REJECTED |
| prior-8-bar range contraction | **REJECTED** (ΔE ≈ 0 — compression adds nothing; confirms Stages 3–5) |
| prior-8-bar rotations ≥ 4 | INSUFFICIENT / REJECTED |
| VWAP-proxy distance | **REJECTED** (ΔE negative on all) |
| day-regime (TRENDING/OPENING_DRIVE) | **REJECTED** (no OOS) |
| range percentile ≥ 0.97, disp_atr ≥ 1.5 | INSUFFICIENT |
| OI / option-volume "imbalance" | **REJECTED** (Stage-4) / UNOBSERVABLE on the underlying |

**No extra variable adds stable OOS expectancy inside the core state.**

---

## 4. Public order-flow concept → measurable variable → test result

| public concept | our mathematical interpretation | measurable variable | result |
|----------------|--------------------------------|---------------------|--------|
| abnormal price expansion / "price blast" | range percentile vs prior bars | `range_pctile` (§2.1) | **SUPPORTED** as the trigger; symbol-adaptive P90 is stable |
| acceptance / rejection | close holds beyond / reclaims the broken level | `acc_k`, `reclaim_k` (§2.7–8) | **SUPPORTED** as the continuation/trap separator |
| continuation | favourable follow-through vs invalidation | `state4 = 4A` | classifier target |
| trap / reversal / "trapped buyers/sellers" | break → no acceptance → reclaim → opposite | H7 / `reclaim_N` detector | **SUPPORTED** (NATGAS/CRUDE, incl. holdout) |
| buyer vs seller aggression, "lifting offer / hitting bid" | signed aggressor volume / trade-at-ask vs trade-at-bid | — | **UNOBSERVABLE** — no aggressor-classified trade stream. No proxy invented. |
| delta / cumulative delta | Σ(buy−sell) volume | — | **UNOBSERVABLE** on the underlying |
| liquidity structure / depth | resting bid/ask size by level | — | **UNOBSERVABLE** (underlying); option book only, 4 Sep sessions |
| imbalance / "200 % imbalance" | 2:1 one-sided quantity | option CE/PE interval traded-volume ratio (L3 proxy) | **REJECTED** — no incremental OOS value (Stage-4) |
| price rotation | direction-change count in a window | `bal_rotations` | **REJECTED** — negative OOS |
| smart money / large-participant accumulation | sustained hidden one-sided pressure | iceberg / delta-persistence detection | **UNOBSERVABLE** — **not claimed** |
| market profile / value / POC | developing time-price value area | `pf_loc`, POC migration | **REJECTED** as an incremental filter |

---

## 5. Final answers (A – H)

**A. What is mathematically SUPPORTED**
- The **H1/H7 state-machine separation** as a *classifier* of continuation vs
  trap: `reclaim2 ⇒ H7_TRAP` (8–12 % continuation, 84–88 % trap) and
  `acc2 ∧ n1_agree ∧ body_frac ≥ 0.55 ⇒ H1_CONT` (36–44 % continuation).
  Reproduced per symbol on train + validation + OOS **and on the untouched
  final holdout**.
- The **early H7 trap detector** (`reclaim1`, known at bar T+1): ~85 % trap
  probability, negative expectancy on the holdout for NATGAS and CRUDE.
- **Range-percentile (P90) as the spike definition** — stable across sessions
  and regimes, symbol-adaptive.

**B. What is only PROMISING**
- **H1_CONT as a positive-expectancy long entry on the underlying:** CRUDE
  only — E[fixed-3R] +0.36 … +0.53R on all four splits including the holdout,
  n ≈ 246 + 44 over 31 + sessions. The edge is small; P5R ≈ 1 %; drawdown
  −15R; it is **one symbol**, not a universal rule.
- **The `avail_R ≥ 1.0` gate** — lifts NATGAS/CRUDE H1_CONT to +0.23 / +0.35R.

**C. What is REJECTED**
- H1_CONT on **NATGAS** (negative on validation **and** the final holdout) and
  **NIFTY** (unstable — validation −0.73R).
- The **"small structural SL → large-R continuation"** thesis — with any
  causally-valid stop the MFE distribution has no 5R/8R tail.
- Every **extra feature**: Market-Profile location / POC migration, prior-bar
  compression, price rotation, VWAP-proxy distance, day-regime, OI, the
  option-volume imbalance proxy. None add stable OOS expectancy.

**D. What remains UNOBSERVABLE** (no proxy invented)
- true aggressor delta / cumulative delta; footprint / price-level transaction
  distribution; bid/ask depth imbalance on the underlying; liquidity
  pulled/added; absorption; iceberg / large-participant detection;
  constituent-stock → index causation (no single-stock feed; index-vs-index
  5m lead/lag is **contemporaneous**, corr(0) ≈ 0.80, lead/lag corr ≈ −0.05).

**E. Exact mathematical model** — §2 (the deterministic decision list + the
equations 1–13). No weights, no score.

**F. Exact application state machine** — §6 below.

**G. Required additional data** (to make the UNOBSERVABLE parts testable)
- underlying **tick stream with aggressor side** (trade price vs bid/ask) →
  delta, cumulative delta, lift/hit;
- underlying **full depth-of-book** snapshots → depth imbalance, liquidity
  add/pull, absorption;
- **single-stock 1-minute / tick feed** for the top ~15 index constituents →
  stock-lead-index causation;
- **≥ 100 independent sessions** across a full volatility cycle, and a
  genuinely untouched multi-month holdout.

**H. Final-holdout status** — the 18 % final-holdout slice was scored **once**,
after freezing the model. Results are in §3 (HOLDOUT column). It **confirms**:
H7_TRAP stays negative (NATGAS −1.34R, CRUDE −0.53R); H1_CONT holds for CRUDE
(+0.49R) and fails for NATGAS (−0.16R). **Nothing is PROVEN** — a single 18 %
slice on correlated intraday events (effective independent N ≈ the session
count) is not sufficient, and the per-trade edge where it exists is small.

---

## 6. Application state machine (blueprint — explainable bar by bar)

Every completed 5-minute bar, per symbol, the engine emits one row:

```
{timestamp, symbol, price, state, level_L, values{...}, reason, invalidity}
```

| state | entered when (causal) | values shown | invalidity condition |
|-------|----------------------|--------------|---------------------|
| **S0 NORMAL** | `range_pctile(bar) < 0.90` | `range_pctile`, ATR, `base` | — |
| **S1 ABNORMAL SPIKE** | `range_pctile ≥ 0.90` | `range_x`, `body_frac`, `uw_frac`, `lw_frac`, `disp_atr`, direction | next bar closes back inside the spike range without a level break |
| **S2 BROKEN LEVEL** | spike high/low displaced `L` | `L`, `dist_L`, `dist_L_atr` | close never held beyond `L` (→ straight to S3B) |
| **S3A ACCEPTANCE** | `acc2` (2 closes beyond `L`) `∧ n1_agree` | `acc1/acc2`, `n1_agree`, `body_frac` | `reclaim3` fires later → downgrade to S3B/H7 |
| **S3B FAILED / RECLAIM** | `reclaim1` or `reclaim2` | `reclaim_dist_atr`, bar index | a later 2-close acceptance re-forms above `L` |
| **H1 PARTICIPATION** | S3A `∧ body_frac ≥ 0.55 ∧ ¬reclaim2` | `avail_R` (with 1·ATR stop), `R_pts`, entry, stop | close back through `L`, or MAE reaches −1R |
| **H7 TRAP** | `reclaim2` (or `reclaim1`, earlier) | `reclaim_dist_atr`, expected reversal side | price closes beyond `L` again and holds 2 bars |
| **AVAILABLE-R** | on H1 | `avail_R = |entry − L_next| / R`; **gate: proceed only if `avail_R ≥ 1.0`** | `avail_R < 1.0` → mark "insufficient room", no action |
| **STRUCTURAL STOP** | on H1 | `stop = entry − 1·ATR` (LONG); `R_pts` | — |
| **TARGET SPACE** | on H1 | `L_next`, distance in R; expectancy is `+E` only for CRUDE in this study | — |
| **4A CONTINUATION / 4B REVERSAL** | outcome, logged post-hoc | realised `R`, MFE_R, MAE_R, exit kind | — |

`reason` strings are literal: e.g. `"S1: range pctile 0.94 ≥ 0.90"`,
`"H7: close 24012 back through L 24030 at T+1 (reclaim_dist 0.6 ATR)"`,
`"H1 blocked: avail_R 0.7 < 1.0 (next barrier VAH 24080, R 22 pt)"`.

**This engine is a research / explanation tool. It is not wired to any order
path, produces no buy/sell signal, and computes no score.** Its practical
output on this data is: *identify and avoid / fade H7 (SUPPORTED, NATGAS &
CRUDE); treat H1 as a low-conviction CRUDE-only continuation candidate
(PROMISING); do nothing on AMBIG.*

---

## 7. Reproduce

```
cd backend
python scripts/orderflow_stage6.py          # needs /root/oi_dashboard/oi_history.db (read-only)
# -> data/orderflow_stage6_events.csv  +  data/orderflow_stage6_report_2026-09-06.txt
```

Deferred: re-run with the H7 filter once **more sessions are captured** — the
final-holdout confirmation should be repeated on a genuinely new multi-month
period before any of this moves past PROMISING.
