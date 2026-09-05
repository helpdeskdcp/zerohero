# Calibration Overconfidence — Root-Cause Investigation

**Date:** 2026-09-05 · **Mode:** READ-ONLY. No production config, trading logic, threshold,
calibration parameter, frozen NIFTY setting, or `live_trading` setting was changed while
producing this document. Every number below is a live read of `scalp_signals` /
`app_settings` or a static read of the source files named. **No fix is implemented here.**

---

## 0. Headline numbers (unchanged from the initial check)

74 resolved LIVE `AUTOSCALP` signals: actual win-rate 47.3%, mean predicted win 57.7%,
ECE 0.115, Brier 0.282 (worse than the naive "always guess base rate" score of ~0.25),
profit factor 0.75, expectancy -0.54 pts/trade. `verdict: OVERCONFIDENT` is the system's
own flag (`GET /api/autoscalp/calibration-report`), not this report's interpretation.

Score-bucket win-rate is **non-monotonic** — the model's core assumption (higher score →
higher win probability) is empirically violated in this sample:

| score bucket | n | actual win% |
|---|---|---|
| 40-50 | 15 | 66.7% |
| 50-60 | 19 | 47.4% |
| 60-70 | 23 | **34.8%** ← worse than the *lower*-scored bucket |
| 70-80 | 12 | 50.0% |

---

## 1. Complete pipeline trace

```
raw features (candles, chain, greeks)
  -> app/engines/sr_engine.compute_sr()          [S/R zones, ATR, VWAP, GEX]
  -> app/engines/regime_mtf.detect_regime()       [regime label]
  -> app/engines/regime_mtf.mtf_alignment()       [signed -100..+100 "mtf" magnitude]
  -> app/engines/state_classifier.classify()      [state_score 0-100, 9 weighted components]
  -> app/engines/option_engine.analyse_leg()/
     select_option()                              [final_quality 0-100 per option leg]
  -> app/engines/scalp_strategy.decide_from_context()
       blended = 0.62*state_score + 0.24*final_quality + 0.14*min(100, mtf_magnitude+40)
       blended *= regime_score_mult * signal_type_score_mult * tod_score_mult
  -> app/engines/scalp_strategy._score_to_prob(blended, calib, regime, signal_type)
       s = blended/100
       prior = sigmoid(2.3*(s-0.58))                          [used until a curve exists]
       fitted = sigmoid(curve.k*(s-0.5) + curve.b)             [used once one exists]
  -> app/backtest/calibration.fit() / autoscalp/runner._maybe_recalibrate()
       refits a GLOBAL logistic curve every 900s once >=40 resolved LIVE samples exist
  -> app/autoscalp/runner.py db.insert_scalp_signal(...)       [probability frozen at this instant]
  -> app/engines/paper_trading.close_trade() -> outcome WIN/LOSS/FLAT written
  -> app/autoscalp/calibration_report.py                       [reads the frozen probability
                                                                  + the outcome, computes Brier/
                                                                  ECE/reliability -- this is what
                                                                  flagged OVERCONFIDENT]
```

### 1a. Exact formulas, by stage

**State score** (`app/engines/state_classifier.py:29,112-113,334`):
```
_W = {price_action:.20, level_strength:.16, volume:.12, oi:.12,
      momentum:.14, vwap:.08, atr:.08, htf:.06, retest:.04}   # sums to 1.00
raw_score   = round(100 * sum(comp[k] * _W[k] for k in _W), 1)
state_score = round(raw_score * (1 - false_risk.penalty), 1)   # penalty in {0, .25, .5, .8}
```
Each `comp[k]` is itself a 0-1 sub-score from a dedicated helper inside `_eval_break`/
`_eval_reversal` (e.g. `htf` from `_htf_trend()`, `oi` from `_oi_at()` — see §3).

**Option quality** (`app/engines/option_engine.py:26-29,127-131`):
```
_QW = {liquidity:.24, translation:.22, delta_fit:.16, premium_fit:.12,
       atm_proximity:.12, own_trend:.10, theta:.04}          # sums to 1.00
quality = 100 * sum(_QW[k] * component[k] for k in _QW)      # atm_proximity filled with 0.5 placeholder here
final_quality = select_option() re-scores atm_proximity with the real per-candidate value
```

**MTF magnitude** (`app/engines/regime_mtf.py:23-24,68-80`): weighted sum of a 9/21-EMA
directional read across 1m(10%)/3m(15%)/5m(22%)/15m(26%)/30m(27%), signed -100..+100.

**Top-level blend** (`app/engines/scalp_strategy.py:314-320`):
```
blended = 0.62*state_score + 0.24*final_quality + 0.14*min(100, mtf_magnitude+40)
blended *= regime_score_mult[regime] * signal_type_score_mult[signal_type] * tod_score_mult[tod]
blended = clamp(blended, 0, 100)
```

**Score -> probability** (`app/engines/scalp_strategy.py:60-75`):
```
s = clamp(blended, 0, 100) / 100
prior = 1 / (1 + exp(-(2.3*(s-0.58))))                       # fixed, never fitted
curve = calib.curves[f"{regime}|{signal_type}"] or calib.curves[f"*|{signal_type}"] or calib.global
probability = prior                          if curve is missing or curve.k==0 and curve.b==0
            = 1/(1+exp(-(curve.k*(s-0.5)+curve.b)))  otherwise
```
Live calibration blob read today: `{"version":"live-2026-09-05-n67","global":{"k":1.0,"b":0.1176,"n":67},"curves":{},"fitted":true}` — `curves` is **empty**, so every signal, of every symbol/regime/signal_type, resolves to the one `global` curve (or the prior, before one existed).

**Fitting** (`app/backtest/calibration.py:64` `fit()`, called from `app/autoscalp/runner.py:1015` `_maybe_recalibrate()`): OLS logistic fit of `logit(bucket_win_rate) ~ (score-0.5)` on score buckets rounded to 0.1, needs >=4 rows/bucket and >=40 total rows.

---

## 2. The ten specific questions

**1. Exact source file/function per stage** — given above (§1).

**2. Exact formula per stage** — given above (§1a).

**3. Inputs used** — candles (`bars_by_tf`, all 5 timeframes), the option chain (`chain`,
CE/PE rows with OI/OI-change/greeks/LTP), the regime/MTF read, and the persistent
`autoscalp_calibration` `app_settings` blob.

**4. Is any feature double-counted?** No feature is used verbatim twice in the additive
sum. But see §5 — several *different* features encode substantially the same underlying
fact.

**5. Do correlated features artificially inflate score? YES, structurally.** Three
separate places independently measure "is the higher-timeframe trend clean and
continuing":
  - `state_classifier._htf_trend()` — 20/50 EMA cross on 30m or 15m bars, feeds the `htf`
    component at **6%** weight inside `state_score` (which itself carries 62% of `blended`
    → effective weight ≈3.7%).
  - `regime_mtf.mtf_alignment()` — 9/21 EMA cross across 1m-30m, weighted 53% toward
    15m/30m alone — feeds `blended` **directly at 14%**, and separately GATES the trade
    outright when `mtf.htf_dominant` opposes the direction.
  - `option_engine._own_trend()` (inside `analyse_leg`, feeding `final_quality` at 24%
    weight in `blended`) — trend read on the **option's own** 5m/3m candles, which track
    the index closely for delta-weighted legs.
  None of these three reuse the same array, so this is not literal code duplication — but
  they are reading a highly overlapping signal (the same underlying trend, on overlapping
  timeframes, via different EMA pairs) and stacking it additively three times at three
  different weights, once as a soft bonus and once as a hard veto. A single strong, clean
  trend can inflate `blended` through all three channels simultaneously without adding
  three independent pieces of evidence.

**6. Are components additive when they should not be?** Mostly no — `false_risk` is
correctly **multiplicative** (`state_score = raw_score * (1 - penalty)`, not summed in).
One genuine inconsistency found: `regime_conf` (`reg["confidence"]`) is written into the
`component_scores` display dict (`scalp_strategy.py:378`) looking like a 13th weighted
input, but grep confirms it is used **nowhere else** in the file — it feeds neither
`state_score` nor `blended`. It is cosmetic, not a scoring input. This is a minor
transparency defect (a reader of `component_scores` would reasonably assume all 13 fields
are weighted), not a mis-weighting.

**7. Does missing/weak data get a positive default?**
  - `option_engine.analyse_leg`: `theta_drag` defaults to **0.3** when theta/ltp is
    missing, meaning `(1-theta_drag)=0.7` — an above-neutral score for a component with no
    real data behind it.
  - `state_classifier._htf_trend`/`_eval_break`: an `UNKNOWN` HTF read scores **0.5**
    (neutral-ish, defensible) rather than being excluded from the weighted sum.
  - `own_ok` in `option_engine.analyse_leg` gives **0.4** (not 0 or a true neutral 0.5)
    when the option's own trend direction is `NONE`.
  None of these is dramatically wrong in isolation, but all three lean toward "assume
  slightly-better-than-nothing" rather than either excluding the term or truly zeroing it
  — a small, consistent upward bias when data is thin, compounding with §5's stacking.

**8. Is regime/symbol-specific behaviour erased by a global mapping? YES — this is the
single largest, most concretely evidenced finding.** The live calibration blob's
`"curves": {}` is empty: **every** symbol, every regime, every signal_type is scored
through the exact same one global sigmoid. The system's own code supports per-key curves
(`curves.get(f"{regime}|{signal_type}")`) — they are simply not populated because no
individual (regime, signal_type) key has yet reached the same `_MIN_ROWS=40` floor the
global curve needed (each key only has a handful of the 74 total rows — see §4 below). A
symbol/regime combination that behaves very differently from the population average (see
§4: NATURALGAS TRENDING_UP) is being scored as if it were average.

**9. Is probability being read as calibrated when it's actually a confidence score?**
**Partially, and the code already tries to guard against exactly this.** `_calib_meta()`
and `effective_confidence()` exist specifically to expose `calibration_status: prior|fitted`
and cap the *label* confidence (HIGH/MEDIUM/LOW) when calibration is thin — this is a real,
working safeguard on the categorical confidence label. But the **numeric** `probability`
field itself carries no such caveat downstream: it is stored as a bare float in
`scalp_signals.probability` and read as-is by `ev_gate()` (which sizes the trade decision)
and by `calibration_report.py` (which grades the model). Nothing prevents a probability
produced from the un-fitted "prior" sigmoid (an assumed, hand-picked shape, never fit to
this system's own data) from being used identically to one produced from the fitted
global curve — both are plain floats in the same column with no source flag carried
alongside them into `ev_gate`.

**10. Is the same historical data used both to fit and to evaluate calibration?**
**Yes, by the runner's actual code path — contradicting that module's own docstring.**
`app/backtest/calibration.py`'s module docstring states *"Nothing here trains on rows it
will later be scored against — the P6 runner passes disjoint chronological slices."* That
claim is true of the offline backtest/replay harness. It is **not** true of the live path:
`AutoScalpRunner._maybe_recalibrate()` (`runner.py:1015-1028`) calls
`db.list_scalp_signals(source="LIVE", status="CLOSED", limit=2000)` — the **entire**
resolved history, no chronological split — every 900s once >=40 samples exist, and
`calibration_report.py._resolved_rows()` reads the identical unrestricted pool to grade
the result. For the current 74-trade sample this did not bias *this* report (evidence in
§3 below: calibration was still `null` as of 2026-09-02, so most/all of these 74 signals
predate any fit and used the un-fitted prior) — but it is a structural risk for every
future recalibration: each new global fit is validated by "is it well calibrated on the
same trades used to fit it," which will look better than true out-of-sample performance.

---

## 3. Root-cause finding: `oi` is dead across the entire sample

The `oi` component (12% weight in `state_score`, tied with `volume` for the third-highest
individual weight after `price_action` 20% and `level_strength` 16%) is **exactly 0.0 in
all 74 rows, for all 5 symbols** — not just NATURALGAS:

```
component  n_nonzero(/74)  stdev
oi                      0  0.000     <- every other component varies; oi never does
```

Traced as far as static reading allows: `comp["oi"] = _oi_at(chain, level, "ce_write"|
"pe_write")` (`state_classifier.py:189,262`), which computes
`clamp(oi_chg_at_level / avg_oi_per_strike / 1.5, 0, 1)`. The chain-construction code
(`app/runtime.py._autoscalp_chain`) does populate a `ce_oi_chg`/`pe_oi_chg`-shaped field
(`"oi_chg": r.get("ce_oi_change")`), so the field names line up on paper. **This report
does not have a definitive root cause for why the value is nevertheless always exactly
0** — that would need a live trace of the actual `chain` argument during market hours
(reading it after the fact from stored `scalp_signals` rows does not include the raw chain
payload). Flagged as open, not resolved here; candidates are (a) `oi_change` is
consistently None/0 in the live chain data reaching this function, or (b) a real wiring
defect between chain construction and this specific call. Either way, **12 percentage
points of `state_score`'s weight has contributed zero real information for the entire
observed history** — the effective weight distribution has actually been `price_action
23%, level_strength 18%, volume 14%, momentum 16%, vwap 9%, atr 9%, htf 7%, retest 5%,
oi 0%` (renormalized), not the intended 12%.

---

## 4. Feature-level attribution (n=74, correlation against predicted probability and against actual win)

| component | corr vs. **predicted probability** | corr vs. **actual win** |
|---|---|---|
| retest | **+0.356** | -0.106 |
| level_strength | **-0.309** | +0.079 |
| vwap | **+0.244** | -0.016 |
| momentum | +0.156 | +0.064 |
| htf | +0.131 | -0.004 |
| mtf | +0.126 | -0.042 |
| option_quality | +0.107 | -0.064 |
| false_risk | +0.056 | -0.016 |
| atr | +0.051 | -0.111 |
| volume | -0.012 | +0.011 |
| price_action | -0.001 | +0.004 |
| regime_conf | -0.002 | -0.146 |
| oi | 0.000 (constant) | 0.000 (constant) |

**(A) What drives high predicted probability:** `retest`, `vwap`, and (inversely)
`level_strength` are the strongest linear drivers of the score the model outputs.

**(B) What drives losing trades:** **nothing does, meaningfully.** The largest-magnitude
correlation with actual outcome across all 13 components is `regime_conf` at -0.146 — and
`regime_conf` is not even a scoring input (§2, Q6), so this is very likely sampling noise
in a 74-row set, not a real driver. **This is the cleanest statement of the core problem:**
the components that move the *predicted* probability up (retest, vwap, momentum, htf, mtf)
show essentially zero relationship with what actually happens next.

**(C) The NATURALGAS 60-70 score bucket specifically** (n=20 across the two worst-hit
(regime, signal_type) pairs — see §5): `vwap`, `regime_conf`, `false_risk`, and to a lesser
extent `atr` are pinned near their maximum (≈0.9-1.0) on almost every row **regardless of
outcome** — 15 of 20 rows show `vwap=1.00`. These near-saturated components add a
persistent, high, nearly-constant bonus to every signal of this shape, while `momentum`
and `htf` are also frequently 1.00 on **both** winning and losing rows within this same
bucket (e.g. a LOSS row and a WIN row can both show `momentum=1.00, htf=1.00`) — so even
within this specific bucket, the components don't separate its own winners from its own
losers. The bucket's score is high because several inputs are structurally saturated for
this state-transition shape, not because the model found a discriminating pattern.

---

## 5. Symbol / regime / signal_type comparison (n<20 excluded from any reliability claim, per instruction)

### By symbol
| symbol | n | avg predicted | actual win% | gap | reliable (n>=20)? |
|---|---|---|---|---|---|
| NATURALGAS | 43 | 58.1% | 41.9% | **+16.2pp** | ✅ yes |
| NIFTY | 12 | 55.8% | 50.0% | +5.8pp | ❌ no (n<20) |
| CRUDEOIL | 11 | 56.6% | 63.6% | -7.1pp | ❌ no (n<20) |
| BANKNIFTY | 6 | 57.5% | 66.7% | -9.2pp | ❌ no (n<20) |
| SENSEX | 2 | 66.5% | 0.0% | +66.5pp | ❌ no (n<20, extreme) |

Only NATURALGAS has enough samples to say anything with confidence: a real, moderate
overconfidence gap (+16.2pp). Every other symbol's number is directional colour only.

### By regime
| regime | n | avg predicted | actual win% | gap | reliable (n>=20)? |
|---|---|---|---|---|---|
| TRENDING_DOWN | 31 | 55.9% | 51.6% | +4.3pp | ✅ yes — essentially well-calibrated |
| RANGE | 21 | 59.0% | 52.4% | +6.6pp | ✅ yes — mild overconfidence |
| TRENDING_UP | 19 | 59.8% | 26.3% | **+33.4pp** | ⚠️ **n=19, one shy of the floor** — by far the largest gap of any group, but per instruction this is **not** declared reliable |
| REVERSAL_REGIME | 2 | 54.0% | 100.0% | -46.0pp | ❌ no (n<20) |
| BREAKOUT_REGIME | 1 | 51.2% | 100.0% | -48.8pp | ❌ no (n<20) |

**This is the sharpest split in the whole dataset.** The two regimes with adequate
samples (TRENDING_DOWN, RANGE) are reasonably calibrated. TRENDING_UP is dramatically
overconfident (+33.4pp) — but at n=19 it is explicitly **one trade short** of this
report's own 20-sample floor, so it is reported as a strong lead, not a finding. It also
explains §4(C): NATURALGAS's TRENDING_UP×SUPPORT_REVERSAL pair (n=10) is more than half
of all TRENDING_UP signals in the dataset, so "NATURALGAS is overconfident" and
"TRENDING_UP is overconfident" are substantially the same underlying signal viewed from
two different group-by columns, not two independent findings.

### By signal_type
| signal_type | n | avg predicted | actual win% | gap | reliable (n>=20)? |
|---|---|---|---|---|---|
| SUPPORT_BREAKDOWN | 40 | 56.4% | 47.5% | +8.9pp | ✅ yes — mild overconfidence |
| SUPPORT_REVERSAL | 22 | 60.2% | 50.0% | +10.2pp | ✅ yes — mild overconfidence |
| RESISTANCE_REVERSAL | 12 | 57.3% | 41.7% | +15.6pp | ❌ no (n<20) |

Both adequately-sampled signal types show only mild overconfidence on their own — the
severe gap is concentrated in the **intersection** of symbol × regime × signal_type
(§4C), not in any single dimension alone.

### The two combined (symbol, regime, signal_type) triples driving most of the gap (n>=4, full history)
| triple | n | avg score | avg predicted | actual win% |
|---|---|---|---|---|
| NATURALGAS · TRENDING_DOWN · SUPPORT_BREAKDOWN | 10 | 68.7 | 58.2% | 30% |
| NATURALGAS · TRENDING_UP · SUPPORT_REVERSAL | 10 | 69.3 | 63.7% | 30% |
| NIFTY · TRENDING_DOWN · SUPPORT_BREAKDOWN (same signal_type, different symbol) | 6 | 68.0 | 56.5% | **66.7%** |

The same `signal_type`, similar average score, opposite outcome depending on symbol —
consistent with §2 Q8 (global calibration erasing a real per-symbol difference), not with
a flaw in `SUPPORT_BREAKDOWN`'s definition itself. Both NATURALGAS triples are n=10 —
**below this report's 20-sample floor**, reported as a concrete, traceable lead, not a
proven pattern.

---

## 6. Root cause classification

Based on the evidence above, in order of how strongly each is supported:

1. **Regime/symbol blindness** — ✅ strongly supported (§2 Q8, §5). The calibration curve
   is global; the two most concrete bad patterns found (NATURALGAS × TRENDING_UP, and the
   TRENDING_UP regime generally) are exactly the kind of subgroup a global-only mapping
   cannot see.
2. **Feature double-counting / correlated-feature inflation** — ✅ supported (§2 Q5). Three
   separately-computed "HTF trend is clean" signals (state_classifier `htf`,
   `regime_mtf.mtf_alignment`, option `own_trend`) stack additively across three different
   weights rather than being treated as one piece of evidence.
3. **Scoring formula problem (component weighting / saturation)** — ✅ supported (§3, §4C).
   `oi` (12% intended weight) contributes zero information across 100% of the sample.
   Several components (`vwap`, `regime_conf` even though it isn't wired in, `false_risk`)
   sit near-saturated for whole classes of signals, adding a near-constant bonus that
   doesn't discriminate outcome.
4. **Calibration problem (methodology)** — ✅ supported (§2 Q10). The live recalibration
   path fits and evaluates on the same undifferentiated historical pool, contradicting the
   `backtest/calibration.py` docstring's disjoint-slice claim, which is only honoured by
   the separate offline replay harness.
5. **Sample-size limitation** — ✅ real and load-bearing, but secondary. n=74 total; every
   informative subgroup found here (NATURALGAS-TRENDING_UP triples, TRENDING_UP regime
   overall) is at or below the 20-sample floor this very report is enforcing. The
   *direction* of every finding above is consistent and traceable to a mechanism, which is
   why this report treats them as leads worth watching rather than dismissing them as pure
   noise — but formally, none of the sharpest numbers (TRENDING_UP's +33.4pp gap; either
   NATURALGAS triple at n=10) clears the bar to be called proven.
6. **Probability interpreted as calibrated when it's a confidence score** — ⚠️ partially
   supported (§2 Q9). The categorical confidence label is correctly downgraded when
   calibration is thin; the numeric `probability` float carries no equivalent flag into
   `ev_gate()` or the calibration report.
7. **Data-quality problem** — ⚠️ narrowly supported. `oi` is dead (§3), but this report
   could not distinguish "chain data genuinely lacks OI-change info" from "a wiring
   defect" without a live trace — left open, not claimed as data-quality with confidence.
8. **Outcome-label problem** — ❌ not investigated / no evidence found. `outcome` values
   (WIN/LOSS/FLAT) were taken as given; this report did not audit how FLAT is decided or
   whether any resolution logic mislabels outcomes.
9. **Execution/hold-time problem** — ❌ not implicated here. This is the subject of the
   separate, already-closed §D (`PRODUCTION_READINESS.md`) — insufficient evidence either
   way at n=12, and out of scope for a probability-calibration audit.

---

## 7. What this report is and is not

**Is:** a read-only trace of the exact code path from raw features to a stored
probability, and an evidence-based attribution of where the 74-trade sample's measured
overconfidence concentrates.

**Is not:** a fix, a recommendation to change any weight/threshold/curve, or a claim that
any single pattern above is statistically proven — every subgroup sharp enough to look
like a smoking gun (TRENDING_UP regime, either NATURALGAS triple) is explicitly at or
under the 20-sample floor this report itself enforces. No production config, trading
logic, threshold, calibration parameter, frozen NIFTY setting, or `live_trading` setting
was touched.

**Tracked as:** K8 in `PRODUCTION_READINESS.md`.
