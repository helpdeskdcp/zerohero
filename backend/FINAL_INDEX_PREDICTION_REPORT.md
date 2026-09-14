# Final Index Prediction Report

Covers both the frozen `liquidity_sweep` baseline and this mission's pivot
to 8 alternative index-first signal hypotheses. Every numerical claim below
is traceable to an actual test/backtest artifact named in each section.

## 1. Executive Summary

Two structurally different families of index-direction hypothesis have now
been tested on real NIFTY 5m data with a strict chronological TRAIN/
VALIDATION/OOS methodology, look-ahead audits, and leakage-safe baselines:
(a) SMC/liquidity-sweep pattern detection (sweep + CHoCH/BOS + CISD/FVG/
OrderBlock), and (b) 8 classical technical-structure hypotheses (trend
continuation, breakout, mean reversion x2, pullback-in-trend, opening
range, volatility-expansion, multi-timeframe structure). **Neither family
shows a real, robust, out-of-sample directional edge.** No candidate from
either family is promoted past the initial discovery/testing stage --
Phases 8-20 of the pivot mission (calibration, walk-forward, ablation,
regime analysis, robustness, CE/PE mapping) were correctly never run,
because nothing survived Phase 6/7 to justify them.

## 2. Previous Liquidity Sweep Failure

`app/liquidity_sweep/` -- index-first SMC strategy, commit `b06ba43` +
`ba03f68`. Real 2-year Kaggle NIFTY 5m backtest, 2,110 causally-detected
signals, chronological TRAIN(47.1%)/VALIDATION(50.75%)/OOS(48.1%)
directional accuracy -- coin-flip in every split. WIN/LOSS mean setup
scores identical (57.5 vs 57.5). Extended per-feature analysis (38
features: momentum, volatility, structure, HTF regime, setup-score
components): 0 STRONG, 0 WEAK, 1 MISLEADING (large-n artifact), 8
UNAVAILABLE (traced to Kaggle NIFTY volume being 100% NULL, cascading into
an uncomputable VWAP). Leakage-safe joint model check (logistic regression
+ random forest, all 38 features): OOS AUC 0.541 vs a pre-registered 0.57
pass bar -- fails. Look-ahead audit: PASS (static import audit + future-
data mutation test + its non-vacuous counterfactual). Full detail:
`LIQUIDITY_SWEEP_FINAL_REPORT.md`, `LIQUIDITY_SWEEP_PHASE1_FEATURE_ANALYSIS.md`.
**Not modified, deleted, or re-tuned by this mission.**

## 3. Available Data

- Kaggle `research_historical.db`, table `normalized_bars`:
  - NIFTY INDEX 5m: 2015-01-09 .. 2026-05-18, 209,888 real rows, clean (no duplicate timestamps).
  - BANKNIFTY INDEX 5m: same range, but every row is stored TWICE (confirmed by direct query) --
    fixed in `app/index_signal_research/data.py` via `SELECT DISTINCT`, not used in the primary
    run to keep an apples-to-apples NIFTY-only comparison against the frozen baseline.
  - NIFTY FUTURE 1d, GOLD/SENSEX/SILVER 1d -- daily only, not usable for any intraday hypothesis here.
- `data/market_history.db` (real broker capture): `market_candles`, `quote_snapshots`, `option_greeks`,
  ~2026-09-02..2026-09-11 (10 real days) -- option-chain pipeline validation only.

## 4. Data Limitations

- **Volume is 100% NULL/0** for NIFTY and BANKNIFTY 5m in the Kaggle dataset (confirmed by direct
  query) -- no real VWAP, volume-expansion, or volume-percentile feature could be computed; none
  were fabricated. This also structurally disables 2 of the 8 `liquidity_sweep` setup-score checks
  (`VWAP_ALIGNED`, `VOLUME_ABOVE_AVERAGE`).
- No historical option-chain, Greeks, or OI exist before ~2026-09-02 -- any option-level backtest
  before that date is impossible, not merely difficult.
- Only 10 real days of option-chain data exist in total -- insufficient for any chronological-split
  statistic; used only for one-off pipeline validation (`liquidity_sweep`'s Stage 2).
- No 1m or 3m NIFTY/BANKNIFTY history exists -- all "higher timeframe" work in both missions used
  real UP-aggregation from 5m bars (`app.liquidity_sweep.resample`, reused as-is by
  `app.index_signal_research.features.add_htf_bias`), never fabricated finer granularity.

## 5. Alternative Hypotheses Tested

`app/index_signal_research/hypotheses.py`, 8 variants:

| ID | Hypothesis | Core condition |
|---|---|---|
| A | Trend continuation | 6-bar ATR-normalized expansion + 10-bar block HH/HL or LH/LL structure agreement |
| B | Breakout | Close beyond prior 20-bar high/low by >=0.3x ATR |
| C (reversion) | Mean reversion (fade) | \|price-EMA20\|>=2x ATR + RSI extreme -> bet the extreme reverts |
| C (continuation) | Mean "reversion" continuation | Same extreme -> bet it continues instead (tested both, per the brief's own instruction) |
| D | Pullback in trend | Trend structure + price back near EMA20 + confirming candle color |
| E | Opening range | Breakout of the first 30-minute (6-bar) session range |
| F | Volatility expansion | ATR >=1.5x its own 20-bar average -> bet the bar's own return sign |
| G | Multi-timeframe structure | Real (backward-joined) HTF EMA-bias agrees with D's 5m confirmation |

## 6. Methodology

- Causal, vectorized features (`features.py`): all `pandas.rolling()`/`.ewm()`/`.shift()`, verified
  look-ahead-free by a mutation test (`tests/test_index_signal_research_features.py`) identical in
  spirit to the liquidity_sweep audit -- two branches identical up to T, diverging after, must
  produce byte-identical features at T when truncated.
- Outcome label (`labels.py`) REGISTERED BEFORE any hypothesis was run: UP/DOWN/RANGE over a
  12-bar horizon at a 1.0x ATR threshold -- IDENTICAL to the frozen liquidity_sweep baseline's own
  definition, so results are directly comparable, not re-picked to make this mission's own numbers
  look better.
- Chronological TRAIN(50%)/VALIDATION(25%)/OOS(25%) split, no shuffling.
- Baseline decided on TRAIN ONLY (majority UP/DOWN direction among TRAIN's fired signals), applied
  UNCHANGED to VALIDATION/OOS -- never recomputed on the split being scored.
- Bonferroni-corrected significance (alpha=0.05/8=0.00625) across the 8 tested variants.
- **A real methodological flaw was found and fixed during this mission's own review**: a
  TRAIN-decided single-direction baseline can drift out of sync with a later split's true UP/DOWN
  base rate, making a balanced-mix signal look like it "beats baseline" purely from that drift while
  still being an exact coin flip in absolute terms (caught on hypothesis C_MEAN_REVERSION: OOS
  accuracy exactly 0.5000, p-value vs baseline 0.0012 "significant", p-value vs plain 0.5 = 1.0 --
  i.e. no real signal at all). Fixed by requiring OOS accuracy to ALSO beat plain 50/50
  (Bonferroni-significant), TRAIN accuracy to beat its OWN baseline, and VALIDATION to show at
  least a weak (p<0.10) same-direction effect -- applied uniformly to all 8 hypotheses, not
  selectively. See `app/index_signal_research/evaluate.py`'s `passes_bar` and
  `tests/test_index_signal_research_evaluate.py`'s regression tests for both the drift case and a
  second unrelated bug this same review caught (`0.0 or default` silently treating a real 0.0
  p-value as falsy in Python).

## 7. Feature Analysis

Covered by the already-completed liquidity_sweep Phase 1/2 work (38 features, 0 STRONG/WEAK) --
see `LIQUIDITY_SWEEP_PHASE1_FEATURE_ANALYSIS.md`. The pivot's 8 hypotheses each combine 2-4 of the
same underlying feature family (trend/structure/momentum/volatility) into a single testable
condition rather than re-running the full 38-feature discrimination suite -- Phase 6/7's per-
hypothesis OOS accuracy IS the discrimination test for this mission.

## 8. OOS Results

| Hypothesis | OOS n | OOS accuracy | OOS baseline | p (vs baseline) | p (vs 0.5) |
|---|---|---|---|---|---|
| A_TREND_CONTINUATION | 3093 | 0.4717 | 0.4963 | 0.0063 | 0.0018 |
| B_BREAKOUT | 790 | 0.4848 | 0.4722 | 0.4763 | 0.4132 |
| C_MEAN_REVERSION | 666 | 0.5000 | 0.4369 | 0.0012 | 1.0000 |
| C_MEAN_CONTINUATION | 666 | 0.4084 | 0.4369 | 0.1483 | 0.0000 |
| D_PULLBACK_IN_TREND | 667 | 0.5127 | 0.4858 | 0.1634 | 0.5356 |
| E_OPENING_RANGE | 5626 | 0.4993 | 0.4877 | 0.0854 | 0.9256 |
| F_VOLATILITY_EXPANSION | 143 | 0.3776 | 0.5105 | 0.0019 | 0.0043 |
| G_MTF_STRUCTURE | 131 | 0.4427 | 0.4427 | 1.0000 | 0.2211 |

Full detail incl. per-year/per-time-bucket stability:
`INDEX_SIGNAL_RESEARCH_PHASE1_7_REPORT.md`, raw JSON:
`data/research/index_signal_research/phase1_7_hypothesis_results.json`.

Notable non-result worth flagging honestly (NOT pursued further, see caveats in section 17):
F_VOLATILITY_EXPANSION's OOS accuracy (0.3776) is significantly BELOW both its baseline and 0.5,
in the wrong direction to be a usable bet as specified -- interesting only as a hint that "bet the
opposite of the naive last-candle direction during a volatility expansion" might warrant a
separately pre-registered test in a future session; treating it as a finding NOW would be exactly
the kind of post-hoc data-dredging this mission was told not to do (n=143 OOS, one calendar year
covers 135 of them -- not remotely enough to trust an inverted rule either).

## 9. Probability Calibration -- NOT APPLICABLE

No hypothesis cleared Phase 6/7's bar. Per Phase 22 ("do not build elaborate machinery before
finding an edge"), a calibrated probability model was not built for a signal with no measured
directional edge to calibrate.

## 10. Walk-Forward Results -- NOT APPLICABLE (same reason as section 9)

## 11. Ablation Results -- NOT APPLICABLE (same reason as section 9)

## 12. Regime Results

Per-year and per-time-bucket OOS breakdowns WERE computed for all 8 hypotheses (part of Phase 6/7
itself, see section 8's linked JSON) -- none show a hypothesis that is weak overall but strong in
one specific, sufficiently-sampled regime; where a hypothesis looks better in one year or time
bucket, the opposite split is typically flat or worse (e.g. D_PULLBACK_IN_TREND: 2025 OOS accuracy
0.583 vs 2026 OOS accuracy 0.473 -- not a stable regime effect, a coin flip that happened to land
on one side in the smaller sample).

## 13. Robustness Results -- NOT APPLICABLE (same reason as section 9)

## 14. Option Mapping Limitations

Unchanged from the frozen liquidity_sweep baseline: real option-chain/Greeks/OI data exists only
for ~10 calendar days (2026-09-02..2026-09-11, `data/market_history.db`). **OPTION PROFITABILITY
VALIDATION REMAINS BLOCKED BY INSUFFICIENT HISTORICAL OPTION DATA** -- unchanged by this mission,
since no index-direction candidate reached the point of needing a CE/PE mapping test.

## 15. Best Candidate

**NONE.** No hypothesis, in either the frozen liquidity_sweep baseline or this mission's 8
alternative hypotheses, clears its own pre-registered, leakage-safe, Bonferroni-corrected pass bar
on held-out data.

## 16. Failed Candidates

- `liquidity_sweep` (SMC sweep+CHoCH/BOS+CISD/FVG/OB): OOS directional accuracy 48.1% (coin-flip),
  0/38 features discriminative, joint-model OOS AUC 0.541 (fails 0.57 bar).
- A_TREND_CONTINUATION: OOS accuracy 0.4717, BELOW its own baseline (0.4963) -- underperforms.
- B_BREAKOUT: OOS accuracy 0.4848, not significantly different from baseline (p=0.476).
- C_MEAN_REVERSION: OOS accuracy exactly 0.5000 -- a literal coin flip (p vs 0.5 = 1.0).
- C_MEAN_CONTINUATION: OOS accuracy 0.4084, BELOW baseline and BELOW 0.5.
- D_PULLBACK_IN_TREND: OOS accuracy 0.5127 but not Bonferroni-significant (p=0.163) and not stable
  across years (0.583 in 2025 vs 0.473 in 2026).
- E_OPENING_RANGE: OOS accuracy 0.4993, essentially a coin flip (p vs 0.5 = 0.926).
- F_VOLATILITY_EXPANSION: OOS accuracy 0.3776, significantly WORSE than baseline and 0.5 (wrong
  direction to use as specified; see section 8's caveat).
- G_MTF_STRUCTURE: OOS accuracy 0.4427, identical to its own baseline (p=1.0) -- adds nothing.

## 17. Statistical Caveats

- 8 hypothesis variants were tested; Bonferroni correction (alpha=0.05/8=0.00625) was applied to
  every OOS significance check, not the nominal 0.05, specifically to guard against exactly the
  "one hypothesis crosses p<0.05 by chance among several tested" risk Phase 7 warns about.
- The TRAIN-decided-baseline drift issue (section 6) means a naive single-baseline comparison is
  NOT sufficient on its own for this kind of unconditional-direction labeling; the additional
  vs-0.5 check is now load-bearing and should be carried into any future extension of this
  framework, not dropped as "redundant."
- Every reported number here is a single train/val/oos split, not a rolling walk-forward average
  (Phase 8 was not run -- see section 10) -- a hypothesis could behave differently under a rolling
  re-fit; this was not tested because nothing passed the initial gate that would justify the extra
  computational and reporting cost.
- F_VOLATILITY_EXPANSION's below-baseline result (section 8) is flagged but NOT treated as a
  discovery -- acting on a single post-hoc inverted reading of an already-multiple-tested hypothesis
  would be a fresh data-snooping violation, not a genuine finding.

## 18. Final Verdict

**NO ROBUST EDGE FOUND.** Across two structurally different hypothesis families (SMC pattern
detection, and 8 classical technical-structure rules) tested with consistent, honest, leakage-safe
methodology on the same real 2-year NIFTY 5m dataset, none demonstrates a directional edge that
survives out-of-sample testing. This is an acceptable, reportable result, not a failure of the
engineering process (per the mission's own framing).

## 19. Required Next Data

1. Real intraday volume/order-flow/OI data for NIFTY/BANKNIFTY (currently 100% absent) -- opens an
   entire feature family (volume-confirmed breakouts, real VWAP mean reversion, order-flow
   imbalance) that is structurally untestable on the current dataset.
2. More than 10 real days of option-chain/Greeks/OI history -- required before ANY option-level
   profitability claim (as opposed to pipeline validation) can be attempted, independent of whether
   a future index-direction hypothesis succeeds.
3. Tick-level or 1m data, if a shorter-horizon (sub-5-minute) hypothesis is ever wanted -- not
   fabricable from the existing 5m bars.

## 20. Recommended Next Engineering Step

Per Phase 22's own instruction ("do not build elaborate machinery before finding an edge"), the
recommended next step is NOT to build more index-first infrastructure on the current dataset --
two independent hypothesis families have now failed on it. Instead:
1. Source real intraday volume/order-flow data for NIFTY/BANKNIFTY (see section 19.1) before
   testing any further price-structure-only hypothesis, since several of the untested feature
   families (real VWAP, volume confirmation) are specifically the kind of information that could
   plausibly discriminate WIN/LOSS where price-structure alone has now twice failed to.
2. If new data cannot be sourced, consider a hypothesis class not yet tried on ANY dataset in this
   codebase (cross-asset lead-lag, e.g. NIFTY-vs-BANKNIFTY or futures-vs-index divergence) rather
   than re-testing more variants of trend/breakout/reversion on the same 5m OHLC-only feature set.
3. Do not resume liquidity_sweep-style SMC pattern work or re-tune any of the 8 hypotheses above --
   both are now closed lines of inquiry on this dataset, per this report's own verdict.
