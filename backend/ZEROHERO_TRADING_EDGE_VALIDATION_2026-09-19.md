ZEROHERO — INDEPENDENT TRADING EDGE VALIDATION
================================================

Repository: /root/zerohero (main, commit 64a217c)
Date: 2026-09-19
Phase: research/validation only. No strategy code modified. No live orders.
No parameters changed. No commits made this phase.

Method: 6 independent read-only investigations (4 as parallel forks —
data/leakage, strategy logic, options microstructure, adversarial review —
plus direct quant/regime/calibration computation on the real live
database, done by the coordinator to keep the numbers authoritative and
avoid transcription drift across forks). All findings below are cited to
file:line or to real database queries against `chanakya.db`, not inferred.

Labels used throughout: VERIFIED / SUPPORTED / WEAK EVIDENCE /
INSUFFICIENT EVIDENCE / DISPROVED / NOT TESTED. No claim of "guaranteed,"
"90%+ accurate," "profitable," or "production ready" appears anywhere in
this report.

---

## 1. Executive Summary

**The core, load-bearing finding of this phase is methodological, not
statistical**: the only real trade history available (179 resolved
AUTOSCALP paper trades, 2026-08-31 to 2026-09-18) is **not a clean
out-of-sample test of one fixed strategy**. Independently confirmed by two
separate audits (Agent 1 and Agent 6): at least two live logic-inversion
bugs were fixed mid-window (`8f4d53b`, `f9dfbc8`), and a risk parameter
(`trail_atr` for NATURALGAS/CRUDEOIL) was explicitly tuned on part of this
same dataset (`69c49c0`, 2026-09-04) partway through collection. The
dataset is a record of an evolving, self-correcting system, not a frozen
strategy under test.

Within that important caveat, every subset examined — the full pool
(n=179), the pre-fix subset (n=132), and the post-fix "current logic"
subset (n=47) — shows **negative expectancy, gross, before any trading
costs**. Applying the one real, validated per-instrument cost model that
exists (NATGAS/CRUDEOIL) turns an already-negative-to-barely-breakeven
gross result clearly and substantially more negative net. No ML model
exists in the live decision path to evaluate separately. Sample sizes for
every disaggregated cut (by symbol, by regime, by confidence bucket) are
small enough that most individual cells cannot support a standalone claim
in either direction.

**Verdict on the central research question ("does the current strategy
have a measurable out-of-sample edge"): DISPROVED for the gross,
uncorrected pooled data as evidence of a positive edge, and INSUFFICIENT
EVIDENCE to make any confident claim about "the current" (post-fix, clean)
configuration specifically, because no sufficiently large, uncontaminated,
single-configuration sample exists yet.** Two concrete, code-verified,
actionable root causes were found for the negative results that do NOT
depend on the contamination issue and would need fixing regardless:
an EV gate that always assumes an idealized ~1.55R payoff it has never
delivered, and an option-selection process that structurally favors the
fastest-theta-decay delta band while weighting theta at only 4% of its
score.

## 2. Current Strategy Definition

Traced end-to-end (file:line) by Agent 2 and cross-checked by Agent 3:

`app/autoscalp/runner.py` (2s poll loop, DB-leader-gated) →
`app/engines/scalp_strategy.py::decide_from_context()` →
`sr_engine.compute_sr()` (S/R on the option's own premium bars, not index
points) → `regime_mtf.detect_regime()`+`mtf_alignment()` (ADX≥25 for
TRENDING_UP/DOWN) → `state_classifier.classify()` (BULLISH/BEARISH/etc,
HTF-alignment tie-break) → `option_engine.py::analyse_leg/select_option`
(composite quality score: liquidity 0.24, translation 0.22, delta_fit
0.16, premium_fit 0.12, atm_proximity 0.12, own_trend 0.10, theta 0.04) →
`ev_gate()` (EV = p·reward − (1−p)·risk − cost, with **avg_win/avg_loss
hardcoded to None** at the only live call site, `runner.py:663`, so it
always substitutes the idealized T1/SL geometry, never real historical
payoff) → `_plan_from_leg()` (SL = entry − 1.1×ATR, T1 = entry + 1.7×ATR,
trailing stop applied live) → `app/engines/paper_trading.py` (paper-only
execution, no broker order reachable, no slippage modeled in realized
P&L).

`RESISTANCE_BREAKOUT` (trend-continuation) is blocked by default
(`_DEFAULT_FILTERS`), leaving lagging/confirmation-based reversal setups
as the dominant eligible entries during a confirmed trend.

## 3. Data Validity

VERIFIED (Agent 1): candle construction excludes the still-forming bar
before any decision (`CandleAggregator.bars(closed_only=True)`); expiry/
strike resolution uses only the current tick's price; no look-ahead
sourcing found in the chain-snapshot path.

NOT FULLY VERIFIED (Agent 1, explicitly flagged, not ruled unsafe): WS
tick-ordering guarantees, and the internals of `app/backtest/runner.py`'s
train/test bar-slicing (a real train/test-aware backtest harness for the
live `decide_from_context()` DOES exist — this was not previously known
and is the single highest-value follow-up: audit its bar-slicing for
leakage before trusting any number it produces).

## 4. Leakage Audit

**No forward-looking (look-ahead) data leakage was found** in the live
decision path itself — bars, chain snapshots, and expiry/strike resolution
all use only information available at decision time (VERIFIED, Agent 1).

**A different, more consequential form of contamination was found: data
snooping / non-frozen-strategy contamination**, not look-ahead leakage.
Two independently-confirmed logic-inversion bugs were live and being
traded on for part of the sample window, and a risk parameter was
explicitly re-fit on part of the same trade history mid-window. This is
DISPROVED as "clean" for treating the 179-trade pool as one out-of-sample
test — see §16.

## 5. Strategy Logic

Agent 2's full trace found the state machine's conflict-resolution logic
sound (fast-TF vs HTF disagreement and state-vs-HTF disagreement are both
explicitly, deliberately adjudicated, not silently contradictory). No
unreachable conditions, no duplicate-signal generation within a single
decision call.

**Concrete, quantified defect found**: the EV gate (`option_engine.py`)
is called with `avg_win=None, avg_loss=None` unconditionally
(`runner.py:663` — not a fallback for missing data, a permanent
hardcoding), so every trade is accepted or rejected against an idealized
~1.55R payoff (from the 1.7/1.1 ATR multiples) that the system has never
actually delivered. Real closed trades show `avg_win=6.14pts <
|avg_loss|=7.29pts` — the realized payoff ratio is *worse than 1:1*,
the opposite of what the gate assumes. **This is SUPPORTED, code-verified,
and directly explains why marginal setups are being accepted that a
realistic EV calculation would reject.**

**Regime-specific defect**: TRENDING_UP's poor performance (see §10) has a
coherent code-level explanation — the continuation setup
(`RESISTANCE_BREAKOUT`) is blocked by default, and the surviving
reversal/breakdown entries require a confirmed close-beyond or a crossing
within the last 4 bars (confirmation-based, not anticipatory) — entries
during confirmed trends are structurally late and/or counter-trend.

A previously-fixed inverted-scoring bug is documented in-code
(`state_classifier.py:265-274` comment) as a permanent record — evidence
this exact class of directional-scoring bug has recurred in this codebase
(it's the same bug class as the two mid-sample fixes in §4/§16, just an
earlier instance).

## 6. Options Microstructure

SPOT PREDICTION ≠ OPTION PROFITABILITY was tested directly (Agent 3):

- The naive "map index points 1:1 onto premium" bug does **NOT** exist —
  SR/ATR/entry/SL/target are computed on the option's own premium bar
  history. **VERIFIED sound.**
- Option selection structurally favors the delta band (0.35–0.62) with
  the **fastest** theta decay, while theta itself is weighted at only 4%
  of the composite selection score. **SUPPORTED** as a plausible
  contributor to the high TIME-exit rate (see §7).
- IV richness is measured (RICH/CHEAP/FAIR) but **never used to block or
  size an entry** — a structural gap for a strategy buying premium.
  **DISPROVED** that IV awareness translates into IV-aware decisions.
- No real bid-ask spread check exists anywhere — only a soft OI/volume
  liquidity proxy with no hard reject threshold. **MISSING**, not a soft
  gap.
- No slippage is ever subtracted from realized P&L in the live path — the
  only cost-awareness is a rough pre-trade EV-gate heuristic (0.10–0.12R
  for MCX, 0 for NIFTY) that was never reconciled against the real,
  separately-computed per-instrument cost model. **MISSING** in the P&L
  that matters (the one recorded), present only as a pre-trade filter
  input.

## 7. Baseline Performance (real live paper trades, gross, before costs)

Computed directly from `chanakya.db`, `strategy='AUTOSCALP'`,
`status='CLOSED'`, n=179, 2026-08-31 → 2026-09-18.

| Metric | Value |
|---|---|
| Total trades | 179 |
| Win rate | 0.419 |
| Avg win | 6.14 pts |
| Avg loss | −7.29 pts |
| Expectancy | −0.723 pts/trade |
| Profit factor | 0.781 |
| Net P&L (gross) | −129.50 pts |
| Max drawdown | −201.5 pts |
| Max consecutive losses | 5 |
| Avg holding time | ~1158s (median 1094s, ~19 min) |
| Per-trade Sharpe (not annualized) | −0.063 |

**As established in §4/§16, this pooled number is not a valid single-
configuration measurement** — it is reported here as the raw baseline the
task asked for, with that caveat carried forward into every later section.

## 8. Out-of-Sample Performance

Splitting at the later of the two confirmed logic-inversion fixes
(2026-09-16T00:45 UTC):

| Subset | n | Win rate | Expectancy | PF | Net |
|---|---|---|---|---|---|
| PRE-FIX (had inverted logic) | 132 | 0.417 | −0.467 | 0.827 | −61.60 |
| POST-FIX (current logic) | 47 | 0.426 | **−1.445** | 0.711 | −67.90 |

**The post-fix, smaller sample is worse, not better.** This is **WEAK
EVIDENCE at best against a current edge** — n=47 is thin enough (see §16
adversarial CI analysis) that this difference is not proof the fixes made
things worse; it is proof that fixing two confirmed logic-inversion bugs
did not produce a visible positive edge in the trades observed since.
**No claim of a positive out-of-sample edge is supported by this subset.**

**Additional contamination specific to NATGAS/CRUDEOIL**: `trail_atr` for
these two instruments was explicitly recalibrated from the first 74
closed trades (commit `69c49c0`, 2026-09-04) — meaning even the "post-fix"
subset for these two symbols was generated using a parameter fit on part
of this same trade history. **Neither symbol's post-fix numbers can be
honestly called out-of-sample.**

## 9. Walk-Forward Results

**NOT TESTED.** No reusable walk-forward harness exists in this repo (a
prior fork's inventory found 4 independent one-off `walk_forward`
implementations, none shared). A genuine train→validation→out-of-sample
walk-forward run of the *live* `decide_from_context()` logic was not
performed this phase — `app/backtest/runner.py::run_backtest()` does
exist with train/test parameters and calls the live decision function
directly, but its internal bar-slicing for leakage was not audited this
pass (flagged in §3 as the top follow-up). Running it without that audit
first would risk reporting a number that isn't trustworthy either way.

## 10. Regime Analysis

Post-fix (current logic) subset only, n=47 (pooled-sample regime numbers
are reported in §16 for context but are subject to the same contamination
caveat):

| Regime | n | Win rate | Expectancy | PF | Net |
|---|---|---|---|---|---|
| TRENDING_DOWN | 27 | 0.407 | −0.802 | 0.862 | −21.65 |
| TRENDING_UP | 12 | 0.417 | −3.062 | 0.367 | **−36.75 (worst)** |
| RANGE | 8 | 0.500 | −1.188 | 0.510 | −9.50 |

No regime shows a positive PF in the post-fix subset. Per your instruction,
no regime is called "best" — TRENDING_DOWN is *least bad*, at n=27, still
negative. This is consistent with §5's code-level finding that
trend-continuation setups are blocked by default, leaving structurally
late/counter-trend entries as the dominant TRENDING_UP/DOWN trades.

Pooled-sample BREAKOUT_REGIME/REVERSAL_REGIME (n=2 each, 100% win) —
**explicitly flagged by the adversarial review as decorative, not
evidential; must not be read as "these regimes work."**

## 11. AI Comparison

**NOT TESTED — no comparison is possible with current data.** Confirmed
(consistent with the prior Autonomous Scalper Paper Validation report):
no trained ML model exists anywhere in the live decision path. One real,
lightweight ML model exists (`app/optionchain/ann_confirm.py`, online
logistic regression + isotonic calibration) but is dark — zero references
from the live path. The one input→output→outcome join log that does exist
(`fsg_shadow_log`) has only 36 rows, **zero** joined to a real resolved
outcome — nowhere near enough to run the requested AI-only / quant-only /
AI+confirmation / AI-as-veto comparison. **This section cannot be
completed until either the dark ANN model is wired in and run for a real
period, or `fsg_shadow_log` accumulates enough joined outcomes.**

## 12. Confidence Calibration

`confidence` is a **categorical field (LOW/MEDIUM/HIGH), not a numeric
0–100% score** — the exact 70/80/90% buckets requested do not exist in
this system's output. Calibration was measured against the categories
that do exist, on the post-fix (current logic) subset (n=47, small):

| Confidence | n | Win rate | Expectancy | PF |
|---|---|---|---|---|
| HIGH | 22 | 0.409 | +0.114 | 1.02 (barely breakeven) |
| LOW | 20 | 0.450 | −2.312 | 0.403 |
| MEDIUM | 5 | 0.400 | −4.830 | 0.258 |

For contrast, the **pooled** (contaminated) sample showed the opposite
ordering — HIGH performed *worst* (win rate 0.333) — which is now known to
be an artifact of pooling pre- and post-fix data rather than a real
calibration signal. **WEAK EVIDENCE, not SUPPORTED**: post-fix HIGH
confidence is directionally the best bucket and the only one at or above
breakeven, but n=22 is too small to certify real calibration, and MEDIUM's
n=5 is too small to say anything at all. **Do not trust model confidence
without more data — the calibration picture has already reversed once
when the sample was corrected for contamination, and could reverse again
with more data.**

## 13. No-Trade Analysis

**INSUFFICIENT EVIDENCE.** The no-trade engine's rejection reasons are
logged live (per the prior Autonomous Scalper Paper Validation report:
stale data, disconnected feed, insufficient OI, abnormal spread, poor
RR/EV, daily loss limit, position limit are all real, enforced, tested
gates), but **no mechanism retroactively tracks what price actually did
after a rejected setup** — there is no "shadow outcome" log for NO_TRADE
decisions the way there is for taken trades. It is therefore not possible
this phase to answer "how many rejected setups would have been profitable
vs losses" — that data simply isn't being captured. **This is a concrete,
actionable gap**: adding a lightweight shadow-outcome tracker for rejected
setups (log the setup, don't act on it, check price N minutes later)
would let a future phase actually answer this question instead of leaving
it as INSUFFICIENT EVIDENCE indefinitely.

## 14. Cost/Slippage Impact

Real, validated per-instrument costs exist for exactly two symbols
(`app/institutional_edge/costs.py`) and were applied to the full 179-trade
sample:

| Symbol | n | Gross (₹) | Cost (₹) | Net (₹) |
|---|---|---|---|---|
| NATURALGAS | 57 | −2,062.50 | 6,469.50 | **−8,532.00** |
| CRUDEOIL | 40 | −12,630.00 | 7,380.00 | **−20,010.00** |

Both instruments go from already-negative-or-marginal gross to clearly
and substantially more negative net. **No validated cost model exists for
NIFTY, BANKNIFTY, or SENSEX** — their reported P&L is gross only, and per
§6, slippage is never modeled in the live path for any symbol, meaning
even these net figures likely still understate real-world cost (spread
and execution latency are not included, only brokerage/exchange
fees/CTT). **SUPPORTED: realistic costs make the picture strictly worse,
never better, everywhere they could be measured.**

## 15. Adversarial Findings

Full adversarial pass (Agent 6) against every finding above, in both
directions:

- Sample-size check: overall win-rate 95% CI ≈ [0.347, 0.491] — wide, but
  its upper bound still sits below breakeven for this strategy's typical
  RR, so the *directional* negative finding survives. BANKNIFTY's
  apparent edge (win rate 0.571, n=35) has a CI that **fully overlaps**
  the overall losing population — **not distinguishable from noise.**
- Small-sample "100% win rate" regimes (n=2 each) are **decorative, not
  evidential** — explicitly flagged not to be reported as findings.
- **Most damaging finding**: the pooled dataset mixes trades from at least
  two confirmed logic-inversion-bug periods and one explicit in-sample
  parameter recalibration — every other cut inherits this contamination.
  This is why §8/§10/§12 above are reported on the post-fix subset with
  explicit small-n caveats rather than the full pool.
- No survivorship bias found (all 179 rows accounted for, none hidden or
  excluded).
- No duplicate-signal inflation found (zero repeated `signal_id`s).
- **Fills are, if anything, too favorable, not too harsh** — no slippage
  is modeled on entry, meaning the already-negative expectancy is more
  likely an *understatement* of real-world performance than an
  overstatement.
- No mfe/mae computation anomalies found.

## 16. Statistical Uncertainty

Every subset in this report is small by the standards needed for a
confident trading-edge claim: n=179 pooled (contaminated), n=47 post-fix
(clean-logic but still parameter-contaminated for 2 of 5 symbols), and
every by-symbol/by-regime/by-confidence cut smaller still (as low as
n=2). Standard errors at these sample sizes are large relative to the
effect sizes being measured (see §15). **No number in this report should
be read as more precise than "the sign is probably right, the magnitude
is not reliable."**

## 17. Failure Conditions

The largest, most concrete, code-verified failure mode found: **the EV
gate approves trades using an idealized payoff ratio (~1.55R) it has never
actually delivered** (real avg_loss > avg_win in magnitude), compounded by
**an option-selection process that structurally favors the fastest-
theta-decay delta band while weighting theta at only 4% of its score**,
with **no realized-P&L slippage model** to reveal how much of the already-
negative result is being further hidden by optimistic fills. Secondary
failure mode: **TRENDING_UP entries are structurally late/counter-trend**
because continuation setups are blocked by default.

## 18. Evidence Supporting an Edge

- BANKNIFTY, post-fix: n=15, win rate 0.533, PF 1.952, net +₹42.75 (points,
  not cost-adjusted) — the only symbol/period cell in the entire dataset
  showing a positive result.
- Post-fix HIGH-confidence trades: n=22, barely breakeven (PF 1.02).

Both are **WEAK EVIDENCE at best** — neither survives the sample-size
scrutiny in §15/§16 (BANKNIFTY's CI overlaps the losing population;
HIGH-confidence is barely above 1.0 at n=22). **No cell in this dataset
constitutes SUPPORTED or VERIFIED evidence of a real edge.**

## 19. Evidence Against an Edge

- Negative expectancy in the full pool (−0.723 pts/trade), in the pre-fix
  subset (−0.467), and — more relevantly for "the current strategy" — in
  the post-fix subset (−1.445, worse).
- Real, validated costs turn NATGAS/CRUDEOIL from marginal-to-negative
  gross into clearly negative net.
- A code-verified, quantified mechanism (the EV gate's unrealistic payoff
  assumption) that would predict exactly this kind of negative-expectancy
  outcome, independent of the sample-contamination issue.
- A second code-verified mechanism (theta-decay-blind, IV-blind option
  selection) consistent with the observed high TIME-exit rate (64/179)
  and low TARGET-hit rate (11/179).
- No slippage modeling means real-world performance is likely worse than
  even these negative numbers show.

This is the stronger, better-supported side of the evidence.

## 20. Required Next Experiment

In priority order:
1. **Fix the sample-contamination problem before drawing any further
   conclusion**: freeze the strategy (no logic or parameter changes),
   then collect a genuinely single-configuration sample of real paper
   trades going forward — the current history cannot be cleaned after the
   fact, only ring-fenced going forward.
2. **Audit `app/backtest/runner.py`'s bar-slicing for leakage** (§3) before
   trusting any number from it — if clean, it could accelerate reaching a
   larger sample without waiting weeks for live paper trades.
3. **Replace the EV gate's hardcoded `avg_win=None, avg_loss=None`** with
   real historical avg_win/avg_loss (once a clean sample exists) and
   re-measure — this is the single most concrete, actionable, already-
   quantified defect in this report.
4. **Add a shadow-outcome tracker for rejected (NO_TRADE) setups** so §13
   can eventually be answered instead of staying INSUFFICIENT EVIDENCE.

---

## FINAL ANSWERS TO THE SEVEN RESEARCH QUESTIONS

**1. Does the current strategy show statistically meaningful evidence of
an edge?**
No. DISPROVED for the pooled data (negative expectancy, PF<1). For the
clean post-fix subset specifically: INSUFFICIENT EVIDENCE to conclude
anything with confidence (n=47, and even that subset carries residual
contamination for 2 of 5 symbols) — but what evidence exists points
negative, not positive, in that subset too.

**2. Is the evidence out-of-sample?**
No, not cleanly. The 179-trade history spans at least two confirmed
logic-inversion-bug periods and one explicit in-sample parameter
recalibration. This is the single biggest methodological problem in this
validation.

**3. Does the edge survive realistic costs/slippage?**
No. Where a real cost model exists (NATGAS, CRUDEOIL), applying it turns
an already negative-to-marginal gross result substantially more negative
net. No slippage is modeled anywhere in the live P&L, meaning even these
numbers likely understate real-world cost.

**4. Is the edge stable across market regimes?**
There is no edge to be stable — every regime with a usable sample size in
the post-fix subset (TRENDING_DOWN, TRENDING_UP, RANGE) shows negative
expectancy. TRENDING_UP is worst, with a coherent code-level explanation
(continuation setups blocked by default, entries structurally late).

**5. Does AI add measurable incremental value?**
Cannot be measured. No trained ML model runs in the live decision path.
The one real ML model that exists is dark/unreachable, and the one
outcome-joinable shadow log has zero resolved joins.

**6. What is the largest known failure mode?**
The EV gate accepts trades against an idealized ~1.55R payoff ratio it has
never actually delivered (real losses run larger than real wins),
compounded by option selection that structurally favors the option delta
band with the fastest theta decay while weighting theta at only 4% of its
score, with no slippage modeling to reveal the true gap.

**7. What exact experiment should be performed next?**
Freeze the strategy's logic and parameters, then collect a fresh,
genuinely single-configuration sample of live paper trades (or,
conditional on a leakage audit passing, run `app/backtest/runner.py`
against historical data) large enough to narrow the confidence intervals
in §15 — before changing anything else, including before applying the
already-identified EV-gate fix, so that fix's effect can itself be
measured cleanly rather than added to an already-moving baseline.

No live-trading recommendation is made. This report is a research
artifact, not a deployment decision.
