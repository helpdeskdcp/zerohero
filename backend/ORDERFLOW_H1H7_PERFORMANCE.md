# H1 / H7 Structural State Engine — historical out-of-sample performance

**Verdict: `NOT VALIDATED`. Highest honest status: `PROMISING`. `PROVEN`: nothing.**

The first pass of this evaluation only saw 3 sessions (it read through
`market_hub`, which holds only the 4 zerohero histcap days). This pass runs the
**same deployed deterministic engine** (`app/orderflow/h1h7_state.py`, unchanged)
over the **full multi-source history** the frozen Stage-3→8 research used
(`scripts/orderflow_histsrc` = zerohero histcap + `oi_dashboard` cycles /
live_candles): **2 503 eligible events, 38 session dates (2026-07-13 → 09-04),
all three market regimes.** No new market data was captured — today is a weekend
and `market_history.db` still ends at 2026-09-04 — but these sessions were
already on disk; the earlier run just wasn't wired to them.

Nothing about production changed: **no** broker execution, `live_trading`,
`paper_mode`, order logic, or shadow-engine change; the evaluator adds no
BUY/SELL/order/entry_signal logic and is standalone (not pytest-collected).

- Report generator: `backend/scripts/orderflow_h1h7_performance.py`
  (`--source histsrc` [default] | `--source market_hub`).
- Event table: `backend/data/orderflow_h1h7_performance.csv` — **2 503 rows**,
  columns: `timestamp, symbol, session, src, regime, split, new_state,
  new_action, new_research_status, baseline_state, spike_direction, entry_ref,
  structural_level, sl_price, risk_points, available_R, n1_agreement, disp_atr,
  body_fraction, reclaim_distance_ratio, reclaimed_within_3, acc1, acc2,
  reclaim2, MFE_R, MAE_R, reached_1R, reached_2R, reached_3R, sl_first, fix3_R,
  exit`.

---

## Method

- **Event universe** — every eligible bar the engine classifies over
  `orderflow_histsrc` sessions: **CRUDEOIL 1087 events / 38 sessions**,
  **NATURALGAS 1265 / 35**, **NIFTY 151 / 36**. Regime mix (per the Stage-3
  session label): CRUDEOIL TREND_UP 468 / CHOP 463 / TREND_DOWN 156;
  NATURALGAS CHOP 711 / TREND_UP 313 / TREND_DOWN 241; NIFTY CHOP 93 /
  TREND_DOWN 30 / TREND_UP 28.
- **Zero look-ahead** — detection ≤ idx; acceptance/reclaim window idx+1..idx+3
  (completed); walk from idx+1. (The engine's replay test already proves this
  bar-by-bar on every real session.)
- **Reference geometry (frozen)** — Stage-6 "A_spike": entry = spike close,
  stop = spike extreme ∓ 3 % of the spike range, `R = |entry − stop|`. **A
  scoring reference, not an order.**
- **Realistic walk** — stop fills at the breaching bar's extreme ∓ 1 tick.
  Records MFE_R, MAE_R, whether +1R/+2R/+3R reached, whether SL was hit before
  +1R (`sl_first`), and realised R at a fixed 3R target (`fix3_R`). **"win" =
  `fix3_R > 0`.**
- **Direction = spike direction for every state**. For `H1_CONT` that is the
  continuation trade; for `H7_*` it is the *"buy the break"* outcome the `AVOID`
  label tells you to skip — its badness is the avoidance-quality metric. Fading
  H7 is REJECTED (Stage-8) and is not walked.
- **Baseline** — the frozen Stage-6 decision list on the *same events*
  (`reclaim2` binary + `body_frac ≥ 0.55` gate) vs the deployed engine
  (reclaim-distance bands + `disp_atr ≥ 1.0` gate).
- **Chronological split** — per-symbol session-date rank, matching the frozen
  Stage-6 fractions: TRAIN 0–45 % / VALIDATION 45–65 % / OOS 65–82 % /
  HOLDOUT 82–100 %.
- **Data caveats** — `oi_dashboard` `cycles_resampled` highs/lows are slightly
  understated (Stage-3 QC); `live_candles` carry no volume (irrelevant — the
  engine is volume-free). This is the same dataset Stage-3→8 used, and its 18 %
  final holdout was **already scored once by Stage-6** — a positive holdout here
  is *confirmatory*, not fresh out-of-sample.

---

## 1. New engine — per state (all symbols pooled, underlying/futures walk)

| state | n | sessions | W/L | win % | E[R] | PF | net R | max DD | max consec loss | MFE~ | MAE~ | P(+1R) | P(+3R) | P(SL first) |
|---|--:|--:|:--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| **H1_CONT** | **56** | **28** | 30/26 | **53.6** | **+0.702** | **1.96** | **+39.3** | −8.8 | 5 | 2.28 | −0.84 | 0.73 | 0.46 | 0.25 |
| H1_CONT_OBSERVE (NIFTY/NATGAS) | 75 | 31 | 35/40 | 46.7 | +0.271 | 1.33 | +20.3 | −23.3 | 6 | 1.91 | −0.97 | 0.68 | 0.32 | 0.24 |
| H1_CONT_BLOCKED | 341 | 38 | 161/179 | 47.2 | +0.434 | 1.57 | +147.9 | −15.9 | 8 | 2.14 | −1.00 | 0.77 | 0.37 | 0.21 |
| H1_WEAK | 186 | 35 | 78/107 | 41.9 | +0.154 | 1.16 | +28.6 | −24.2 | 10 | 2.18 | −1.12 | 0.76 | 0.38 | 0.24 |
| H7_TRAP | 470 | 37 | 8/461 | **1.7** | **−1.677** | 0.03 | −788.4 | −788.4 | 266 | 0.13 | −1.44 | 0.09 | 0.02 | **0.94** |
| H7_LEANING_TRAP | 298 | 37 | 47/251 | 15.8 | −0.790 | 0.34 | −235.4 | −238.1 | 32 | 0.48 | −1.19 | 0.32 | 0.13 | 0.66 |
| AMBIGUOUS | 830 | 38 | 284/544 | 34.2 | −0.118 | 0.88 | −98.3 | −138.7 | 12 | 1.25 | −1.11 | 0.56 | 0.26 | 0.40 |
| SPIKE_NO_LEVEL | 0 | — | — | — | — | — | — | — | — | — | — | — | — | — |

### Derived buckets

| bucket | n | W/L | win % | E[R] | PF | net R | max DD |
|---|--:|:--:|--:|--:|--:|--:|--:|
| **H7_SUPPORTED** (research_status = SUPPORTED) | 768 | 55/712 | 7.2 | −1.333 | 0.12 | −1023.8 | −1026.5 |
| **H7_AVOID** (action = AVOID) | 768 | 55/712 | 7.2 | −1.333 | 0.12 | −1023.8 | −1026.5 |
| **NO_ACTION** (action = NO_ACTION) | 1432 | 558/870 | 39.0 | +0.069 | 1.08 | +98.5 | −55.7 |
| **CONTINUATION_CANDIDATE** (action) | 56 | 30/26 | 53.6 | +0.702 | 1.96 | +39.3 | −8.8 |

*Reading:* "buying the break" of an `H7_SUPPORTED` event lost on **92.8 %** of
768 events (P(SL first) 0.83, E[R] −1.33). The `AVOID` label is pointing away
from a reliably bad trade.

### H1_CONT by symbol

| symbol | n | win % | E[R] | PF | net R |
|---|--:|--:|--:|--:|--:|
| CRUDEOIL | 56 | 53.6 | +0.702 | 1.96 | +39.3 |
| NIFTY | 0 | — | — | — | — |
| NATURALGAS | 0 | — | — | — | — |

`H1_CONT` fires for CRUDEOIL only — NIFTY/NATGAS matches are emitted as
`H1_CONT_OBSERVE` / `NOT_VALIDATED` by design.

### H1_CONT / H7_SUPPORTED by regime

| regime | H1_CONT n | win % | E[R] | PF | net R | | H7_SUPPORTED n | win % | E[R] |
|---|--:|--:|--:|--:|--:|---|--:|--:|--:|
| CHOP | **22** | **72.7** | **+1.578** | **5.42** | +34.7 | | 407 | 7.9 | −1.337 |
| TREND_UP | 29 | 44.8 | +0.276 | 1.30 | +8.0 | | 237 | 7.6 | −1.291 |
| TREND_DOWN | 5 | 20.0 | −0.676 | 0.47 | −3.4 | | 124 | 4.0 | −1.401 |

**The `H1_CONT` edge is concentrated in CHOP regime.** In TREND_UP it is
marginal (E[R] +0.28); in TREND_DOWN it is negative on n = 5. `H7_SUPPORTED`
"buy the break" loses in **every** regime.

---

## 2. Baseline (frozen Stage-6 decision list) — same events

| baseline state | n | W/L | win % | E[R] | PF | net R | max DD | max consec loss |
|---|--:|:--:|--:|--:|--:|--:|--:|--:|
| H1_CONT | 621 | 272/348 | 43.8 | +0.249 | 1.29 | +154.9 | −30.4 | 9 |
| H1_WEAK | 114 | 40/73 | 35.1 | −0.108 | 0.90 | −12.3 | −36.5 | 13 |
| H7_TRAP | 892 | 108/782 | 12.1 | −1.116 | 0.21 | −995.5 | −998.9 | 29 |
| AMBIG | 629 | 223/405 | 35.4 | −0.053 | 0.94 | −33.1 | −90.6 | 13 |

---

## 3. BASELINE vs NEW ENGINE (same historical period, same events)

### H1_CONT — the only actionable state

| | signals | win % | E[R] | PF | net R | max DD | max consec loss |
|---|--:|--:|--:|--:|--:|--:|--:|
| **BASELINE** | 621 | 43.8 % | +0.249 | 1.29 | +154.9 R | −30.4 R | 9 |
| **NEW ENGINE** | 56 | **53.6 %** | **+0.702** | **1.96** | +39.3 R | **−8.8 R** | **5** |

The new gate (`disp_atr ≥ 1.0` **AND** `available_R ≥ 1.0`) keeps **56 of the
baseline's 621** H1_CONT events (≈ 9× more selective) and the survivors are
higher quality on **every** axis: +9.8 pts win rate, 2.8× expectancy, PF
1.29 → 1.96, a third of the drawdown, fewer consecutive losses. It gives up
absolute net R (fewer trades).

### H7_TRAP — avoidance quality ("buy the break" outcome)

| | signals | win % | E[R] | PF | net R |
|---|--:|--:|--:|--:|--:|
| **BASELINE** (`reclaim2`) | 892 | 12.1 % | −1.116 | 0.21 | −995.5 R |
| **NEW ENGINE** (`reclaim_dist/spike_range > 0.60`) | 470 | **1.7 %** | **−1.677** | 0.03 | −788.4 R |

The reclaim-distance refinement produces a **smaller, sharper** `H7_TRAP` set —
the breaks it flags are almost never worth buying (win 1.7 % vs 12.1 %). The
in-between events land in `H7_LEANING_TRAP` (win 15.8 %) — also `AVOID`,
conservatively.

---

## 4. Chronological split (per-symbol date rank)

symbol-sessions per split: TRAIN 49 · VALIDATION 23 · OOS 17 · HOLDOUT 20.

### new H1_CONT

| split | n | win % | E[R] | PF | net R |
|---|--:|--:|--:|--:|--:|
| TRAIN | 30 | 50.0 | +0.441 | 1.50 | +13.2 |
| VALIDATION | 11 | 54.5 | +0.786 | 2.34 | +8.7 |
| OOS | 10 | 60.0 | +1.116 | 3.22 | +11.2 |
| HOLDOUT | 5 | 60.0 | +1.257 | 3.42 | +6.3 |

**Every split is E[R]-positive** for new H1_CONT — but OOS (n=10) and HOLDOUT
(n=5) are thin, and HOLDOUT is the same spent slice Stage-6 already scored.

### baseline H1_CONT (for comparison)

| split | n | win % | E[R] |
|---|--:|--:|--:|
| TRAIN | 298 | 45.6 | +0.295 |
| VALIDATION | 107 | 35.5 | **−0.081** |
| OOS | 113 | 47.8 | +0.435 |
| HOLDOUT | 103 | 42.7 | +0.257 |

The baseline's VALIDATION cohort is **negative**; the new engine's tighter gate
removes exactly that weak set.

### new H7_TRAP ("buy the break" — should stay strongly negative everywhere)

| split | n | win % | E[R] | P(SL first) |
|---|--:|--:|--:|--:|
| TRAIN | 213 | 0.9 | −1.753 | 0.95 |
| VALIDATION | 77 | 0.0 | −1.780 | 0.96 |
| OOS | 99 | 3.0 | −1.540 | 0.88 |
| HOLDOUT | 81 | 3.7 | −1.549 | 0.95 |

Rock-stable across all four splits. This is the well-supported part.

---

## 5. Option-premium profitability — `NOT VALIDATED`

Real historical option data exists (zerohero ATM premium ticks ~25–30 s poll for
4 sessions; `oi_dashboard` per-strike CE/PE LTP+OI ~9–20 s for Jul–Aug).
**Stage-4 already re-walked the H1/H7 events on the captured ATM premium and
found the index edge does not survive the option spread + theta.** This
evaluation does not re-derive an option win rate on top of that — it would not
change the Stage-4 conclusion and the per-state option N is thin (56 H1_CONT).
**OPTION-PREMIUM PROFITABILITY: NOT VALIDATED** (edge does not survive ATM
premium). The underlying/futures structural results above are the ones reported.

---

## 6. Answers to the required report items

| metric | value (new engine, `H1_CONT`, underlying/futures) |
|---|---|
| **sample size** | 2 503 eligible events / 38 sessions / 3 regimes. Actionable `H1_CONT`: **n = 56, 28 sessions, all 3 regimes** (all CRUDEOIL). |
| **win rate** | `H1_CONT` **53.6 %** (30/56). `H7_SUPPORTED` "buy the break" 7.2 % ⇒ AVOID is well-founded. |
| **expectancy** | `H1_CONT` **+0.702 R** · `H7_SUPPORTED` −1.333 R · `NO_ACTION` +0.069 R. |
| **profit factor** | `H1_CONT` **1.96** · baseline `H1_CONT` 1.29 · `H7_SUPPORTED` 0.12. |
| **net R / max DD** | `H1_CONT` +39.3 R / −8.8 R (max consec loss 5). Baseline `H1_CONT` +154.9 R / −30.4 R (9). |
| **baseline comparison** | new `H1_CONT` 56 sig @ 53.6 % / E[R] +0.70 / PF 1.96 / DD −8.8 R —vs— baseline 621 sig @ 43.8 % / E[R] +0.25 / PF 1.29 / DD −30.4 R. New `H7_TRAP` 470 @ 1.7 % —vs— baseline 892 @ 12.1 %. New engine wins on quality on **all four** chronological splits. |
| **is the new engine actually better?** | **On signal quality, yes — directionally and consistently:** the new `H1_CONT` beats the baseline on win rate, expectancy, PF, drawdown and consecutive losses on every split, and the new `H7_TRAP` is a cleaner avoid. **It is ≈ 9× more selective** (56 vs 621 signals) so it books less absolute net R, and the `H1_CONT` edge is **concentrated in CHOP regime**. **Still `NOT VALIDATED`** — the untouched holdout is n = 5 and already spent, the data is one broker's polled bars, and events are intraday-correlated. |

---

## 7. Verdict against the pre-declared validation criteria

| criterion | result |
|---|---|
| ≥ 10 sessions with an `H1_CONT` event | **PASS** (28) |
| ≥ 50 `H1_CONT` events | **PASS** (56) |
| ≥ 2 regimes represented | **PASS** (3) |
| OOS slice ≥ 20 events | FAIL (n = 10) |
| OOS E[R] > 0 | PASS (+1.12) |
| HOLDOUT slice ≥ 20 events | FAIL (n = 5) |
| HOLDOUT E[R] > 0 | PASS (+1.26) |
| HOLDOUT is FRESH (sessions after the frozen Stage-6 holdout) | **FAIL** — no sessions exist after 2026-09-04 |

**⇒ `NOT VALIDATED`.** The sample/regime/session bar is met and every
chronological split's `H1_CONT` E[R] is positive, but the out-of-sample and
holdout slices are too thin (n = 10, n = 5) and the holdout was already scored
once by the frozen model — it is confirmatory, not fresh. The `H1_CONT` edge is
also CHOP-concentrated.

**Highest honest status: `PROMISING`** — consistent with the frozen research
ceiling, now with a larger consistent sample behind it, not above it.

The frozen research ceiling is unchanged:

- H7 reclaim-distance boundary → **SUPPORTED** as an *avoidance* classifier
  (buying an H7 break loses ~93 % of the time, in every regime and every split;
  fading it is REJECTED).
- H1_CONT on the **underlying** → **PROMISING**, CRUDEOIL-only, CHOP-concentrated,
  more selective and higher-quality than the baseline.
- Option-premium profitability → **does not survive ATM premium** (Stage-4);
  not re-derived.
- **PROVEN: nothing.**

## 8. Next step

Capture **≥ 40 fresh sessions (post-2026-09-04) across ≥ 2 regimes** — the
histcap worker does this automatically on weekdays — then re-run
`orderflow_h1h7_performance.py --source histsrc`; a positive `H1_CONT` E[R] on
that genuinely-unseen slice (n ≥ 20) would move the status from `PROMISING` to
`SUPPORTED`. A real aggressor/tick/L2 feed is still needed before any
`PROVEN`-grade claim. Until then the engine stays a describe-and-log shadow
layer; `live_trading` / `paper_mode` / broker execution / order logic remain
untouched.
