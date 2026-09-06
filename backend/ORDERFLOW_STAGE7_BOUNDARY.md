# Order-Flow Stage-7 — The H1 / H7 Mathematical Boundary

**Status:** RESEARCH ONLY. No production code changed. No signal. No orders.
No weighted / AI score. Conservative language
(REJECTED / PROMISING / SUPPORTED / PROVEN).

**Method:** the boundary is derived from the Stage-6 causal event space
(`data/orderflow_stage6_events.csv`), enriched with reclaim-distance, reaction-
candle and timing variables. Comparison populations are defined by the
**causal antecedent** (never the outcome, never the threshold being tested):

- `ACCEPT-PATH` := `acc1 ∧ n1_agree ∧ ¬reclaim2`
- `RECLAIM-PATH` := `reclaim2`

Outcome = Stage-6 `state4` (`4A_CONT` / `4B_TRAP`), from the spike-close entry +
spike-extreme stop + realistic fill. Chronological
**TRAIN 45 % / VAL 20 % / OOS 17 % / FINAL HOLDOUT 18 %** split from Stage-6.
Script: `backend/scripts/orderflow_stage7.py` →
`data/orderflow_stage7_events.csv` (2 240 rows),
`data/orderflow_stage7_report_2026-09-06.txt`.

---

## 1. Exact H1 / H7 mathematical boundary

**The boundary is a RECLAIM DISTANCE, not reclaim yes/no.**

Let `reclaim_dist` = the largest amount, over bars `T+1 … T+3`, by which a
**completed close** sits back through the broken level `L`, normalised by the
spike range `rng = high(T0) − low(T0)`.

Continuation probability collapses **monotonically** in `reclaim_dist / rng`,
identically on NIFTY, NATGAS and CRUDE (and under `/ATR` normalisation):

| `reclaim_dist / rng` | P(continuation) | E[fixed-3R] | state |
|----------------------|:---------------:|:-----------:|-------|
| ≤ 0.28  | ~20–26 % | −0.3 … −0.7 R | **AMBIGUOUS** (a shallow poke, not a trap) |
| 0.28 – 0.60 | ~15–17 % | −0.6 … −1.0 R | leaning trap |
| 0.60 – 1.1  | ~2–3 %  | −1.1 … −1.6 R | **HARD TRAP** |
| > 1.1   | ~0 %    | −1.6 … −2.2 R | **HARD TRAP** |

**Knee ≈ 0.6 × the spike range.**  H7 (hard trap) ⇔
`reclaim_dist > ~0.6 · rng` (or ≈ 0.9 · ATR). Below the knee the event is
genuinely ambiguous — it is *not* a certain trap.

On the H1 side, no single variable cleanly separates `4A_CONT` from `4B_TRAP`
inside the ACCEPT-PATH — the best single separators have modest rank-AUC:
`reaction_range / spike_range` (0.64–0.69 on NIFTY/NATGAS),
`reclaim_dist/ATR` (0.56 CRUDE), `disp_atr` (≈ 0.54). **`body_frac` is not in
the top 4 for any symbol.**

---

## 2. Minimum required variables

Greedy forward selection on TRAIN, verified on VAL/OOS, inside the ACCEPT-PATH:

| symbol | smallest combination that improved TRAIN E and held on VAL/OOS |
|--------|---------------------------------------------------------------|
| NIFTY  | *(none beat the base)* |
| NATGAS | `disp_atr ≥ 1.0` **∧** `available_R ≥ 1.0` |
| CRUDE  | `disp_atr ≥ 1.0` **∧** `upper_wick_frac < 0.25` (for a long) |

`disp_atr = |close − open| / ATR` (body size in ATR units) recurs — it
subsumes `body_frac` and is the more informative form. `n1_agree` and
`body_frac ≥ 0.55` were **not** selected — inside the accept-path they add
little beyond `disp_atr`.

**Minimum viable variable set:** `range_pctile`, `L` (one structural level),
`reclaim_dist / rng`, `disp_atr`, `available_R`. Five numbers, all from
completed candles.

---

## 3. Thresholds discovered (no holdout leakage)

| quantity | threshold | basis |
|----------|-----------|-------|
| abnormal spike | `range_pctile ≥ 0.90` | Stage-1/3/5/6 stability analysis |
| **hard-trap boundary** | `reclaim_dist > 0.6 · rng` (≈ `0.9 · ATR`) | **this stage**, monotone on TRAIN, holds VAL/OOS + spent holdout |
| acceptance | `acc2` (2 completed closes hold beyond `L`) | Stage-2/5/6 |
| displacement | `disp_atr ≥ 1.0` | greedy selection on TRAIN |
| available-R gate | `available_R ≥ 1.0` | interaction test (§5) |
| body_frac | **not a knife-edge** — the 0.50–0.60 band behaves alike; 0.55 (Stage-6) sits inside a stable region but is not special | §3 grid |

The FINAL HOLDOUT was **not** used to choose any of these.

---

## 4. Symbol-specific differences

| effect | classification |
|--------|----------------|
| **H7 hard-trap boundary** (`reclaim_dist > 0.6·rng` ⇒ ~0 % continuation, strongly −E) | **UNIVERSAL MECHANISM** — same shape and knee on all three symbols and on the spent holdout for NATGAS/CRUDE |
| **H1_CONT positive expectancy on the underlying** | **SYMBOL-SPECIFIC** — CRUDE only (E +0.36…+0.53R every split incl. holdout); NIFTY unstable; NATGAS holdout ≈ 0 |
| **NATGAS median R ≈ 0.6 pt** | **DATA / INSTRUMENT-QUALITY effect** — a 0.6-pt structural stop is below realistic slippage, so NATGAS R-multiples are inflated and not tradable as measured; it is not a market difference |
| NIFTY: only ~40 ACCEPT-PATH events over 35 sessions | **DATA effect** — NIFTY rarely produces a range-P90 spike that also breaks a level; sample too thin for a stable H1 read |

---

## 5. Available-R interaction — directional edge **× room**, not pure direction

H1_CONT region (`acc2 ∧ n1 ∧ body_frac ≥ 0.55 ∧ ¬reclaim2`), by
`available_R` (pooled non-holdout / OOS / **HOLDOUT**):

| symbol | `avR ≥ 0` | `avR ≥ 1.0` | `avR ≥ 2.0` |
|--------|:---------:|:-----------:|:-----------:|
| CRUDE  | +0.49 / +0.36 / **+0.49** | +0.35 / +0.70 / **+0.65** | +0.11 / +0.36 / +0.31 |
| NATGAS | +0.06 / +0.54 / **−0.16** | +0.23 / +0.59 / **+0.09** | −0.09 / −0.64 / −0.11 |
| NIFTY  | (n too small below avR≥1) | −0.40 (n7) | +1.14 (n1) |

`available_R ≥ 1.0` **flips NATGAS H1_CONT from holdout-negative (−0.16R) to
holdout ≈ breakeven (+0.09R)** and keeps CRUDE holdout-positive (+0.65R).
`avR ≥ 2.0` is *worse* (over-filters). **The robust H1 edge is
"directional × sufficient structural room (avR ≥ 1)".** SUPPORTED as an
interaction; the `avR ≥ 1` gate is the operative one.

---

## 6. Earliest valid entry

From Stage-5/6 and confirmed here: the accept-path is only meaningful once a
2-candle acceptance has formed (bar `T+2`). The 1-candle acceptance
(`acc1`) is noisier (`disp_atr`/`reaction_agrees` do most of the work).
**Earliest valid H1 entry ≈ bar `T+2` close**, entry-to-stop `R` ≈ the spike
range (≈ 20–35 index pts on NIFTY/CRUDE; ≈ 0.6 pt on NATGAS — untradable).

---

## 7. Earliest reliable trap detection

**Bar `T+1`.**  If the *first* completed post-spike close is back through `L`
by more than ~0.6 × the spike range → hard trap, ~0–3 % continuation,
E ≈ −1.3 to −1.7R (long-side). Even a `T+1` close back through `L` by *any*
amount (`reclaim1`) already gives P(trap) ≈ 79–89 % (Stage-6 §5). The
graded-distance form here removes the shallow-poke false positives.

---

## 8. Structural stop

Stage-6 §6 comparison stands: spike-extreme / 1·ATR are the causally honest
choices; the reaction-candle stop inflates R-multiples via a tiny R; **no
stop produced a 5R/8R tail** — the "small SL → large-R" thesis is REJECTED.
For the H1 read, `1·ATR` from the entry (`E_vol`) is the least-biased.

---

## 9–10. Expected continuation / trap probability

| region (deterministic) | P(continuation) | P(trap) | E[fixed-3R], underlying, realistic stop |
|------------------------|:---------------:|:-------:|:--------------------------------------:|
| **H7_TRAP** (`reclaim_dist > 0.6·rng`) | ~0–3 % | ~90–100 % | NATGAS −1.3R, CRUDE −0.9R (holdout −1.34 / −0.53) |
| shallow reclaim (`≤ 0.28·rng`) | ~20–26 % | ~70–80 % | −0.3 to −0.7R → **AMBIGUOUS** |
| **H1_CONT** (`acc2 ∧ n1 ∧ disp_atr ≥ 1 ∧ avR ≥ 1`) | ~40–50 % | ~50–55 % | **CRUDE +0.35…+0.70R**; NATGAS ≈ 0; NIFTY n/a |

Note the H1_CONT trap rate is **still ~50 %** — it is *less bad*, not a clean
winner.

---

## 11. OOS + holdout performance

| | H1_CONT E[fixed-3R]  train / val / OOS / **HOLDOUT** | H7_TRAP E[fixed-3R] train / val / OOS / **HOLDOUT** |
|--|:--:|:--:|
| NIFTY  | +0.07 / −0.73 / +0.21 / +1.28 (n9) — **unstable** | −1.56 / −1.37 / −0.61 / +0.01 (n7) |
| NATGAS | +0.04 / −0.47 / +0.54 / −0.16 — **fails holdout** | −1.30 / −1.47 / −1.22 / **−1.34** |
| CRUDE  | **+0.53 / +0.47 / +0.36 / +0.49** — holds | −0.89 / −0.86 / −1.12 / **−0.53** |

---

## 12. New holdout

**INSUFFICIENT NEW HOLDOUT DATA.** No sessions exist after 2026-09-04. The
Stage-6 18 % final-holdout slice has already been scored once; it is shown
above for reference and was **not** used to choose any threshold in Stage-7.
Repeat the confirmation on a genuinely new multi-month period before any of
this moves past PROMISING.

---

## 13. SUPPORTED

- **The reclaim-distance boundary.** `reclaim_dist > ~0.6 × spike range` ⇒
  continuation probability ~0 %, E ≈ −1.3 to −2.2R. Universal (all three
  symbols), monotone, causal (known by bar `T+1…T+3`), and strongly negative
  on train/val/OOS **and** the spent holdout for NATGAS/CRUDE. This is a
  reliable *"do not buy / candidate fade"* mechanism.
- **`available_R ≥ 1.0` as an interaction gate** — turns NATGAS H1_CONT
  holdout-non-negative and keeps CRUDE holdout-positive.

## 14. PROMISING

- **H1_CONT as a small positive-expectancy long on the underlying — CRUDE
  only** (+0.36…+0.53R every split incl. holdout; drawdown −15R; P5R ≈ 1 %).
  Requires `avR ≥ 1`. Not universal.

## 15. REJECTED

- `body_frac` as a strong discriminator (never selected by the greedy search;
  `disp_atr ≥ 1.0` is the better form and is still only weak, sep-AUC 0.53–0.69).
- A single **universal** `body_frac` / `available_R` threshold across symbols.
- H1_CONT on **NIFTY** (unstable across splits) and **NATGAS** (holdout
  negative; R ≈ 0.6 pt is sub-slippage — instrument-quality, not market).
- The **"small SL → large-R"** thesis — no 5R/8R tail with any causal stop.
- Every extra feature from Stages 4–6 (Market-Profile location / POC
  migration, prior-bar compression, price rotation, VWAP-proxy distance,
  day-regime, OI, the option-volume "imbalance" proxy).

## 16. UNOBSERVABLE (no proxy presented as order flow)

- true buyer / seller aggression, lifting-the-offer / hitting-the-bid, delta,
  cumulative delta, footprint / price-level transaction distribution,
  depth-of-book imbalance, liquidity added / pulled, absorption, iceberg /
  large-participant detection, constituent-stock → index causation.
- Required to make these testable: an underlying **tick stream with aggressor
  side**, **full depth-of-book** snapshots, a **single-stock tick feed** for
  the top ~15 constituents, and ≥ 100 independent sessions across a full
  volatility cycle with a genuinely untouched multi-month holdout.

---

## Application translation (explainable, no score)

Every completed 5-minute bar, per symbol, one row:

```
ABNORMAL SPIKE      range_pctile = 0.94  (≥ 0.90)                 [S1]
   ↓
BROKEN LEVEL        L = 24030 (prev swing high); dist_L = +8 pt   [S2]
   ↓
BODY               |c−o|/rng = 0.71 ;  disp_atr = 1.4
   ↓
REACTION (T+1)      dir = UP  agrees = yes  range = 0.8·ATR
   ↓
ACCEPTANCE         acc1 = 1  acc2 = 1  acc3 = 0
   ↓
RECLAIM            reclaim2 = no ;  reclaim_dist/rng = 0.00
   ↓
AVAILABLE_R        |entry − next barrier| / R = 1.6
   ↓
STATE  →  H1_CONT       (all: acc2 ∧ agrees ∧ disp_atr≥1 ∧ avR≥1 ∧ reclaim_dist/rng ≤ 0.6)
```

**States and their literal reason strings**

| state | condition (by bar `T+2` / `T+3`) | example reason |
|-------|----------------------------------|----------------|
| **H7_TRAP** | `reclaim_dist / rng > 0.6` | `"reclaim 24012 is 0.9·rng back through L 24030 → hard trap"` |
| **H1_CONT** | `acc2 ∧ reaction_agrees ∧ disp_atr ≥ 1.0 ∧ available_R ≥ 1.0 ∧ reclaim_dist/rng ≤ 0.6` | `"acc2 held, reaction up, disp 1.4 ATR, room 1.6 R → participation"` |
| **H1_WEAK** | `acc1 ∧ reaction_agrees` but a gate fails | `"acc1 only, room 0.7 R < 1.0 → weak"` |
| **AMBIGUOUS** | shallow reclaim (`≤ 0.28·rng`) or none of the above | `"reclaim 0.2·rng — ambiguous, no action"` |

This engine is a **research / explanation tool**. It is wired to no order
path, emits no buy/sell signal, and computes no score. Its practical output on
this data: *fade / avoid the hard-trap side (SUPPORTED, universal); treat H1
as a low-conviction CRUDE-only continuation candidate (PROMISING); do nothing
on AMBIGUOUS.*

---

## Final statement

The market mechanism — **abnormal displacement → level break → acceptance vs
*graded* reclaim → participation vs trap** — **is real, causal, and monotone
in the reclaim distance.** The tradable part is (a) a reliable "do-not-buy /
candidate-fade" on the hard-trap side (SUPPORTED, universal) and (b) a small
CRUDE-only continuation edge on the underlying (PROMISING). It does **not**
survive ATM option spread + theta (Stage-4) and is marginal on the underlying
with a realistic stop. **Nothing is PROVEN.** Research only; no production
change, no signal, no orders, no score.

```
cd backend && python scripts/orderflow_stage7.py   # needs /root/oi_dashboard/oi_history.db (read-only)
```
