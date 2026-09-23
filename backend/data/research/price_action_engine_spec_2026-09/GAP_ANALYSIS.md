# Price Action Research + Probability Engine — Gap Analysis vs. Existing Code

Read-only comparison. Nothing modified. Covers `app/liquidity_sweep/`,
`app/strategy_mtf/`, `app/sr_engine.py`/`app/sr_dynamic/`,
`app/engines/state_classifier.py`/`regime_mtf.py`.

## Headline: the single most important thing the user doesn't seem to know

**`app/liquidity_sweep/` already IS almost exactly this spec** — market
structure → liquidity sweep → CHoCH/BOS → CISD/FVG/Order-Block confirmation
→ calibrated probability (not raw score) → 2R risk/target → ITM strike
ranking — built 2026-09-14 (commit `b06ba43`), and it was **backtested and
found NOT READY** on 2 years of real NIFTY 5m data:

```
Stage 1 (2,110 signals, chronological TRAIN/VALIDATION/OOS):
  directional accuracy: 47.1% / 50.75% / 48.1% — coin-flip on every split
  WIN vs LOSS mean setup score: 57.5 vs 57.5 — ZERO discriminative power
```

A follow-up phase (commit `ba03f68`, joint feature-importance check —
logistic regression + random forest on ALL features jointly, not just
univariate) found **0 of 38 features discriminate WIN/LOSS**, even in
combination. This is not a data-availability problem or an undertested
idea — it's a clean null result on abundant real data, checked twice, at
two different levels of rigor (univariate, then joint/nonlinear).

## 1. MTF cascade 30M→15M→5M→3M→1M

**PARTIALLY IMPLEMENTED, different timeframe set.** `app/strategy_mtf/`
(`mtf_config.py`, `mtf_aggregator.py`, `timeframe_bias.py`) already
implements a full weighted MTF cascade — but for `1mo/1w/1d/1h/30m/15m/5m`
(monthly-down-to-5m, monthly-heaviest), not the spec's `30m/15m/5m/3m/1m`
(30m-down-to-1m, no monthly/weekly/daily). `app/liquidity_sweep/`'s
`structure.py::htf_bias()` uses yet a third set: `15m/30m/1h/4h/1d`. None
of the three matches the spec's exact cascade. The genuinely missing piece
is specifically the **3m and 1m timeframes** — nothing in this codebase
resamples or scores at 1m/3m for structure/bias purposes (1m/3m appear
elsewhere only as raw candle timeframes for entry execution, e.g.
`app/backtest/replay.py`'s `_HARNESS_TFS`, never for structure/bias).

## 2. HH/HL/LH/LL, trend/sideways, BOS/CHoCH, swing high/low

**ALREADY IMPLEMENTED**, near-exact match. `app/liquidity_sweep/structure.py`:
- `swings()` — 3-to-N-bar fractal swing high/low detection, anti-repaint
  (a swing needs `right` future bars to confirm, matching this codebase's
  established no-look-ahead contract).
- `swing_structure_labels()` — HH/HL/LH/LL labeling, standard SMC
  definition (each swing compared to the prior swing of the *same* kind).
- `equal_levels()` — equal-highs/equal-lows liquidity pools.
- `app/liquidity_sweep/confirmation.py::structure_break()` — BOS
  (close beyond the most recent swing, continuing the prevailing
  direction) vs CHoCH (close beyond it, against the prevailing direction) —
  textbook definitions, not an invented variant.
- Trend/sideways: `structure.py::htf_bias()` returns
  BULLISH/BEARISH/RANGE/TRANSITION from an EMA(20)-vs-close read, weighted
  across timeframes.

`app/sr_dynamic/pivots.py::confirmed_swings()` is a *different* thing with
a confusingly similar name — it reuses `sr_engine._swings()` for fractal
**swing pivots** (same technique, different module, live-path), not the
classical floor-pivot (P/R1/S1) the spec's "VWAP + Pivot" requirement means
(see #4).

## 3. S/R as zones, multi-TF confluence, breakout+retest, reversal, liquidity sweep, fake-breakout filters

**PARTIALLY IMPLEMENTED.**
- Liquidity sweep: **ALREADY IMPLEMENTED**, `app/liquidity_sweep/sweep.py::
  detect_sweep()` — wick beyond a level (PDH/PDL/equal-high/equal-low) THEN
  a candle closes back inside within a bounded reclaim window (default 3
  bars). A plain breakout with no reclaim explicitly returns `None` — this
  IS the fake-breakout filter the spec asks for, already built in.
- S/R zones (not lines): **PARTIALLY** — `app/sr_engine.py` computes
  classical pivots + PDH/PDL + swing-based S/R as discrete price levels,
  not explicit "zones" with a width/tolerance. `app/liquidity_sweep/
  structure.py::equal_levels()` does cluster nearby swings into a zone
  (`tolerance_pct`), which is the closest existing "zone" concept.
  `app/sr_dynamic/` (clustering.py, engine.py) is a live-path zone/cluster
  engine — not read in depth for this pass, flagged for a closer look if
  the user wants to build on it rather than liquidity_sweep's version.
- Multi-TF confluence: **NOT IMPLEMENTED** as a discrete "does this level
  agree across 30m/15m/5m/3m/1m" check — each module computes S/R on ITS
  OWN timeframe; nothing cross-references levels across timeframes to score
  confluence.
- Breakout+retest: **NOT IMPLEMENTED** — sweep.py's reclaim window is
  sweep-then-reclaim (a rejection pattern), not breakout-then-retest-then-
  continuation (a different, currently absent pattern).
- Reversal: **PARTIALLY** — CHoCH (structure.py) is a reversal signal by
  definition; no separate "reversal setup" scoring beyond that.

## 4. "CE: price > VWAP + Pivot with bullish confirmation" (VWAP/Pivot alone must never create a trade)

**Ingredients ALREADY IMPLEMENTED, the combined gate is NOT.** Classical
floor pivots (P=(H+L+C)/3, R1=2P-L, S1=2P-H, R2/R3/S2/S3) are real and
live-path: `app/engines/sr_engine.py` lines ~41-110 computes exactly this,
and it's imported by `scalp_strategy.py`. `app/mathematical_confluence/`
has a second, independent classical-pivot implementation
(`levels.py::classical_pivots`, reusing `turning_point_engine._pivots`).
VWAP is used extensively and live (state_classifier, option_engine, etc.).
**What does not exist**: a discrete function that gates a trade on
"price > VWAP AND price > Pivot AND [PA+structure+volume confirmation]",
with an explicit rule that VWAP/Pivot alone can never fire a trade. This
specific AND-gate, and its "never alone" guarantee, would be new code —
but it would be composing existing, already-computed values, not deriving
any of them from scratch.

## 5. Sideways = no trade

**PARTIALLY IMPLEMENTED, opposite framing.** `app/strategy_mtf/
zth_sideways_gate.py::evaluate()` exists for a RANGE regime (ADX below
threshold) but does the OPPOSITE of "no trade in sideways": it's an
exception path that ALLOWS a trade during sideways ONLY when every one of
{real FVG/imbalance, sudden expansion candle, confirmed direction,
momentum, R:R ≥ 1.5} holds — i.e. it already encodes "no trade in a range
UNLESS a confirmed breakout/expansion is happening," which is arguably
consistent with (not contradictory to) the spec's "trade only confirmed
breakout/reversal" carve-out, just named and structured differently (an
allow-list exception rather than a blanket block with a carve-out).

## 6. R:R ladder T1=1:2/T2=1:3/T3=1:4, structural SL, trail only after 1:2 on confirmed candle close, never widen SL

**PARTIALLY IMPLEMENTED.** `app/liquidity_sweep/risk.py`: `MIN_RR = 2.0`
(a FLOOR, matching the spec's T1=1:2 exactly) and `build_plan()` enforces
it on the underlying's own geometry (explicitly NOT reusing
`risk_engine.run_risk_engine`'s own R:R gate, which operates on option
premium — a different, non-linear geometry — neutralized via `rr_min=0`
to avoid double-gating on incomparable numbers; position sizing IS reused
from `risk_engine`). **Missing**: T2=1:3 and T3=1:4 as a graduated
multi-target ladder — only a single 2R target exists here.
`app/strategy_mtf/target_stop.py` (146 lines, not read in depth this pass)
may have multi-target logic — flagged for a closer read if pursuing this
spec. "Never widen SL" / "trail only after 1:2 on confirmed candle close":
not confirmed either way in this pass — needs a direct read of
`target_stop.py` and the live trailing-stop mechanics already documented
in the forensic audit (`trading_logic_forensic_audit_2026-09/DEEP_DIVE.md`
Phase C — that audit already confirmed the LIVE trailing/profit-lock
ratchet is monotonic/never-loosens, for the existing autoscalp path, not
this one).

## 7. Options/strike selection via OI/ΔOI/IV/bid-ask/liquidity/Delta/Theta/expiry/premium efficiency

**ALREADY IMPLEMENTED**, close match. `app/liquidity_sweep/strikes.py`:
`StrikeScore = w1*ITM_FIT + w2*DELTA_FIT + w3*LIQUIDITY_SCORE +
w4*SPREAD_SCORE + w5*VOLUME_SCORE + w6*OI_SCORE - w7*SLIPPAGE_RISK -
w8*PREMIUM_RISK`, all weights configurable (default equal, not fitted —
matches this session's own "don't invent unjustified coefficients"
convention elsewhere, e.g. `structural_break.break_score`). This is a
deliberately separate module from the LIVE `app.engines.option_engine.
select_option` (which is ATM-centric; this one is ITM-constrained,
per an explicit brief redirect at build time) — two real, tested, distinct
strike-selection implementations already exist in this codebase.

## 8. ML lab: XGBoost as P(T1)/P(T2)/P(T3)/P(SL) probability layer, walk-forward, no override

**ALREADY IMPLEMENTED AND ALREADY FAILED THE VALIDATION BAR.**
`app/liquidity_sweep/probability.py` explicitly reuses
`app.backtest.calibration` (fit/predict/reliability_curve — the SAME
closed-form logistic calibration validated by live autoscalp, and the same
one the K8 overconfidence bug was fixed in this session, commit `04136c7`)
rather than inventing a second calibration method — probability is
explicitly kept separate from confidence, matching the spec's own
"probability and confidence are NOT the same thing."
`app/liquidity_sweep/model_check.py` is the actual ML-lab piece: L2-
regularized logistic regression AND a shallow random forest (not XGBoost
specifically, but the same class of tabular-ML check), run on ALL features
JOINTLY (catches XOR-like nonlinear combinations a univariate check would
miss), with model SELECTION done on a VALIDATION split and OOS touched
exactly once — the exact walk-forward/no-repeated-tuning discipline the
spec asks for. Result: **0/38 features discriminate WIN/LOSS**, even
jointly. This session's own established rule (XGBoost needs ≥100 labeled
events before training is justified) was moot here — the prior attempt had
2,110 real labeled signals, comfortably clearing that bar, and STILL found
nothing. More data volume is not the gap; the underlying setup definition
not carrying real predictive signal is the finding.

## What genuinely doesn't exist anywhere in this codebase

- The exact 30m→15m→5m→3m→1m cascade (1m/3m structure-scoring specifically).
- A discrete VWAP+Pivot AND-gate with an explicit "never alone" rule.
- Multi-timeframe S/R **confluence** scoring (levels agreeing across TFs).
- Breakout-then-retest-then-continuation as a distinct pattern from
  sweep-then-reclaim.
- A 3-target (T1/T2/T3 = 1:2/1:3/1:4) graduated ladder (only a single 2R
  target exists in liquidity_sweep; strategy_mtf/target_stop.py unread,
  may already have this).

## Bottom line for whoever decides what to build next

Rough coverage estimate: **~55-65%** of this spec's individual pieces
already exist somewhere in this codebase, real and tested — mostly in
`app/liquidity_sweep/`, which is structurally almost the same architecture
this new spec describes. The new spec's stricter rules (mandatory
PA+structure+volume confirmation together rather than any one alone;
"never widen SL"; per-timeframe backtest comparison) are real, sound risk-
management additions — but they address *execution discipline*, not the
specific failure mode `model_check.py` already found (the underlying
structure/sweep/CHoCH features themselves carry no measurable win/loss
signal, univariate or joint, on 2 years of real data). Nothing in the new
spec's wording suggests a mechanism that would make the SAME feature set
suddenly discriminative — it constrains *when* a trade based on those
features is taken, not *whether the features predict outcome*. Whether
that's still worth pursuing (e.g., because the new 30m→15m→5m→3m→1m
cascade and stricter multi-confirmation gate are different enough from
what was tested to be a genuinely new hypothesis) is a real, open call —
not something this read-only pass can settle.
