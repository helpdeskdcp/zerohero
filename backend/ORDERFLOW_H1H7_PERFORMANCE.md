# H1 / H7 Structural State Engine — historical out-of-sample performance

**Verdict: `NOT VALIDATED`.**
The actionable state (`H1_CONT`) has **5 events across 3 sessions in one regime**.
No honest win rate can be quoted, and the new engine is **not demonstrably
better** than the frozen baseline on this data — the difference is within noise
at this sample size. **Nothing is `PROVEN`.**

This document evaluates the *deployed* shadow engine
(`app/orderflow/h1h7_state.py`). It changed **no** production trading logic,
broker execution, `live_trading`, `paper_mode`, or order path, and added no
BUY / SELL / order / entry_signal logic. Evaluation script:
`backend/scripts/orderflow_h1h7_performance.py`. Machine-readable event table:
`backend/data/orderflow_h1h7_performance.csv` (241 rows).

---

## Method

- **Event universe** — every eligible bar the engine classifies over the
  history it can actually see (`market_hub` → the zerohero histcap sessions):
  **241 events, 3 distinct session dates (2026-09-02/03/04), 9 symbol-sessions**
  (CRUDEOIL 99, NATURALGAS 135, NIFTY 7). One market regime. 2026-09-01 has too
  few bars to produce any eligible event.
- **Zero look-ahead** — detection uses bars `≤ idx`; the acceptance / reclaim
  window is bars `idx+1 … idx+3`, all completed; the forward walk starts at
  `idx+1`. (The engine's own replay test already proves this on every real
  session.)
- **Reference geometry (frozen)** — the Stage-6 "A_spike" geometry the engine's
  `available_R` already uses: entry = spike close, stop = spike extreme ∓ 3 % of
  the spike range, `R = |entry − stop|`. **This is a scoring reference, not an
  order.**
- **Realistic walk** — stop fills at the breaching bar's extreme ∓ 1 tick (loss
  can exceed −1R). Records MFE_R, MAE_R, whether +1R/+2R/+3R was reached,
  whether the stop was hit before +1R (`sl_first`), and the realised R at a
  fixed 3R target (`fix3_R`). **"win" = `fix3_R > 0`.**
- **Direction = the spike direction for every state**, uniformly, so states are
  comparable. For `H1_CONT` that is the continuation trade. For `H7_*` it is the
  *"buy the break"* outcome the `AVOID` label tells you to skip — its badness is
  the avoidance-quality metric. **Fading H7 is REJECTED (Stage-8) and is not
  walked.**
- **Baseline** — the frozen Stage-6 decision list on the *same events*:
  `if reclaim2 → H7_TRAP; elif acc2 ∧ n1_agree ∧ body_frac ≥ 0.55 → H1_CONT;
  elif acc1 ∧ n1_agree → H1_WEAK; else AMBIG` — i.e. the pre-refinement labeller
  (`reclaim2` binary + `body_frac` gate) versus the deployed engine
  (reclaim-distance bands + `disp_atr ≥ 1.0` gate).
- **Effective independent N ≈ the session count.** Intraday events are strongly
  correlated; 3 usable sessions/symbol is the real resolution.

---

## Per-event fields (in the CSV)

`timestamp, symbol, session, split, new_state, new_action, new_research_status,
baseline_state, spike_direction, entry_ref, structural_level, sl_price,
risk_points, available_R, n1_agreement, disp_atr, body_fraction,
reclaim_distance_ratio, reclaimed_within_3, acc1, acc2, reclaim2, MFE_R, MAE_R,
reached_1R, reached_2R, reached_3R, sl_first, fix3_R, exit`

---

## 1. New engine — per state (all symbols pooled, underlying/futures walk)

| state | n | sessions | W/L | win % | E[R] | PF | net R | max DD | max consec loss | MFE~ | MAE~ | P(+1R) | P(+3R) | P(SL first) |
|---|--:|--:|:--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| **H1_CONT** | **5** | 3 | 3/2 | **60.0** | **+1.257** | **3.42** | +6.28 | −1.48 | 1 | 3.02 | −0.08 | 0.60 | 0.60 | 0.40 |
| H1_CONT_OBSERVE (NIFTY/NATGAS) | 10 | 3 | 6/4 | 60.0 | +0.782 | 2.32 | +7.83 | −3.10 | 2 | 1.51 | −0.87 | 0.70 | 0.40 | 0.30 |
| H1_CONT_BLOCKED | 24 | 3 | 10/14 | 41.7 | +0.160 | 1.21 | +3.83 | −7.12 | 4 | 1.54 | −0.96 | 0.71 | 0.25 | 0.21 |
| H1_WEAK | 18 | 3 | 7/11 | 38.9 | +0.172 | 1.18 | +3.10 | −7.59 | 5 | 1.95 | −1.16 | 0.78 | 0.39 | 0.22 |
| H7_TRAP | 38 | 3 | 1/37 | 2.6 | −1.556 | 0.05 | −59.15 | −59.15 | 35 | 0.33 | −1.35 | 0.13 | 0.03 | 0.95 |
| H7_LEANING_TRAP | 32 | 3 | 2/30 | 6.2 | −1.239 | 0.13 | −39.64 | −39.99 | 15 | 0.67 | −1.22 | 0.38 | 0.06 | 0.62 |
| AMBIGUOUS | 84 | 3 | 24/60 | 28.6 | −0.273 | 0.73 | −22.96 | −29.14 | 7 | 1.36 | −1.13 | 0.61 | 0.23 | 0.38 |
| SPIKE_NO_LEVEL | 0 | — | — | — | — | — | — | — | — | — | — | — | — | — |

*(SPIKE_NO_LEVEL has no walk — no broken level ⇒ no reference geometry.)*

### Derived buckets

| bucket | n | W/L | win % | E[R] | PF | net R | max DD |
|---|--:|:--:|--:|--:|--:|--:|--:|
| **H7_SUPPORTED** (research_status = SUPPORTED) | 70 | 3/67 | 4.3 | −1.411 | 0.08 | −98.78 | −98.78 |
| **H7_AVOID** (action = AVOID) | 70 | 3/67 | 4.3 | −1.411 | 0.08 | −98.78 | −98.78 |
| **NO_ACTION** (action = NO_ACTION) | 136 | 47/89 | 34.6 | −0.060 | 0.94 | −8.21 | −28.89 |
| **CONTINUATION_CANDIDATE** (action) | 5 | 3/2 | 60.0 | +1.257 | 3.42 | +6.28 | −1.48 |

*Reading:* "buying the break" of an `H7_SUPPORTED` event lost on **95.7 %** of
the 70 events (P(SL first) 0.80, E[R] −1.41). That is directionally consistent
with the frozen research — `AVOID` is pointing away from a genuinely bad trade —
**on 3 sessions**.

### H1_CONT by symbol

| symbol | n | win % | E[R] | PF | net R |
|---|--:|--:|--:|--:|--:|
| CRUDEOIL | 5 | 60.0 | +1.257 | 3.42 | +6.28 |
| NIFTY | 0 | — | — | — | — |
| NATURALGAS | 0 | — | — | — | — |

---

## 2. Baseline (frozen Stage-6 decision list) — same events

| baseline state | n | W/L | win % | E[R] | PF | net R | max DD | max consec loss |
|---|--:|:--:|--:|--:|--:|--:|--:|--:|
| H1_CONT | 50 | 21/29 | 42.0 | +0.212 | 1.27 | +10.62 | −10.46 | 7 |
| H1_WEAK | 12 | 5/7 | 41.7 | +0.232 | 1.24 | +2.79 | −8.46 | 5 |
| H7_TRAP | 88 | 11/77 | 12.5 | −1.060 | 0.23 | −93.33 | −94.06 | 20 |
| AMBIG | 61 | 16/45 | 26.2 | −0.341 | 0.68 | −20.79 | −27.28 | 10 |

---

## 3. BASELINE vs NEW ENGINE (same historical period, same events)

### H1_CONT — the only actionable state

| | signals | win % | expectancy (E[R]) | profit factor | net R | max drawdown |
|---|--:|--:|--:|--:|--:|--:|
| **BASELINE** | 50 | 42.0 % | +0.212 | 1.27 | +10.62 R | −10.46 R |
| **NEW ENGINE** | 5 | 60.0 % | +1.257 | 3.42 | +6.28 R | −1.48 R |

The new engine's `disp_atr ≥ 1.0` **AND** `available_R ≥ 1.0` gate is far more
selective — it keeps **5 of the baseline's 50** H1_CONT events. The 5 it keeps
have better per-event stats here, but **n = 5 vs n = 50 is not a valid
statistical comparison**; a 60 % win rate on 5 samples has a 95 % CI of roughly
15–95 %.

### H7_TRAP — avoidance quality ("buy the break" outcome)

| | signals | win % | expectancy (E[R]) | profit factor | net R | max drawdown |
|---|--:|--:|--:|--:|--:|--:|
| **BASELINE** (`reclaim2`) | 88 | 12.5 % | −1.060 | 0.23 | −93.33 R | −94.06 R |
| **NEW ENGINE** (`reclaim_dist/spike_range > 0.60`) | 38 | 2.6 % | −1.556 | 0.05 | −59.15 R | −59.15 R |

The reclaim-distance refinement produces a **smaller, sharper** `H7_TRAP` set —
the breaks it flags are worse to buy (win 2.6 % vs 12.5 %, E[R] −1.56 vs −1.06).
Consistent with the Stage-7 finding that a deep reclaim is a harder trap tell
than "any reclaim within 2 bars". Still **3 sessions**.

---

## 4. Chronological split — `NOT STATISTICALLY MEANINGFUL`

TRAIN = 2026-09-02, VALIDATION = 2026-09-03, HOLDOUT = 2026-09-04 → **1 session
per split per symbol**.

| new H1_CONT | n | win % | E[R] | net R |
|---|--:|--:|--:|--:|
| TRAIN | 2 | 100.0 | +2.965 | +5.93 |
| VALIDATION | 1 | 0.0 | −1.481 | −1.48 |
| HOLDOUT | 2 | 50.0 | +0.917 | +1.83 |

| new H7_TRAP | n | win % | E[R] | net R |
|---|--:|--:|--:|--:|
| TRAIN | 12 | 8.3 | −1.237 | −14.84 |
| VALIDATION | 12 | 0.0 | −1.746 | −20.95 |
| HOLDOUT | 14 | 0.0 | −1.668 | −23.35 |

`H7_TRAP` "buy the break" loses on every event of VALIDATION and HOLDOUT — the
avoidance signal is at least stable in *sign* across the 3 days. `H1_CONT` swings
100 % → 0 % → 50 % on n = 2/1/2, which is exactly what noise looks like.

---

## 5. Option-premium profitability — `NOT VALIDATED`

Real historical ATM option-premium ticks exist for these 3 sessions
(`market_hub.session_option_quotes`, ~25–30 s REST poll). With only **5**
`H1_CONT` events and the Stage-4 finding that the underlying edge does **not**
survive ATM spread + theta, a per-state option win rate is **not computed and
not manufactured**. The underlying/futures structural walk above is the only
thing evaluated. **OPTION-PREMIUM PROFITABILITY: NOT VALIDATED.**

---

## 6. Answers to the required report items

| metric | value |
|---|---|
| **sample size** | 241 eligible events / **3 sessions** / 1 regime. Actionable `H1_CONT`: **n = 5** (all CRUDEOIL). |
| **win rate** | `H1_CONT` 60 % (3/5) — **not meaningful at n = 5**. `H7_SUPPORTED` "buy the break" 4.3 % (⇒ AVOID is pointing the right way). Overall `NOT VALIDATED`. |
| **expectancy** | `H1_CONT` +1.26 R (n = 5). `H7_SUPPORTED` −1.41 R. `NO_ACTION` −0.06 R. |
| **profit factor** | `H1_CONT` 3.42 (n = 5). `H7_SUPPORTED` 0.08. Baseline `H1_CONT` 1.27 (n = 50). |
| **baseline comparison** | new `H1_CONT` = 5 signals @ 60 % / E[R] +1.26 / PF 3.42; baseline `H1_CONT` = 50 signals @ 42 % / E[R] +0.21 / PF 1.27. New `H7_TRAP` = 38 @ 2.6 % buy-the-break win; baseline `H7_TRAP` = 88 @ 12.5 %. |
| **is the new engine actually better?** | **Cannot conclude.** It is much more *selective* (H1_CONT 5 vs 50; H7_TRAP 38 vs 88) and the kept events look cleaner, but at n = 5 the H1_CONT difference is inside the confidence interval, and there are only 3 correlated sessions in one regime. **Not demonstrably better. Not worse. NOT VALIDATED.** |

---

## 7. Predefined validation criteria — not met

To call anything here `PROVEN` / a validated edge would require (pre-declared,
Stage-1..8): **≥ 10 independent sessions AND ≥ 50 independent trades, across ≥ 2
regimes, positive on a held-out slice the model never saw.** Available: **3
correlated sessions, 1 regime, n = 5** on the only actionable state. Criteria are
**not met** ⇒ `NOT VALIDATED`, nothing `PROVEN`.

The frozen research ceiling is unchanged:

- H7 reclaim-distance boundary → **SUPPORTED** as an *avoidance* classifier
  (AVOID; fading it is REJECTED).
- H1_CONT on the **underlying** → **PROMISING**, CRUDEOIL-only, small.
- Option-premium profitability → **unproven** (and not inferred here).
- **PROVEN: nothing.**

## 8. Next step

Unchanged from `ORDERFLOW_STAGE9_L2_RESEARCH.md` / `ORDERFLOW_H1H7_INTEGRATION.md`:
capture ≥ 40 sessions across ≥ 2 regimes (and, for a real order-flow re-test, an
aggressor-classified tick + L2 feed), then re-run this exact evaluation. Until
then the engine stays a describe-and-log shadow layer; it is not promoted to a
signal, and `live_trading` / `paper_mode` / broker execution / order logic are
untouched.
