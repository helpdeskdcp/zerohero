# Order-Flow Stage-8 — The H7 Fade-Candidate Detector + Backtest

**Status:** RESEARCH ONLY. No production code changed. No signal. No orders.
No weighted / AI score. Conservative language
(REJECTED / UNOBSERVABLE / PROMISING / SUPPORTED / PROVEN). UNDERLYING basis
only — no option-premium claim (Stage-4 showed an underlying edge does not
carry to ATM options).

**Task:** build a research-only detector for the H7 trap state (Stage-6/7:
`reclaim_dist / spike_range > ~0.6` ⇒ continuation ≈ 0 %, the spike-direction
side loses ~1.3–2.2 R) and **backtest fading it** — entering *opposite* the
spike after the reclaim boundary is crossed.

Script `backend/scripts/orderflow_stage8.py` →
`data/orderflow_stage8_events.csv` (1 369 rows),
`data/orderflow_stage8_report_2026-09-06.txt`. Built on the Stage-6/7 causal
event space (`collect` + `enrich`), ~35–38 sessions/symbol, 3 regimes,
chronological TRAIN 45 % / VAL 20 % / OOS 17 % / **HOLDOUT 18 % (scored once,
no threshold tuned on it)**.

---

## A. Sources researched

**None via the web** — this environment has no internet access, so no live
research of any specific course / boot-camp / interview / video was possible.
The concept map below is built from (1) the *generic, standard* meaning of
the order-flow terms and (2) our own Stage-1 → Stage-7 empirical findings.
Where a concept requires data we do not have, it is marked **UNOBSERVABLE**
and **no proxy is presented as the real thing**.

---

## B. Public concept → our model (§11 table)

| public concept | observed market behaviour (generic) | our variable | mathematical definition | causal timing | data available? | empirical result | status |
|----------------|-------------------------------------|--------------|-------------------------|---------------|-----------------|------------------|--------|
| price blast / displacement | an outsized bar vs its own recent range | `range_pctile`, `disp_atr` | rank of `(h−l)` among prior-session bars; `|c−o|/ATR` | bar T0 | **yes** | P90 spike def is stable, symbol-adaptive | **SUPPORTED** (as the trigger) |
| structural break / level interaction | the bar displaces a prior high/low/value edge | `L`, `dist_L` | `L = max{prev-bar high, prior swing high, developing VAH, session high}`; `dist_L = c − L` | bar T0 | **yes** | required for any H1/H7 label | **SUPPORTED** |
| acceptance | closes hold beyond the broken level | `acc_k` | `(c(T0) beyond L) ∧ ⋀_{j≤k}[c(T+j) beyond L]` | bar T+k close | **yes** | separates continuation from trap | **SUPPORTED** |
| reclaim / rejection | a close moves back through the level | `reclaim_dist` | `max_{j≤3}` back-through distance | bar T+1…T+3 | **yes** | **monotone P(cont) collapse; knee ≈ 0.6·spike range** | **SUPPORTED** (Stage-7) |
| balance vs imbalance | prior-bar range contraction then expansion | `bal_range_contraction` | prior-k span / (k·ATR) | bars[:T0] | yes | ΔE ≈ 0 — adds nothing (Stages 3–7) | **REJECTED** |
| price rotation | direction-change count in a window | `bal_rotations` | sign flips of Δclose over prior k | bars[:T0] | yes | negative incremental E | **REJECTED** |
| next-bar participation | the first reaction candle agrees with the spike | `n1_agree`, `reaction_agrees` | `sign(c(T+1)−o(T+1)) == dir` | bar T+1 close | yes | weak separator (sep-AUC ~0.53–0.69); used as a gate | **PROMISING** |
| buyer vs seller aggression | who is crossing the spread | signed aggressor volume | `Σ(buy − sell)` | per trade | **NO** | — | **UNOBSERVABLE** |
| lifting the offer / hitting the bid | trade printed at ask vs bid | aggressor side of each print | `count(px≥ask) − count(px≤bid)` | per trade | **NO** | — | **UNOBSERVABLE** |
| delta / cumulative delta | running signed aggressor volume | — | — | per bar | **NO** | — | **UNOBSERVABLE** |
| "R-delta" Buy/Sell 1/2 | a delta-relative ignition pattern | needs delta | — | — | **NO** | — | **UNOBSERVABLE** |
| Ignition Buy/Sell 1/2 | displacement + break + acceptance + follow-through (+ a volume/delta component) | our H1 state covers the price part; the volume/delta part | H1 = `acc2 ∧ n1_agree ∧ disp_atr≥1 ∧ avR≥1` (price); volume/delta = — | T+2 close (price part) | **partial** — price yes, volume/delta **no** | the price-only H1 is symbol-specific (CRUDE) and small | **PROMISING (price part) / UNOBSERVABLE (delta part)** |
| BMC / SMC setup | a liquidity-reaction / order-flow-internal pattern | best price proxy = the reclaim / acceptance state | see H7 (reclaim) / H1 (acceptance) | T+1…T+3 | **partial** | reclaim boundary SUPPORTED; internals not observable | **PROMISING (structure) / UNOBSERVABLE (internals)** |
| footprint / price-level distribution | volume traded at each price | — | — | per trade | **NO** (underlying feed has zero bar volume) | — | **UNOBSERVABLE** |
| depth imbalance / liquidity pulled-added | resting size by book level | — | — | per quote | **NO** | — | **UNOBSERVABLE** |
| smart-money trap / trapped participants | break → no acceptance → reclaim → opposite | **H7 state + the reclaim-distance boundary** | `reclaim_dist / spike_range > X*` | T+1…T+3 | **yes** | boundary SUPPORTED as a **classifier**; **fading it is REJECTED** (see E) | **SUPPORTED (detector) / REJECTED (as a trade)** |

---

## C. Mathematical model (deterministic, causal, no score)

```
S1  ABNORMAL SPIKE   :  pctile(rng(T0) among prior-session bar ranges) ≥ 0.90
S2  BROKEN LEVEL L   :  high(T0) > L  (LONG)  |  low(T0) < L  (SHORT)
                        rng := high(T0) − low(T0)      (≈ 0.9 · ATR on average)
DETECT (T+1..T+3)    :  D_reclaim(j) := (L − close(T+j))    for a LONG spike   (mirror for SHORT)
                        X := max_{j≤3} D_reclaim(j) / rng
BOUNDARY            :   X > X*   ⇒   H7  (hard trap; the spike-direction side loses ~1.3–2.2 R)
                        X ≤ 0.28 ⇒   AMBIGUOUS (shallow poke; ~20–26 % continuation)
```

`X*` re-estimated on TRAIN in this stage: **no value on the pre-declared grid
{0.20 … 1.10} produced a stable positive *fade* expectancy on train+val+oos
for any symbol** (see E). The Stage-7 classifier knee (`X ≈ 0.6`) stands as
the *avoidance* boundary; it is **not** a fade trigger.

Alternative normalisations tested: `reclaim_dist / ATR` gives the same
monotone shape (`spike_range ≈ 0.9·ATR`, so the two are near-collinear); a
dimensionless combination did not out-separate `reclaim_dist / spike_range`.

---

## D. Variables + equations (with exact bar availability)

| variable | equation | available at |
|----------|----------|--------------|
| `range_pctile` | rank of `(h−l)` among all prior-session completed bars | T0 close |
| `spike_range` `rng` | `high(T0) − low(T0)` | T0 close |
| `atr` | 14-bar ATR through T0 | T0 close |
| `spike_over_atr` | `rng / ATR` | T0 close |
| `L` | nearest prior structural high/low the bar displaced | T0 close |
| `disp_atr` | `|c(T0) − o(T0)| / ATR` | T0 close |
| `acc_k` | k consecutive closes hold beyond `L` | T+k close |
| `D_reclaim(j)` | how far `close(T+j)` sits back through `L` | T+j close |
| `X` | `max_{j≤3} D_reclaim(j) / rng` | T+3 close (or earlier if the knee is crossed sooner) |
| `time_to_reclaim` | first `j` with a close back through `L` | that bar's close |
| `reaction_agrees` | `sign(c(T+1)−o(T+1)) == spike_dir` | T+1 close |
| `available_R` | `|entry − next opposing barrier| / R` | entry bar close |
| fade entry | close of the bar where `X > X*` first holds (`entry_offset 0`) or the next bar (`offset 1`) | detect bar / detect+1 close |
| fade stop | **beyond the spike extreme** ± `0.03·rng` (the structural "the trap failed" invalidation) | at entry |
| fade fill on stop | breaching bar's extreme ∓ 1 futures tick (adverse) | — |

---

## E. Stage-8 results — the fade

### §8 boundary sweep (fade E2R, entry = detect-bar close, stop beyond spike extreme)

Across the full pre-declared grid `X ∈ {0.20 … 1.10}`, **no threshold gave
`E2R > 0` on TRAIN *and* VALIDATION *and* OOS for any symbol.** The validation
split is negative for essentially every `X` on NATGAS and CRUDE.

### §F Train / Validation / OOS / Holdout at `X > 0.60` (default; not tuned)

| symbol | E2R train | E2R val | E2R OOS | **E2R HOLDOUT** | pooled non-holdout E2R |
|--------|:---------:|:-------:|:-------:|:---------------:|:----------------------:|
| NIFTY  | n = 0 | +0.37 (n2) | n = 0 | n = 0 | (no sample at X>0.6) |
| NATGAS | **−0.27** (n20) | **−1.30** (n7) | **−1.03** (n11) | **−0.82** (n9) | −0.70 |
| CRUDE  | **−0.20** (n20) | **−0.44** (n6) | +0.00 (n11) | +0.40 (n9) | −0.18 |

- **NATGAS: negative on all four splits** — decisive.
- **CRUDE: negative on train + validation**, breakeven OOS, positive only on
  the n = 9 holdout — not a stable edge; pooled non-holdout E2R = −0.18.
- **NIFTY: no usable sample** at `X > 0.6` (abnormal-spike + level-break +
  large-reclaim + acc1 is rare on NIFTY).

### §9 MFE / target distribution (pooled non-holdout, `X > 0.6`)

| symbol | median MFE_R | P(MFE ≥ 2R) | P(MFE ≥ 3R) | P(MFE ≥ 5R) | E[1R] | E[2R] | E[3R] |
|--------|:-----------:|:-----------:|:-----------:|:-----------:|:-----:|:-----:|:-----:|
| NATGAS | 0.98 | 15 % | 6 % | **0 %** | −0.33 | −0.68 | −0.62 |
| CRUDE  | 0.92 | 27 % | 19 % | **0 %** | −0.21 | −0.18 | −0.10 |

median MAE_R ≈ **−1.0 to −1.2** — the fade is stopped at ~1 R routinely. **No
5R/8R tail on the fade side either.** Entry-timing / stop-definition variants:
the reaction-candle stop at `entry_offset 1` is catastrophic
(CRUDE E2R −2.18); the spike-extreme stop at the earliest entry is the least
bad and still negative.

### Why it fails (mechanism)

A trap **is not a clean reversal**. After a large reclaim, price *chops*
between the spike extreme and `L` and then often drifts — it does not thrust
the other way. The fade stop must sit **beyond the spike extreme** to be a
valid "the trap failed" invalidation, which makes `R` wide; a 2R target from
there is rarely reached (P(MFE ≥ 2R) ≈ 15–27 %), while the fade still gets
stopped at ~1 R about as often as it wins.

---

## F. Train / Val / OOS / Holdout table

See §E above. Cross-symbol: **0 / 3 symbols have a TRAIN-stable fade
boundary.**

---

## G. What is SUPPORTED

- **The reclaim-distance boundary as a CLASSIFIER / avoidance filter.**
  `X = reclaim_dist / spike_range > ~0.6` ⇒ continuation ≈ 0 %, the
  spike-direction long/short is a near-certain loser (Stage-6/7, reproduced
  here). Use it to **stand aside**, not to buy.
- The P90 range-percentile spike definition; the level-break requirement;
  acceptance vs reclaim as the state separator.

### G.1 Independent confirmations of the H7 avoidance classifier

The `X > 0.6` "buying the break is a near-certain loser" reading has now been
reproduced on three independent datasets. All three score the *spike-direction
buy* (the thing AVOID says to skip); none is a fade trade; none makes the
classifier `PROVEN` (still no untouched multi-month holdout).

| # | dataset | instrument / vendor / period | H7_TRAP "buy the break" outcome | note |
|---|---|---|---|---|
| 1 | Stage-3→8 base | NIFTY / NATGAS / CRUDE, polled histcap + oi_dashboard, Jul–Aug 2026, 3 regimes, spent 18 % holdout | continuation ≈ 0 %, spike-direction long/short a near-certain loser | the original basis |
| 2 | `--source kaggle_nifty` (`ORDERFLOW_H1H7_PERFORMANCE_V2.md`) | NIFTY-50 5-min **cash index**, Kaggle (debashis74017), 2015-01 … 2026-05, 2 616 sessions, 11 360 events, all 3 regimes + all 4 chrono splits | "buy the break" wins **11.2 %** (95 % CI 10.0–12.4), `E[R] −1.005` | independent vendor + 11-year period |
| 3 | `orderflow_h1h7_spike_options.py` (`ORDERFLOW_H1H7_SPIKE_OPTIONS.md`) | Upstox **expired-option premium** 5-min (the premium *is* the series), 510 contracts / 12 expiries, Jun–Sep 2026, 5 877 `H7_TRAP` events | "buy the break" wins **3.9 %**, `E[R] −1.44`, `fix3_R ≤ −1R` in **93.3 %**; consistent every regime (−1.39…−1.47) and every chrono split (−1.30…−1.63) | independent *instrument type* (a decaying, 0-bounded, convex series) + vendor + period |

**Caveat on #3 — this does NOT reopen the option-premium question (§H).** It
confirms only the *avoidance* polarity: buying the break of an `H7_TRAP` on
premium is a near-certain loss. It is **not** a tradeable premium edge — the
same run's positive-looking `H1_CONT_OBSERVE` `E[R]` is a convexity artifact
(raw-premium R-multiples are not comparable to a linear instrument), and Stage-4
"nothing survives ATM option premium" stands. #3 also cannot upgrade the status:
contracts overlap in calendar time and share expiry dynamics, so its HOLDOUT
slice is not fresh out-of-sample.

Net effect: `H7` reclaim-distance boundary = **SUPPORTED** (avoidance classifier
only), now on 3 independent datasets. Fading it is still **REJECTED** (§H).
Still **not PROVEN**.

## H. What is REJECTED

- **Fading H7 as a standalone edge** — negative on train + validation for
  both symbols with real samples; nothing survives OOS; 0/3 stable; no
  large-R tail; DD ≈ −1 per trade at a 2R target.
- `body_frac` as a strong discriminator (Stage-7); a single universal `X`
  across symbols; the "small SL → large R" thesis (no P5R/P8R on either the
  continuation or the fade side); Market-Profile location, compression, price
  rotation, VWAP-proxy, day-regime, OI, the option-volume "imbalance" proxy
  (Stages 3–7).
- Any **option-premium** claim from this underlying analysis (Stage-4).

## I. What is UNOBSERVABLE (no proxy invented)

- true buyer/seller aggression, lifting-the-offer / hitting-the-bid, delta,
  cumulative delta, "R-delta", the ignition **volume/delta component**,
  footprint / price-level transaction distribution, depth-of-book imbalance,
  liquidity added / pulled, absorption, BMC/SMC order-flow internals,
  constituent-stock → index causation.

## J. What still needs data

- an underlying **tick stream with aggressor side** (trade vs bid/ask) →
  delta, cumulative delta, lift/hit, R-delta;
- **full depth-of-book** snapshots → depth imbalance, liquidity add/pull,
  absorption;
- a **single-stock tick / 1-minute feed** for the top ~15 constituents →
  stock-lead-index causation;
- **≥ 100 independent sessions** across a full volatility cycle, and a
  genuinely untouched multi-month final holdout (the current one is spent).

## K. Application blueprint

Every completed 5-minute bar, per symbol, one deterministic row — no score:

```
CANDLE               2026-08-19 13:45  CRUDEOIL  6412.0
   ↓
ABNORMAL DISPLACEMENT range_pctile = 0.94 (≥ 0.90)   spike_range = 18.4   disp_atr = 1.3
   ↓
STRUCTURAL LEVEL      L = 6398.0  (prior swing high)   dist_L = +14.0
   ↓
BREAK / ACCEPTANCE    acc1 = 1   acc2 = 1
   ↓
RECLAIM DISTANCE      max back-through L (T+1..T+3) = 7.1   →   X = 7.1 / 18.4 = 0.386
   ↓
CLASSIFICATION        0.28 < X ≤ 0.6  →  "leaning trap"      (X > 0.6 → H7 hard trap; X ≤ 0.28 → ambiguous)
   ↓
CONTINUATION / TRAP   P(continuation) ≈ 15 %   P(trap) ≈ 82 %     (from the Stage-7 curve)
   ↓
STRUCTURAL RISK       invalidation = spike extreme 6430.5 (+3 % pad)
   ↓
TARGET MAP            next opposing barrier VAL 6360 → 2.8 R away   (informational only)
   ↓
ACTION               STAND ASIDE   — reason: "reclaim 0.39·spike_range: continuation unlikely,
                                     fade not an out-of-sample edge (Stage-8)"
```

- Every displayed number has the equation in §C/§D. No "AI score", no
  confidence bar.
- States: `H7_HARD_TRAP` (X > 0.6), `LEANING_TRAP` (0.28 < X ≤ 0.6),
  `AMBIGUOUS` (X ≤ 0.28), `H1_CONT` (no reclaim + acc2 + reaction agrees +
  disp_atr ≥ 1 + avR ≥ 1 — CRUDE only, small +E), `NEUTRAL` (not a spike).
- The only actionable outputs the evidence supports are **STAND ASIDE** on
  the trap side and a **low-conviction CRUDE-only H1 continuation candidate**.

## L. Exact next research step

The mechanism (displacement → break → acceptance vs graded reclaim →
participation vs trap) is real and causal, but its actionable content is
"do not trade the trap", not "trade the trap". The next step is **not**
another price-only stage. It is:

> **Acquire a tick / depth feed** (aggressor-classified trades + level-2 book)
> for CRUDEOIL and NIFTY futures, re-express the H1 "ignition" state with the
> real **delta / imbalance / absorption** component that is currently
> UNOBSERVABLE, and re-run Stage-6→8 with a fresh multi-month holdout.
> Without that data the price-only model has been taken as far as the
> mathematics allows: a **SUPPORTED avoidance classifier** and a
> **PROMISING, small, CRUDE-only** continuation edge — **nothing PROVEN.**

---

## Final statement (for presentation)

*We studied the publicly described order-flow market-behaviour concepts
independently and tried to convert them into a causal mathematical
framework.* We found:

1. **Mechanism:** abnormal displacement → structural-level break → acceptance
   vs a *graded* reclaim → participation (H1) vs trap (H7).
2. **Equation:** `X = reclaim_dist / spike_range`; `P(continuation)` collapses
   monotonically in `X` with a knee near `X ≈ 0.6`.
3. **Why it should work:** a genuine displacement that is *accepted* tends to
   continue; one that is *reclaimed* past ~0.6 of its own range was liquidity
   interaction, not directional conviction.
4. **Historical + OOS evidence:** the `X` boundary reproduces across NIFTY /
   NATGAS / CRUDE, three regimes, and a spent 18 % holdout, as a **classifier**
   — and again on two later independent datasets (Kaggle NIFTY-50 cash index
   2015–2026, 2 616 sessions; Upstox expired-option premium 2026, 5 877
   `H7_TRAP` events, "buy the break" wins 3.9 %). See §G.1. Still not `PROVEN`.
5. **Where it fails:** the boundary tells you what **not** to buy — it is
   **not** a fade signal (Stage-8: fading H7 is negative out-of-sample). A
   trap is not a clean reversal. The continuation (H1) edge on the underlying
   is small and only holds on CRUDE. Nothing survived ATM option premium
   (Stage-4). Nothing is PROVEN.
6. **Data limitation:** the *volume / delta / order-flow-internal* half of
   every "ignition / R-delta / BMC-SMC" concept is **UNOBSERVABLE** with our
   current OHLC + option-chain data. That is the gap to close next.

Research only. No production change, no signal, no orders, no score.
