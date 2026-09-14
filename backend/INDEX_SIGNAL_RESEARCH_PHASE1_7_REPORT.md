# Index-First Signal Research -- Phase 1-7 Report

Pivot mission, following the frozen `liquidity_sweep` NOT READY verdict (see `LIQUIDITY_SWEEP_FINAL_REPORT.md` / `LIQUIDITY_SWEEP_PHASE1_FEATURE_ANALYSIS.md`, not modified or re-tuned by anything here). Tests 8 distinct, independently falsifiable index-direction hypotheses on the SAME real 2-year Kaggle NIFTY 5m window the baseline used, with the SAME chronological TRAIN/VALIDATION/OOS discipline, a leakage-safe baseline decided on TRAIN only, and a Bonferroni-corrected pass bar (alpha=0.00625 across 8 hypothesis variants).

## Phase 1-2 -- data audit

- NIFTY INDEX 5m: 2015-01-09 .. 2026-05-18, 209,888 real rows, no duplicate timestamps (confirmed by direct query).
- BANKNIFTY INDEX 5m: same date range, but EVERY row is stored TWICE in the source table (confirmed by direct query) -- `data.load_index_5m` fixes this with `SELECT DISTINCT`; not used in this run to keep an apples-to-apples comparison against the baseline's own NIFTY-only test.
- Volume: 100% NULL/0 for NIFTY/BANKNIFTY 5m (confirmed) -- no volume-derived feature (real VWAP, volume expansion) was computed; not fabricated, not attempted.
- market_history.db (real 10-day option-chain capture) reserved for Phase 15/16 CE/PE mapping validation only, never for these long-term index-hypothesis statistics -- 10 days is not a usable chronological-split sample size.

- Bars walked: 43967
- Graded bars (complete forward horizon): 43955 (UP 20921, DOWN 21204, RANGE 1830)
- Label definition: horizon=12 bars, threshold=1.0x ATR -- IDENTICAL to the frozen liquidity_sweep baseline's own label definition (not re-picked for this mission).

## Phase 6/7 -- per-hypothesis results (OOS is the number that matters)

| Hypothesis | TRAIN n / acc (vs base) | VAL n / acc (vs base) | OOS n / acc | OOS baseline | OOS p (vs base) | OOS p (vs 0.5) | Passes bar? |
|---|---|---|---|---|---|---|---|
| A_TREND_CONTINUATION | 5743 / 0.4822 (base 0.4846) | 2802 / 0.4911 (p=0.0005) | 3093 / 0.4717 | 0.4963 | 0.0063 | 0.0018 | no |
| B_BREAKOUT | 1469 / 0.4990 (base 0.4833) | 762 / 0.4843 (p=0.4047) | 790 / 0.4848 | 0.4722 | 0.4763 | 0.4132 | no |
| C_MEAN_REVERSION | 1146 / 0.4328 (base 0.4721) | 626 / 0.4633 (p=0.9044) | 666 / 0.5000 | 0.4369 | 0.0012 | 1.0000 | no |
| C_MEAN_CONTINUATION | 1146 / 0.5105 (base 0.4721) | 626 / 0.4681 (p=0.9362) | 666 / 0.4084 | 0.4369 | 0.1483 | 0.0000 | no |
| D_PULLBACK_IN_TREND | 1506 / 0.4960 (base 0.4900) | 759 / 0.4954 (p=0.0014) | 667 / 0.5127 | 0.4858 | 0.1634 | 0.5356 | no |
| E_OPENING_RANGE | 12161 / 0.4983 (base 0.4927) | 5205 / 0.4834 (p=0.6275) | 5626 / 0.4993 | 0.4877 | 0.0854 | 0.9256 | no |
| F_VOLATILITY_EXPANSION | 366 / 0.4590 (base 0.4918) | 245 / 0.5020 (p=0.6099) | 143 / 0.3776 | 0.5105 | 0.0019 | 0.0043 | no |
| G_MTF_STRUCTURE | 332 / 0.5030 (base 0.4940) | 123 / 0.5041 (p=0.5894) | 131 / 0.4427 | 0.4427 | 1.0000 | 0.2211 | no |

`passes_bar` requires ALL of: OOS accuracy beats both its TRAIN-decided baseline AND plain 50/50 (the second check exists because a TRAIN-decided single-direction baseline can drift out of sync with a later split's true UP/DOWN base rate, making a balanced-mix signal look like it 'beats baseline' from that drift alone, while still being indistinguishable from a coin flip in absolute terms -- found and fixed during this mission's own review, see evaluate.py), Bonferroni-significant against both, n>=20, TRAIN accuracy >= TRAIN baseline (not merely OOS), and VALIDATION shows at least a weak (p<0.10) same-direction effect -- a hypothesis that only 'works' on the one untouched split is not stable, per Phase 20's own acceptance criteria.

## Verdict

**NO ROBUST EDGE FOUND.** None of the 8 tested hypothesis variants (trend continuation, breakout, mean reversion x2, pullback-in-trend, opening range, volatility-expansion, multi-timeframe structure) beats its own TRAIN-decided baseline on held-out OOS data at the Bonferroni-corrected significance level (alpha=0.00625). This is consistent with, not contradicted by, the frozen liquidity_sweep baseline's own null result -- across two structurally different families of hypotheses (SMC/liquidity-sweep pattern detection, and now these 8 classical technical-structure hypotheses), on the same real 2-year NIFTY 5m dataset, none shows a real directional edge.

Per Phase 22 ("do not build elaborate machinery before finding an edge") and the mission's own instruction not to force a positive conclusion, this driver STOPS here -- Phases 8-20 (walk-forward, probability calibration, confidence, no-trade engine, ablation, regime analysis, CE/PE mapping, robustness) are not run, because there is no candidate to carry into them.

## What would actually be required next

1. **A fundamentally different data source**: real intraday volume/OI/tick data for NIFTY/BANKNIFTY (completely absent from the Kaggle dataset) would open up an entire feature family (order-flow imbalance, volume-confirmed breakouts, VWAP mean reversion) that is structurally untestable here.
2. **A different instrument or timeframe class** -- these 8 hypotheses were tested on 5m bars only (the finest granularity Kaggle provides); a genuinely different holding-period hypothesis (multi-day swing, not intraday) was already tested and rejected separately (see the project's own `trend-swing-nogo` research).
3. **A fundamentally different signal class** not yet tried in this codebase: cross-asset/cross-index lead-lag relationships, options-market-implied positioning (once more than 10 real days of option-chain history exist), or macro/event-driven conditioning -- none of which this dataset can currently support.

## Full per-hypothesis detail (train/val/oos + per-year + per-time-bucket)

Raw JSON: `data/research/index_signal_research/phase1_7_hypothesis_results.json`

### A_TREND_CONTINUATION
- OOS accuracy 0.4717 vs baseline 0.4963, p=0.006272 (Bonferroni alpha=0.00625, n=3093)
- Per-year OOS: {"2025": {"n_signals": 1184, "accuracy": 0.4637, "baseline_accuracy": 0.4561}, "2026": {"n_signals": 1909, "accuracy": 0.4767, "baseline_accuracy": 0.5212}}
- Per-time-bucket OOS: {"OPENING": {"n_signals": 494, "accuracy": 0.498, "baseline_accuracy": 0.5385}, "MID": {"n_signals": 2053, "accuracy": 0.4691, "baseline_accuracy": 0.4803}, "CLOSING": {"n_signals": 546, "accuracy": 0.4579, "baseline_accuracy": 0.5183}}

### B_BREAKOUT
- OOS accuracy 0.4848 vs baseline 0.4722, p=0.476324 (Bonferroni alpha=0.00625, n=790)
- Per-year OOS: {"2025": {"n_signals": 297, "accuracy": 0.4815, "baseline_accuracy": 0.4882}, "2026": {"n_signals": 493, "accuracy": 0.4868, "baseline_accuracy": 0.4625}}
- Per-time-bucket OOS: {"OPENING": {"n_signals": 185, "accuracy": 0.4973, "baseline_accuracy": 0.4216}, "MID": {"n_signals": 466, "accuracy": 0.4764, "baseline_accuracy": 0.4764}, "CLOSING": {"n_signals": 139, "accuracy": 0.4964, "baseline_accuracy": 0.5252}}

### C_MEAN_REVERSION
- OOS accuracy 0.5 vs baseline 0.4369, p=0.001171 (Bonferroni alpha=0.00625, n=666)
- Per-year OOS: {"2025": {"n_signals": 175, "accuracy": 0.5143, "baseline_accuracy": 0.4514}, "2026": {"n_signals": 491, "accuracy": 0.4949, "baseline_accuracy": 0.4318}}
- Per-time-bucket OOS: {"OPENING": {"n_signals": 303, "accuracy": 0.4851, "baseline_accuracy": 0.4455}, "MID": {"n_signals": 301, "accuracy": 0.5116, "baseline_accuracy": 0.4186}, "CLOSING": {"n_signals": 62, "accuracy": 0.5161, "baseline_accuracy": 0.4839}}

### C_MEAN_CONTINUATION
- OOS accuracy 0.4084 vs baseline 0.4369, p=0.148325 (Bonferroni alpha=0.00625, n=666)
- Per-year OOS: {"2025": {"n_signals": 175, "accuracy": 0.3886, "baseline_accuracy": 0.4514}, "2026": {"n_signals": 491, "accuracy": 0.4155, "baseline_accuracy": 0.4318}}
- Per-time-bucket OOS: {"OPENING": {"n_signals": 303, "accuracy": 0.3993, "baseline_accuracy": 0.4455}, "MID": {"n_signals": 301, "accuracy": 0.4153, "baseline_accuracy": 0.4186}, "CLOSING": {"n_signals": 62, "accuracy": 0.4194, "baseline_accuracy": 0.4839}}

### D_PULLBACK_IN_TREND
- OOS accuracy 0.5127 vs baseline 0.4858, p=0.163429 (Bonferroni alpha=0.00625, n=667)
- Per-year OOS: {"2025": {"n_signals": 240, "accuracy": 0.5833, "baseline_accuracy": 0.475}, "2026": {"n_signals": 427, "accuracy": 0.4731, "baseline_accuracy": 0.4918}}
- Per-time-bucket OOS: {"OPENING": {"n_signals": 33, "accuracy": 0.3333, "baseline_accuracy": 0.3333}, "MID": {"n_signals": 483, "accuracy": 0.5569, "baseline_accuracy": 0.501}, "CLOSING": {"n_signals": 151, "accuracy": 0.4106, "baseline_accuracy": 0.4702}}

### E_OPENING_RANGE
- OOS accuracy 0.4993 vs baseline 0.4877, p=0.085357 (Bonferroni alpha=0.00625, n=5626)
- Per-year OOS: {"2025": {"n_signals": 2346, "accuracy": 0.4979, "baseline_accuracy": 0.4979}, "2026": {"n_signals": 3280, "accuracy": 0.5003, "baseline_accuracy": 0.4805}}
- Per-time-bucket OOS: {"OPENING": {"n_signals": 108, "accuracy": 0.3981, "baseline_accuracy": 0.4722}, "MID": {"n_signals": 4248, "accuracy": 0.5066, "baseline_accuracy": 0.5021}, "CLOSING": {"n_signals": 1270, "accuracy": 0.4835, "baseline_accuracy": 0.4409}}

### F_VOLATILITY_EXPANSION
- OOS accuracy 0.3776 vs baseline 0.5105, p=0.001867 (Bonferroni alpha=0.00625, n=143)
- Per-year OOS: {"2025": {"n_signals": 8, "status": "INSUFFICIENT_SAMPLE"}, "2026": {"n_signals": 135, "accuracy": 0.3704, "baseline_accuracy": 0.5185}}
- Per-time-bucket OOS: {"OPENING": {"n_signals": 130, "accuracy": 0.3538, "baseline_accuracy": 0.5077}, "MID": {"n_signals": 11, "status": "INSUFFICIENT_SAMPLE"}, "CLOSING": {"n_signals": 2, "status": "INSUFFICIENT_SAMPLE"}}

### G_MTF_STRUCTURE
- OOS accuracy 0.4427 vs baseline 0.4427, p=1.0 (Bonferroni alpha=0.00625, n=131)
- Per-year OOS: {"2025": {"n_signals": 18, "status": "INSUFFICIENT_SAMPLE"}, "2026": {"n_signals": 113, "accuracy": 0.4602, "baseline_accuracy": 0.4248}}
- Per-time-bucket OOS: {"OPENING": {"n_signals": 4, "status": "INSUFFICIENT_SAMPLE"}, "MID": {"n_signals": 93, "accuracy": 0.5161, "baseline_accuracy": 0.4086}, "CLOSING": {"n_signals": 34, "accuracy": 0.2647, "baseline_accuracy": 0.5}}
