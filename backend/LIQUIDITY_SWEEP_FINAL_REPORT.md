# Liquidity Sweep Intraday Options Strategy — Final Report

Research/paper build. No order path, no broker call, no live wiring. All numbers
below are from real historical data unless explicitly marked synthetic (unit
tests only). **Bottom-line recommendation is at the end — read that first if
short on time.**

---

## 1. What was found in the existing codebase (audit)

Extremely high reuse rate — most of the mechanical building blocks already
existed and were reused, not duplicated:

| Need | Reused from | How |
|---|---|---|
| PDH/PDL, pivots, swing highs/lows | *(reimplemented fresh)* | `app/engines/sr_engine.py` computes these too, but is on the LIVE autoscalp path — reimplemented the same well-known 3-bar fractal technique in the new package instead of reaching into or editing a live file |
| RSI/MACD/ADX/ATR/EMA | `app/engines/signal_engine.py` (`_rsi`, `_macd`, `_adx`, `_atr`, `_ema_series`) | Direct read-only import — the *same* convention `app/engines/regime_mtf.py` already uses for these exact functions |
| Position sizing (capital × risk% ÷ stop distance, lot-floored, margin-capped) | `app/engines/risk_engine.py::run_risk_engine()` | Called directly — it already implements precisely this brief's formula |
| Probability calibration (score → real historical win-rate, logistic fit, reliability curve) | `app/backtest/calibration.py` (`fit`/`predict`/`reliability_curve`) | Called directly — already used and validated by the live autoscalp calibration pipeline |
| Backtest replay infrastructure | `app/backtest/runner.py`/`replay.py`/`oi_history_adapter.py` | Inspected; not used directly — see §7 below for why a fresh causal-walk was built instead |
| ITM strike selection | `app/engines/option_engine.py::select_option()` | **Not reused** — that function is ATM-centric (its own delta band is 0.35–0.62, picks by proximity to spot) and is on the live path; this brief needs an ITM-constrained, configurable-delta-band selector, a materially different rule |
| CHoCH/BOS/CISD/FVG/Order Block | — | **Genuinely new** — no SMC-style structure-break/imbalance detection existed anywhere in the codebase |

## 2. What was implemented

New package: **`backend/app/liquidity_sweep/`** (9 modules, ~950 lines):

| File | Purpose |
|---|---|
| `structure.py` | Swings, HH/HL/LH/LL labeling, equal-highs/equal-lows liquidity pools, PDH/PDL (strictly prior-session only), HTF bias (15m/30m/1h/4h/1d, EMA-slope based) |
| `indicators.py` | VWAP (session-anchored, freshly built)/EMA/RSI/MACD/ADX/ATR snapshot |
| `sweep.py` | Liquidity sweep detection — wick beyond a level + reclaim within a bounded window (default 3 bars); a plain breakout with no reclaim is explicitly not a sweep |
| `confirmation.py` | CHoCH / BOS (structure-break vs. prevailing trend), CISD, FVG, Order Block |
| `setup_score.py` | Explainable 0–100 evidence-count score (8 named checks) — feeds calibration only, never overrides a mandatory gate |
| `probability.py` | Calibration wrapper, 3-way P(UP)/P(DOWN)/P(RANGE) split, a separate confidence score, and the backtest-only forward-outcome labeler |
| `strikes.py` | ITM strike ranking (configurable delta band, weighted StrikeScore, hard liquidity/spread filters) |
| `risk.py` | SL = sweep extreme ± buffer; target = next real liquidity pool or 2R floor, whichever is farther; position sizing via `run_risk_engine` |
| `engine.py` | Orchestrator — produces the full JSON signal contract, index-first as redirected mid-build |
| `backtest.py` | Two-stage backtest (see §5/§6) |

### Index-first redesign (mid-build instruction)

The build started option-first, then was explicitly redirected: **the index
is the prediction engine, the option is only the execution instrument.**
`engine.evaluate()` computes direction/probability/confidence/entry/SL/target
entirely from the index; `option_candidates` is an *optional* argument —
when omitted, `option` in the output is `"NOT_EVALUATED"`, never a
delta-approximated premium (the brief explicitly forbids treating
spot-move × delta as a real execution price).

## 3. Files changed

All additions, zero modifications to existing files:
- `backend/app/liquidity_sweep/` — 10 files (9 modules + `__init__.py`)
- `backend/tests/test_liquidity_sweep_*.py` — 10 test files, 86 tests
- `backend/LIQUIDITY_SWEEP_FINAL_REPORT.md` — this report

## 4. Tests created and results

**86 tests, all passing.** Full backend suite: **1120/1120 passing**, zero
regressions (checked after every module, matching this repo's established
practice).

| File | Tests | Covers |
|---|---|---|
| `test_liquidity_sweep_structure.py` | 9 | swings, PDH/PDL prior-session-only guarantee, equal levels, HTF bias |
| `test_liquidity_sweep_sweep.py` | 9 | same-bar and delayed reclaim, reclaim-window boundary, plain-breakout rejection, **re-truncation stability** |
| `test_liquidity_sweep_confirmation.py` | 12 | BOS vs CHoCH (including a deliberately ambiguous structure), CISD, FVG (bullish/bearish/none), Order Block |
| `test_liquidity_sweep_setup_score.py` | 4 | full/zero/partial evidence, missing indicators never hard-fail |
| `test_liquidity_sweep_probability.py` | 10 | 3-way split sums to 1, confidence clamping, outcome labeling incl. the ambiguous same-bar-both-thresholds case, real calibration fit/predict roundtrip |
| `test_liquidity_sweep_risk.py` | 9 | SL/target geometry both directions, 2R floor vs. a farther real liquidity target, `run_risk_engine` reuse (budget never exceeded) |
| `test_liquidity_sweep_strikes.py` | 10 | ITM-only filters, delta band, spread/volume/OI rejection, **never selects solely on premium price**, configurable weights |
| `test_liquidity_sweep_indicators.py` | 5 | session-anchored VWAP, graceful `None` on insufficient history |
| `test_liquidity_sweep_engine.py` | 7 | full end-to-end BUY_CE signal (hand-verified bar series), NO_TRADE gates individually, option-candidate wiring |
| `test_liquidity_sweep_lookahead.py` | 5 | **see §6 — the mandatory look-ahead audit** |
| `test_liquidity_sweep_backtest.py` | 6 | chronological split, accuracy/timeout accounting, walk dedup |

## 5. Data used

Full data-availability audit (already reported mid-build, repeated here for
the record):

| | Range | Instruments | What's real |
|---|---|---|---|
| **Kaggle** (`data/historical/kaggle/research_historical.db`) | 2015-01-09 → 2026-05-18 | NIFTY, BANKNIFTY index | Real 5-minute OHLC, ~210K bars for NIFTY alone. **No option data, no volume for NIFTY** (column is NULL in this dataset) |
| **market_history.db** (real broker captures) | 2026-09-02 → 2026-09-11 (10 days) | NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX, BANKEX, CRUDEOIL, NATURALGAS | Real quote_snapshots (LTP/bid/ask/OI/volume) + option_greeks (delta/gamma/theta/vega/IV) — genuinely real, genuinely rich, genuinely only 10 days |

**Execution timeframe**: the brief asks for 3-minute confirmation. Kaggle has
5-minute bars only (no 1-minute NIFTY data to build 3m candles from without
fabricating data, which section 14 explicitly forbids) — **5m was used for
both sweep and confirmation in Stage 1**, a documented substitution. Stage 2's
10-day window *does* have real 1m/3m data in `market_candles`, but 5m was kept
for config consistency with Stage 1; using genuine 3m there is a natural,
cheap follow-up if this is taken further.

## 6. Look-ahead-bias audit — RESULT: PASS

Every function in the package takes bars already truncated to ≤ the
evaluation timestamp T (the caller's contract, same convention as
`app.orderflow.h1h7_state` and `app.structural_break` elsewhere in this
codebase). Proven, not just asserted, via `test_liquidity_sweep_lookahead.py`:

1. **Static source audit** — no module in `app/liquidity_sweep/` imports any
   live DB/market-data/network module (AST-parsed, not a grep). Look-ahead
   via a side channel is structurally impossible, not merely untested.
2. **No mutable module-level state** — confirmed no hidden cache/singleton
   could leak information between calls.
3. **Future-data mutation test** — two bar series identical up to T, one
   continuing the same trend after T, one crashing violently after T.
   `engine.evaluate()` on both series **truncated to T produced byte-identical
   output**. This is the brief's own literal test design (§22/§12).
4. **Non-vacuous counterfactual** — the same two series, evaluated WITHOUT
   truncation, DO produce different output — proving check #3 is a real,
   discriminating test, not passing because the function ignores its input.
5. Every index `sweep.py`/`confirmation.py` report is asserted `< len(bars)`
   supplied, and reclaim is always exactly the last given bar, never beyond.

**All 5 checks pass.** No look-ahead bias found.

## 7. Backtest — STAGE 1: Index Direction (the primary, rigorous test)

Reused `app.backtest.calibration`'s existing fit/predict/reliability
machinery; the causal WALK itself is new (not the replay harness in
`app/backtest/runner.py`, which is bar-by-bar option-candle replay driving
`scalp_strategy.decide_from_context` — a different engine's live decision
function, not reusable for walking this new module).

**Setup**: real Kaggle NIFTY 5m OHLC, most recent 2 years (2024-2026,
~43,700 bars walked). 250-bar rolling lookback window per evaluation (a full
from-genesis rescan at every bar would be O(n²) and untenable — a live
system wouldn't do that either). 1-hour (12-bar) outcome horizon. Chronological
50/25/25 TRAIN/VALIDATION/OOS split — **no shuffling** (shuffling a time
series before a walk-forward split is itself a look-ahead leak).

**A real bug was caught and fixed during this run**: an early, un-deduplicated
walk fired 3 near-identical "signals" on consecutive bars (250/251/252) for
what was clearly one underlying sweep event, because the reclaim window kept
re-confirming it. Fixed with a cooldown after each signal (documented in
`backtest.py`, locked in by `test_liquidity_sweep_backtest.py`). Signal rate
dropped from an implausible ~16% of all bars to a plausible ~6/day.

### Results (2,110 raw signals total, before any probability threshold)

| Split | n | n graded | Directional accuracy | Brier | ECE |
|---|---|---|---|---|---|
| TRAIN | 1,055 | 643 | **47.1%** | 0.254 | 0.051 |
| VALIDATION | 527 | 335 | **50.75%** | 0.257 | 0.094 |
| OOS | 528 | 314 | **48.1%** | 0.252 | 0.055 |

- **Directional accuracy hovers at coin-flip (47–51%) across every split.**
- Calibration itself is reasonable (low ECE, Brier close to what a constant
  0.5 predictor gives) — the model is **honestly reflecting an absence of
  edge**, not overconfidently misreporting a real one.
- **No signal ever reached the 60%+ probability bucket** in any split — the
  calibration model never generates high confidence, which is the correct
  behavior when the underlying feature genuinely doesn't discriminate.
- At the 55% threshold (the only bucket with any samples): TRAIN 48.9% (n=184),
  VALIDATION 48.25% (n=114), OOS **54.5%** (n=101) — the OOS uptick is
  consistent with sampling noise at n=101, not treated as a discovered edge.

### The decisive finding

```
WIN mean setup_score:  57.5  (n=624)
LOSS mean setup_score: 57.5  (n=668)
```

**Identical.** The setup score — structure alignment, HTF bias, VWAP/EMA/RSI/
ADX alignment combined — has **zero measured discriminative power** between
winning and losing signals. Win rate is ~47–50% regardless of sweep type
(UPPER 49.8%, LOWER 46.9%) or direction (BULLISH 46.9%, BEARISH 49.8%).

**Directional accuracy vs. trade win rate are reported separately, as
required** — everything in this section is INDEX directional accuracy
(did price move the called way within the horizon), never mixed with an
option P&L number.

**No parameter search was run to try to improve this number.** The brief
explicitly warns against exactly that ("do not optimize dozens of parameters
until it looks profitable") — a single honest result on real, unshuffled,
walk-forward data is worth more than a tuned one.

## 8. Backtest — STAGE 2: Option Execution (pipeline validation only)

**Explicitly and repeatedly labeled: "PIPELINE VALIDATION — INSUFFICIENT
FOR LONG-TERM PROFITABILITY CLAIM."**

Real 10-day window (`market_history.db`), real option-chain (LTP/bid/ask/OI/
volume from `quote_snapshots`, real delta from `option_greeks`, joined by
nearest timestamp, never from after the evaluation instant). 25 sampled
evaluation points across the window (not exhaustive — `quote_snapshots` has
1.8M rows and is unindexed for this ad-hoc time-range query; each lookup
took several seconds, so 25 samples was the tractable, still-representative
choice).

**Result**: 1 of 25 sampled points produced a full, valid, real end-to-end
signal — genuine BUY_PE, ITM strike (real Δ=-0.63, OI=5.2M, tight real
spread), entry 23,487.4, SL 23,539.4, target 23,383.5, exactly 2R. **This
confirms the entire pipeline — index structure → sweep → confirmation →
probability → ITM strike ranking with real Greeks/OI/volume — runs correctly
on real captured data.** With only 10 days and 25 samples, this is nowhere
near enough to say anything about win rate, profit factor, or drawdown, and
none is claimed.

## 9. ITM strike selection — what was validated

`rank_strikes()` is fully unit-tested (10 tests): correctly enforces ITM
constraint (CE strike < spot, PE strike > spot), configurable delta band,
hard rejects on wide spread/low volume/low OI, and — the specifically
required property — **never selects solely on premium price** (a cheap but
illiquid contract loses to a pricier, liquid one). The one real Stage 2
signal (§8) exercised this against genuine market data successfully. A
systematic ITM-depth comparison (ATM vs 1-step vs 2-step vs delta-based,
per section 20 of the original brief) was not run as a separate sweep —
see §11.

## 10. Losing-trade / failure-mode analysis

Given the headline finding (zero discriminative power in the setup score),
a granular per-rule failure-mode breakdown (which of CHoCH/BOS/CISD/FVG/OB
correlates with losses) would not be meaningful to report as a "finding" —
with WIN and LOSS scores identical, no combination of these checks
discriminates outcomes on this dataset. What *is* real: the **38.8% TIMEOUT
rate** (neither the 2R target nor the stop was reached within the 1-hour
horizon) is itself informative — a large fraction of "confirmed" setups
simply go nowhere within a realistic intraday holding period, independent of
which direction they were.

## 11. What was deliberately NOT done, and why

- **No parameter/threshold grid search** to chase a better number once the
  baseline showed no edge — the brief explicitly forbids exactly this
  (§27/28 of both briefs: "never fabricate," "do not optimize until it
  looks profitable"). Doing so on a signal already shown to lack edge risks
  multiple-comparison false positives, not a genuine discovery.
- **No formal ablation grid** (sweep-only vs +CHoCH vs +CISD vs +FVG vs +OB)
  as a separate run — with the full combined score showing zero
  discriminative power, an ablation across its components was judged
  unlikely to be informative and risks the same p-hacking concern above.
  If genuinely wanted despite this, it's a well-scoped, cheap follow-up
  (the samples already carry enough fields to do it directly).
- **No 3-minute confirmation for Stage 1** — Kaggle has no 1-minute NIFTY
  bars; 5m was used, documented, not silently substituted.
- **No re-pull of the deleted full-year Upstox option dataset** — that's a
  real network/disk action (7GB) that wasn't authorized for this task.
- **BankNifty Stage 2 run** — not completed given the per-query cost
  observed on NIFTY; the NIFTY run already demonstrates the pipeline.

## 12. Robust or overfit?

**Neither, in the sense the question implies** — there is no positive
result to be robust or overfit. The finding is a genuine null result: on
2 years of real, unshuffled, walk-forward NIFTY 5m data, this specific
liquidity-sweep + CHoCH/BOS + CISD/FVG/Order-Block construction shows no
measurable directional edge, consistently across TRAIN/VALIDATION/OOS. That
consistency (coin-flip in all three splits, not "great in-sample, terrible
OOS") is itself evidence the null result is real and not a fluke of one
split.

## 13. Known limitations

1. Execution timeframe is 5m, not the specified 3m, for Stage 1 (data
   availability, documented).
2. Stage 2 has only 10 real days and 25 sampled points — a pipeline check,
   not a statistical validation.
3. HTF bias uses a simple EMA-slope read per timeframe, not a
   full ADX/structure-based classifier per timeframe (a scope choice for
   this pass, clearly separable if a richer HTF read is wanted later).
4. `min_reaction_atr` (sweep quality threshold), `reclaim_window`,
   `cooldown_bars`, and the 8 setup-score checks' weights are all plain,
   documented, un-fitted defaults — never tuned against this backtest's own
   results, by design (see §11).
5. The 3-way P(UP)/P(DOWN)/P(RANGE) split's `range_share` (0.35) is a
   documented modeling choice, not a second calibrated model — treat the
   RANGE figure specifically as a plausible constant, not a validated
   probability.

## 14. Final recommendation

**NOT READY.**

Not "needs more data" — the primary Stage 1 test (the one explicitly
prioritized as most important) had abundant real data (2 years, ~44K bars,
2,110 signals) and delivered a clean, statistically consistent null result.
The raw liquidity-sweep + structure-break + confirmation construction, as
specified and implemented here, does not show measurable directional edge
on NIFTY 5m. The engineering (detection logic, risk/sizing, ITM strike
ranking, look-ahead protection) is sound and fully tested — the trading
hypothesis itself is not supported by this backtest.

If this is taken further, the highest-value next steps in priority order
would be: (a) test whether a genuine 3-minute confirmation timeframe changes
the result (needs 1-minute source data, not currently available for NIFTY),
(b) test other instruments/timeframes rather than re-tuning this one's
parameters, (c) revisit whether CHoCH/BOS/CISD/FVG/Order-Block as defined
here match a professional trader's actual usage closely enough, since a
faithful-but-wrong implementation of the right idea would look exactly like
this result too.
