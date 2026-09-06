# H1/H7 + spike research on the Upstox expired-option **premium** series

**Verdict — `NOT VALIDATED` (nothing `PROVEN`).**

Two pre-declared gates fail by construction:

1. The engine emits an **actionable `H1_CONT` for CRUDEOIL only** (frozen). The
   underlying here is NIFTY, so every continuation-gate match is emitted as
   `H1_CONT_OBSERVE` / `NO_ACTION`. The actionable state received **zero**
   samples — same outcome as the `kaggle_nifty` run.
2. There is **no fresh holdout**. Span is ~11 weeks of one underlying; a
   contract lives across many weeks, the chronological split is by
   contract-session rank, so the same calendar weeks and the same expiry
   dynamics appear in several splits. The `HOLDOUT` slice is not out-of-sample
   in any meaningful sense.

**What this run *does* add (the one useful result):** the **H7 reclaim-distance
= AVOID** classification is confirmed on a completely new instrument *type* —
the option premium itself, a different vendor (Upstox), a different period
(2026-06 … 2026-09). "Buying the break" of an `H7_TRAP` on premium loses in
**93.3 %** of cases (`fix3_R ≤ −1R`), win rate **3.9 %**, `E[R] −1.44`, in every
regime (CHOP −1.47 / TREND_UP −1.42 / TREND_DOWN −1.39) and every chronological
split (TRAIN −1.63 / VAL −1.30 / OOS −1.31 / HOLDOUT −1.41). This **strengthens
`H7 = SUPPORTED`** as an avoidance classifier. Fading H7 is still `REJECTED`
(Stage-8) and is not walked.

**The positive-looking `H1_CONT_OBSERVE` number is an artifact — do not act on
it.** `H1_CONT_OBSERVE` shows `E[R] +0.82` gross (+0.80 after a 2-tick spread
haircut), positive in all four splits. It is spurious because:

- It is a plain **directional bet on a decaying, 0-bounded, convex series**.
  Direction is a mix (LONG 1543 / SHORT 1071; CE and PE on both sides), so it is
  *not* "buy calls in a rally" — it is "premium keeps moving the way it just
  moved". On premium that is easy in **either** direction: theta pulls shorts
  toward the target, gamma pushes longs, and the R-denominator (spike range
  ± 3 % pad) is tiny next to how far a premium travels. R-multiples on raw
  premium are **not comparable** to R-multiples on a roughly-linear instrument —
  the whole frozen R-framework assumes near-linear price.
- MAE is small (`~−0.36R`) and MFE large (`~1.88R`) — the convexity signature,
  not an edge.
- Half the "wins" are `EOD` (premium merely closed on the favourable side),
  not `T3` target hits (821 `T3` vs 804 `EOD`).
- It is carried by the `CHOP` **premium-series** regime (`n=1610`, `E[R] +1.14`
  vs TREND_UP +0.54 / TREND_DOWN +0.16) and it **degrades on the newest data**
  (`OOS` win 68 % → `HOLDOUT` win 49 %, PF 3.4 → 1.5).
- The deployed engine cannot emit an actionable signal here anyway.

**Spike / sideways_spike / hammer on premium — `NOT VALIDATED`.** `spike` loses
(`win 23.2 %`, `E[pts] −1.83`, `PF 0.78`, 95 k resolved) with **monotonic
degradation** across the split (TRAIN −0.75 → VAL −1.54 → OOS −3.96 →
HOLDOUT −2.56 pts) — the classic non-stationary / overfit signature. Only
`sideways_spike` is marginally positive (`E[pts] +1.34`, `PF 1.22`) on a small
sample (7.7 k resolved), and only the `TREND_UP` premium-regime slice of `spike`
is positive (`+1.71`) — again just directional. `spike+oi_confirm` does not
rescue it (`E[pts] −1.07`). Expiry-day `spike` is brutal (`E[pts] −6.78`).

---

## Frozen — nothing changed

`app/orderflow/h1h7_state.py`, `app/orderflow/smart_money.py`, the Stage-6
baseline `_decision_list`, entry / exit / trading / execution / broker / risk
logic, existing tests, validation thresholds: **byte-identical**. `live_trading`
stays `false`, `paper_mode` stays `true`. No order was placed. No missing data
was fabricated — true order flow (aggressor tape / depth) is `UNOBSERVABLE` in
the option data and is never estimated; theta is in the walked premium path, the
bid/ask **spread is not** (hence the spread-haircut *diagnostic*, which is not a
gate and not a rule).

- Script: `backend/scripts/orderflow_h1h7_spike_options.py` (research-only)
- Data: the `option_bars_5m` **VIEW** over `normalized_option_bars`
  (`source='upstox_expired_options'`) in the separate research DB
  `backend/data/historical/upstox/upstox_research.db` — nothing copied
- Event tables (git-ignored, regenerated on demand):
  `backend/data/orderflow_h1h7_spike_options.csv` (46 924 rows),
  `backend/data/orderflow_spike_options_legs.csv` (377 007 rows)

---

## Method

- **Unit** = one (option contract, trading day). RTH bars only
  (09:15–15:30 IST). 5-minute premium bars from the view; zero look-ahead
  (a bar is eligible once T+1…T+3 are complete).
- **H1/H7**: the inner loop of `scripts/orderflow_h1h7_performance.collect()`
  reproduced verbatim (same helpers, same reference geometry: entry = spike
  close, stop = spike extreme ∓ 3 % of the spike range, `R = |entry − stop|`),
  fed premium bars instead of underlying bars. Symbol passed to the engine =
  `NIFTY`.
- **Walk**: `scripts/orderflow_h1h7_performance._walk` (realistic stop fill at
  the breaching bar's extreme ∓ 1 tick; premium tick = 0.05; fixed 3R target).
- **Direction** = the spike direction for every state, uniformly. For `H7_*`
  that is the "buy the break" outcome the AVOID label says to skip.
- **Baseline** = the frozen Stage-6 decision list on the same events.
- **Spike patterns**: `smart_money_setups(pattern ∈ {spike, sideways_spike,
  hammer})` unchanged, plus a `spike+oi_confirm` variant gated on real option OI
  rising across the trigger bar.
- **Regime** = `_regime()` on the **premium** series (TREND_UP / TREND_DOWN /
  CHOP). Underlying-regime alignment is not available for the full span (Upstox
  NIFTY 1-minute history only covers ~2026-08-07 onward).
- **Splits**: contract-session rank, 45 / 65 / 82 / 100 (frozen Stage-6
  fractions).
- **Spread haircut**: `E[R] − ticks·0.05 / R` per event, `ticks ∈ {1,2,4}`
  round-trip. **Diagnostic only.**

## Data

| | |
|---|---|
| (contract, day) sessions | 14 921 |
| contracts | 510 |
| expiries | 12 — 2026-06-16 … 2026-09-01 (3 are monthlies; some long-dated legs run back to 2022-11) |
| H1/H7 eligible events | 46 924 (CE 24 171 / PE 22 753; expiry-day 2 376) |
| spike/sideways/hammer legs | 377 007 |
| premium-series regime mix | CHOP 24 107 / TREND_DOWN 13 141 / TREND_UP 9 676 |

## H1/H7 — per state (premium walk, pooled)

| state | n | win % | E[R] | PF | P(1R) | P(3R) | P(SL first) |
|---|--:|--:|--:|--:|--:|--:|--:|
| `H1_CONT` | 0 | — | — | — | — | — | — |
| `H1_CONT_OBSERVE` | 2 614 | 62.2 | **+0.818** | 2.87 | 0.71 | 0.31 | 0.16 |
| `H1_CONT_BLOCKED` | 8 353 | 59.2 | +0.623 | 2.32 | 0.63 | 0.24 | 0.21 |
| `H1_WEAK` | 2 000 | 63.0 | +0.864 | 2.71 | 0.83 | 0.33 | 0.13 |
| `H7_TRAP` | 5 877 | 3.9 | **−1.438** | 0.05 | 0.09 | 0.02 | 0.89 |
| `H7_LEANING_TRAP` | 5 312 | 17.2 | −0.718 | 0.29 | 0.25 | 0.07 | 0.63 |
| `AMBIGUOUS` | 17 716 | 40.9 | −0.062 | 0.91 | 0.43 | 0.13 | 0.38 |
| `action=AVOID` (SUPPORTED) | 11 189 | 10.2 | −1.096 | 0.14 | 0.17 | 0.04 | 0.77 |
| `action=NO_ACTION` | 30 683 | 49.1 | +0.260 | 1.43 | 0.53 | 0.19 | 0.30 |

`H1_CONT_OBSERVE` chronological: TRAIN `E[R] +0.85` (win 64) · VAL `+1.01` (65) ·
OOS `+1.05` (68) · **HOLDOUT `+0.32` (49)**. Spread haircut: gross +0.818 →
−1t +0.808 → −2t +0.798 → −4t +0.778.

`H7_TRAP` chronological: TRAIN `−1.63` · VAL `−1.30` · OOS `−1.31` ·
HOLDOUT `−1.41` — consistent.

## Baseline (Stage-6 decision list) vs new engine — same events

| | n | win % | E[R] | PF | netR | maxDD |
|---|--:|--:|--:|--:|--:|--:|
| continuation — BASELINE | 11 632 | 56.7 | +0.571 | 2.12 | +6 636 | −405 |
| continuation — NEW | 2 614 | 62.2 | +0.818 | 2.87 | +2 138 | −76 |
| H7_TRAP — BASELINE | 15 772 | 17.9 | −0.839 | 0.26 | −13 232 | −13 238 |
| H7_TRAP — NEW | 5 877 | 3.9 | −1.438 | 0.05 | −8 454 | −8 456 |

The refined engine's `H7_TRAP` is a **tighter, more selective avoid** than the
baseline (fewer events, far worse "buy the break" outcome). On the option
premium this is the only place the refinement clearly helps.

## Spike / sideways_spike / hammer on premium

| variant | resolved | win % | E[pts] | PF | net pts |
|---|--:|--:|--:|--:|--:|
| `spike` | 95 115 | 23.2 | −1.83 | 0.78 | −173 670 |
| `sideways_spike` | 7 683 | 25.9 | +1.34 | 1.22 | +10 277 |
| `hammer` | 82 067 | 24.0 | −0.56 | 0.88 | −46 291 |
| `spike+oi_confirm` | 51 052 | 23.1 | −1.07 | 0.85 | −54 841 |

`spike` by premium-regime: TREND_UP `+1.71` / TREND_DOWN `−1.36` / CHOP `−3.45`.
`spike` by split: TRAIN `−0.75` → VAL `−1.54` → OOS `−3.96` → HOLDOUT `−2.56`.
`spike` expiry-day `E[pts] −6.78` (win 18.8 %).

## Recommended next steps

- **Do not** promote anything from this run. `H1_CONT` stays CRUDEOIL-only;
  NIFTY continuation stays `NOT_VALIDATED` / observe-only.
- If option-premium continuation is to be studied properly it needs: (a) a
  premium-aware R definition (delta/gamma-scaled, not raw points), (b) a real
  bid/ask series (not in the current data), (c) a genuinely out-of-sample
  period — non-overlapping contracts, months apart.
- The H7-on-premium avoidance result is folded into the H7 evidence base as the
  third independent confirmation (index/futures → Kaggle NIFTY → option premium)
  — see `ORDERFLOW_STAGE8_H7_FADE.md` §G.1. Still `SUPPORTED`, still not
  `PROVEN`.
