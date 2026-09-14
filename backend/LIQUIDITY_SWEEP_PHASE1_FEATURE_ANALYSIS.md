# Liquidity Sweep -- Phase 1 Per-Signal Feature Discrimination Analysis

Index-First Prediction Engine mission, Phase 0 (architecture) + Phase 1 (measure, don't assume) + part of Phase 2 (formal statistical tests). Additive to, and does not replace, `LIQUIDITY_SWEEP_FINAL_REPORT.md` (Stage 1/2 backtest, verdict NOT READY) -- same underlying signal set, far deeper per-feature measurement.

## Phase 0 -- what changed vs. what was reused

- **Reused unchanged, tested**: `_walk_raw_signals` (signal-finding walk, cooldown dedup, chronological split), all sweep/CHoCH/CISD/FVG/OrderBlock/risk/strikes detection, `app.backtest.calibration` for probability fitting.
- **New, additive**: `app/liquidity_sweep/resample.py` (real 5m->15m/30m/1h/4h/1d up-aggregation), `backtest._build_bars_by_tf_at` + `_walk_raw_signals_with_features` (2-pass: cheap signal-finding pass unchanged, then real-HTF feature enrichment only at the ~2,000-ish found signal indices), `app/liquidity_sweep/feature_analysis.py` (Cohen's d / Mann-Whitney U / Cramer's V / chi-square / mutual information + a fixed, documented STRONG/WEAK/REDUNDANT/MISLEADING/UNAVAILABLE/NONE categorization rule).
- **Bug found and fixed for this analysis (not yet in the live `engine.py` scoring path)**: Stage 1's original walk called `engine.evaluate(bars_by_tf={"5m": ...})` only -- `structure.htf_bias()` only recognises 15m/30m/1h/4h/1d, so EVERY one of the 2,110 original signals silently got regime="RANGE"/htf_score=0.0 (the function's own documented fallback for "no timeframe has enough history"), not a real measurement. This analysis supplies genuine resampled HTF bars so `regime` is measured for the first time. This does NOT retroactively invalidate the original NOT READY verdict (no NO_TRADE gate in `engine.py` depends on htf_bias -- the same 2,110-ish raw signals fire either way; only their setup_score/probability/regime tags were previously meaningless).
- **Also newly measured (previously dead/unwired)**: setup_score's 8th check, `VOLUME_ABOVE_AVERAGE`, is never actually populated by `engine.py`'s real call to `setup_score.compute` (it never passes `avg_volume`/`sweep_bar_volume`) -- this analysis independently recomputes what that check's value WOULD be (`chk_volume_above_average`), to measure whether it's worth wiring up.

## Dataset

- Real Kaggle NIFTY 5m OHLC, 2-year window (same as the original Stage 1 backtest)
- Bars walked: 43967
- Signals captured with full feature set: **2110**
- WIN: 624 | LOSS: 668 | TIMEOUT (excluded from discrimination, per `label_outcome`'s own convention): 818

## Category summary

- **STRONG** (0): none
- **WEAK** (0): none
- **MISLEADING** (1): structure_type
- **UNAVAILABLE** (8): above_vwap, chk_secondary_confirmation, chk_structure_aligned, chk_volume_above_average, chk_vwap_aligned, rr, volume, volume_ratio
- **NONE** (29): above_ema20, adx, atr14, bars_to_reclaim, chk_adx_trending, chk_ema_aligned, chk_htf_aligned, chk_rsi_not_extreme, cisd, confidence, day_of_week, direction, ema20_gt_ema50, fvg, htf_score, level_source, macd, order_block, passed_checks, probability, regime, reward_amount, risk_amount, rsi14, setup_score, sweep_kind, sweep_reaction, sweep_reaction_atr_ratio, time_bucket
- **REDUNDANT pairs**: none found (|r| >= 0.8 threshold)

## Per-feature results

Categorization rule is fixed and documented in `feature_analysis.py`'s module docstring, decided BEFORE running this script -- not tuned after seeing results.

| Feature | Type | N available | Missing % | WIN mean | LOSS mean | Effect size | p-value | Mutual info | Category |
|---|---|---|---|---|---|---|---|---|---|
| setup_score | continuous | 1292 | 0% | 60.5569 | 60.7223 | cohens_d=-0.0195 | 0.6529 | 0.0000 | **NONE** |
| passed_checks | continuous | 1292 | 0% | 4.8446 | 4.8578 | cohens_d=-0.0195 | 0.6529 | 0.0000 | **NONE** |
| htf_score | continuous | 1292 | 0% | 0.0152 | 0.0210 | cohens_d=-0.0329 | 0.8309 | 0.0000 | **NONE** |
| sweep_reaction | continuous | 1292 | 0% | 34.1505 | 32.5443 | cohens_d=0.0682 | 0.2744 | 0.0000 | **NONE** |
| sweep_reaction_atr_ratio | continuous | 1292 | 0% | 1.5200 | 1.4762 | cohens_d=0.0609 | 0.2303 | 0.0010 | **NONE** |
| bars_to_reclaim | continuous | 1292 | 0% | 2.4503 | 2.3772 | cohens_d=0.0957 | 0.2339 | 0.0122 | **NONE** |
| rsi14 | continuous | 1292 | 0% | 50.2580 | 51.0465 | cohens_d=-0.0455 | 0.3543 | 0.0000 | **NONE** |
| macd | continuous | 1292 | 0% | 1.2226 | 0.1743 | cohens_d=0.0560 | 0.5563 | 0.0000 | **NONE** |
| adx | continuous | 1292 | 0% | 35.6466 | 35.3649 | cohens_d=0.0120 | 0.6396 | 0.0000 | **NONE** |
| atr14 | continuous | 1292 | 0% | 22.2973 | 21.6270 | cohens_d=0.0709 | 0.2985 | 0.0000 | **NONE** |
| volume | continuous | 1292 | 0% | 0.0000 | 0.0000 | cohens_d=n/a | 1.0000 | 0.0090 | **UNAVAILABLE** |
| volume_ratio | continuous | 0 | 100% | n/a | n/a | cohens_d=n/a | n/a | n/a | **UNAVAILABLE** |
| probability | continuous | 1292 | 0% | 0.5146 | 0.5155 | cohens_d=-0.0196 | 0.6529 | 0.0000 | **NONE** |
| confidence | continuous | 1292 | 0% | 55.3471 | 55.2955 | cohens_d=0.0061 | 0.8616 | 0.0000 | **NONE** |
| rr | continuous | 1292 | 0% | 2.0000 | 2.0000 | cohens_d=n/a | 1.0000 | 0.0090 | **UNAVAILABLE** |
| risk_amount | continuous | 1292 | 0% | 48.6145 | 46.4589 | cohens_d=0.0791 | 0.1285 | 0.0000 | **NONE** |
| reward_amount | continuous | 1292 | 0% | 97.2290 | 92.9178 | cohens_d=0.0791 | 0.1285 | 0.0000 | **NONE** |
| direction | boolean | 1292 | 0% | {'BEARISH': 316, 'BULLISH': 308} | {'BULLISH': 349, 'BEARISH': 319} | cramers_v=0.0273 | 0.3264 | 0.0004 | **NONE** |
| regime | categorical | 1292 | 0% | {'RANGE': 136, 'TRANSITION': 249, 'BULLISH': 139, 'BEARISH': 100} | {'RANGE': 151, 'TRANSITION': 255, 'BULLISH': 157, 'BEARISH': 105} | cramers_v=0.0211 | 0.9023 | 0.0002 | **NONE** |
| structure_type | boolean | 1292 | 0% | {'BOS': 187, 'CHOCH': 437} | {'CHOCH': 430, 'BOS': 238} | cramers_v=0.0586 | 0.0353 | 0.0018 | **MISLEADING** |
| cisd | boolean | 1292 | 0% | {'False': 435, 'True': 189} | {'True': 215, 'False': 453} | cramers_v=0.0188 | 0.4997 | 0.0002 | **NONE** |
| fvg | boolean | 1292 | 0% | {'False': 193, 'True': 431} | {'False': 230, 'True': 438} | cramers_v=0.0356 | 0.2002 | 0.0007 | **NONE** |
| order_block | boolean | 1292 | 0% | {'True': 153, 'False': 471} | {'False': 486, 'True': 182} | cramers_v=0.0293 | 0.2919 | 0.0005 | **NONE** |
| sweep_kind | boolean | 1292 | 0% | {'UPPER_SWEEP': 316, 'LOWER_SWEEP': 308} | {'LOWER_SWEEP': 349, 'UPPER_SWEEP': 319} | cramers_v=0.0273 | 0.3264 | 0.0004 | **NONE** |
| level_source | categorical | 1292 | 0% | {'EQUAL_HIGH': 300, 'EQUAL_LOW': 278, 'PDL': 24, 'PDH': 22} | {'EQUAL_HIGH': 311, 'EQUAL_LOW': 306, 'PDH': 29, 'PDL': 22} | cramers_v=0.0291 | 0.7792 | 0.0004 | **NONE** |
| chk_structure_aligned | boolean | 1292 | 0% | {'True': 624} | {'True': 668} | cramers_v=n/a | n/a | 0.0000 | **UNAVAILABLE** |
| chk_secondary_confirmation | boolean | 1292 | 0% | {'True': 624} | {'True': 668} | cramers_v=n/a | n/a | 0.0000 | **UNAVAILABLE** |
| chk_htf_aligned | boolean | 1292 | 0% | {'False': 471, 'True': 153} | {'False': 498, 'True': 170} | cramers_v=0.0089 | 0.7479 | 0.0001 | **NONE** |
| chk_vwap_aligned | boolean | 1292 | 0% | {'False': 624} | {'False': 668} | cramers_v=n/a | n/a | 0.0000 | **UNAVAILABLE** |
| chk_ema_aligned | boolean | 1292 | 0% | {'True': 612, 'False': 12} | {'True': 653, 'False': 15} | cramers_v=0.0058 | 0.8335 | 0.0001 | **NONE** |
| chk_rsi_not_extreme | boolean | 1292 | 0% | {'True': 580, 'False': 44} | {'True': 631, 'False': 37} | cramers_v=0.0280 | 0.3145 | 0.0005 | **NONE** |
| chk_adx_trending | boolean | 1292 | 0% | {'True': 430, 'False': 194} | {'True': 455, 'False': 213} | cramers_v=0.0069 | 0.8041 | 0.0000 | **NONE** |
| chk_volume_above_average | boolean | 1292 | 0% | {'False': 624} | {'False': 668} | cramers_v=n/a | n/a | 0.0000 | **UNAVAILABLE** |
| above_vwap | boolean | 0 | 100% | n/a | n/a | cramers_v=n/a | n/a | n/a | **UNAVAILABLE** |
| above_ema20 | boolean | 1292 | 0% | {'False': 308, 'True': 316} | {'True': 354, 'False': 314} | cramers_v=0.0220 | 0.4294 | 0.0003 | **NONE** |
| ema20_gt_ema50 | boolean | 1292 | 0% | {'False': 293, 'True': 331} | {'True': 366, 'False': 302} | cramers_v=0.0159 | 0.5665 | 0.0002 | **NONE** |
| time_bucket | categorical | 1292 | 0% | {'MID': 416, 'CLOSING': 155, 'OPENING': 53} | {'CLOSING': 157, 'MID': 444, 'OPENING': 67} | cramers_v=0.0287 | 0.5884 | 0.0004 | **NONE** |
| day_of_week | categorical | 1292 | 0% | {'Friday': 116, 'Monday': 107, 'Tuesday': 128, 'Wednesday': 141, 'Thursday': 128, 'Saturday': 1, 'Sunday': 3} | {'Friday': 134, 'Monday': 131, 'Wednesday': 137, 'Thursday': 121, 'Saturday': 6, 'Tuesday': 138, 'Sunday': 1} | cramers_v=0.0758 | 0.2831 | 0.0030 | **NONE** |

## Headline finding

**No feature in this set -- individually -- shows a real (STRONG or WEAK), non-misleading discriminative relationship between WIN and LOSS outcomes.** This is consistent with, and deepens, the original Stage 1 finding (WIN/LOSS setup-score means were statistically identical, 57.5 vs 57.5): it is not just the combined 8-check score that fails to discriminate -- none of its individual components, nor any of the additional momentum/volatility/volume/structure/HTF features captured here, do either, on this real 2-year NIFTY 5m dataset with this signal-detection logic. This does not prove no possible feature set could ever work; it means this specific, fairly comprehensive set does not, honestly measured rather than assumed.

## Multiple-comparisons caveat

38 features were tested independently at alpha=0.05/0.01. No Bonferroni/FDR correction was applied to the category thresholds above -- treat any single WEAK/STRONG result as a hypothesis to re-test on a fresh split, not a confirmed edge, especially if the number of STRONG+WEAK features found is small relative to what chance alone would produce.

Raw per-signal feature CSV: `data/research/liquidity_sweep/phase1_signal_features.csv` (2110 rows, 41 columns) -- for independent re-analysis.

## Phase 2 completion -- leakage-safe joint feature check

Individual features can each look like noise while a REAL combination still predicts the outcome (e.g. XOR of two features) -- univariate tests above cannot see this. This runs two standard models (L2 logistic regression; a shallow random forest, to also catch nonlinear interactions) on ALL features jointly, using the same TRAIN -> VALIDATION -> OOS chronological split discipline as the original Stage 1 backtest -- OOS touched exactly once.

- Train/Val/OOS sizes: 646 / 323 / 323 (base rates 47.2% / 50.1% / 48.6%)
- Logistic regression: VAL AUC 0.5122, OOS AUC 0.5410, OOS accuracy 0.5325, OOS Brier 0.2523
- Random forest: VAL AUC 0.4928, OOS AUC 0.5452, OOS accuracy 0.5356, OOS Brier 0.2489
- OOS majority-class baseline accuracy: 0.5139 (selected model: **logistic**)
- **Pass bar (OOS AUC >= 0.57)**: **DOES NOT PASS** -- logistic OOS AUC 0.541 does not clear 0.5+0.07 (0.57) -- no combination of these features, jointly, predicts WIN/LOSS on held-out data either

Top-10 random forest feature importances (diagnostic only -- not evidence of edge on its own, since the model as a whole did not pass the bar):

  - reward_amount: 0.0787
  - confidence: 0.0766
  - macd: 0.0765
  - rsi14: 0.0737
  - adx: 0.0719
  - risk_amount: 0.0680
  - atr14: 0.0668
  - sweep_reaction: 0.0639
  - sweep_reaction_atr_ratio: 0.0637
  - htf_score: 0.0550
