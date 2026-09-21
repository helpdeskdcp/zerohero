# TRADING LOGIC FORENSIC AUDIT — 2026-09-21

Read-only. No production code was modified to produce this report (verified
at the end via `git diff --stat`). Baseline commit: `ab40e0a`.

## 1. Architecture

```
Market data (AngelOne WS/REST, app/connectors/angelone.py, app/l2capture/)
  -> app/histcap/ (candle/quote/greeks capture, market_history.db)
  -> app/engines/sr_engine.py::compute_sr()          [S/R zones, ATR, VWAP, GEX]
  -> app/engines/regime_mtf.py::detect_regime()/mtf_alignment()
  -> app/engines/state_classifier.py::classify()     [4-state + false-breakout, 9-component score]
  -> app/engines/option_engine.py::analyse_leg()/select_option()/ev_gate()
  -> app/engines/scalp_strategy.py::decide_from_context()   <-- SOURCE OF TRUTH
       -> app/backtest/calibration.py::predict()     [raw score -> probability]
       -> optional chain_gate / sr_gate (both default OFF)
  -> app/autoscalp/runner.py (live loop, 30s cadence per symbol)
       -> app/autoscalp/safeguards.py::check_entry()  [real trade-open gate]
       -> app/ai/shadow.py::run_shadow_decision()      [AFTER the decision, comparison-only]
            -> app/ai/behavior_ai.py -> app/ai/ai_client.py (Groq/OpenAI) -> app/ai/fusion.py
            -> app/ai/shadow_notify.py (Telegram, gated on fused final_state)
  -> app/db.py (scalp_signals = real paper trades; shadow_decisions = per-tick shadow log)
  -> app/ai/shadow_outcome.py (forward-outcome + setup-dedup, read-only reporting)
```

## 2. Runtime Flow

Traced via actual function calls (not filenames):
`autoscalp/runner.py::tick_once()` -> per symbol, every `decide_every_sec` (default 30s) ->
`scalp_strategy.decide_from_context(bars_by_tf, chain, atm=..., calib=self.calibration(), ...)`
-> real decision -> if BUY_CE/BUY_PE and safeguards pass, `_open_positions()` records a
real trade (`scalp_signals` row) -> separately, `shadow.run_shadow_decision()` is called
with the SAME `sig` dict for AI-comparison logging, regardless of whether a real trade
opened. These two paths (real trade open vs. shadow log) are **independent** — confirmed
by reading `runner.py`'s call sites; shadow logging never gates or blocks a real entry,
and a real entry never waits on shadow/AI output.

## 3. Source of Truth

**`app/engines/scalp_strategy.py::decide_from_context()`** — the only function that
returns `BUY_CE`/`BUY_PE`/`NO_TRADE`/`WATCH`. Confirmed by reading the file in full:

1. Final decision: `decide_from_context()` (line 277-554).
2. Confidence: `_confidence()` (line 185), refined by `effective_confidence()` (line 221,
   caller-side data-quality/calibration downgrade — never upgrades).
3. Probability: `_score_to_prob()` (line 173) -> delegates to `calibration.predict()`.
4. Direction: `state_classifier.classify()`'s `direction` field (BULLISH/BEARISH from the
   winning state), consumed at line 356.
5. Entry: `_plan_from_leg()` (line 251) — `entry = sel["ltp"]`, the selected leg's live LTP.
6. Exit (SL/T1/T2): also `_plan_from_leg()` — ATR-multiple off entry, clamped to the
   leg's own S/R.
7. Veto power: (a) `ev_gate()` (option_engine.py) — EV/RR gate, hard NO_TRADE; (b) MTF
   opposing-HTF block (line 362); (c) `ce_pe_confirmation` CONFLICT/OPPOSING (line 397);
   (d) optional chain_gate/sr_gate CONTRADICT-when-marginal (both default OFF); (e)
   `app/ai/fusion.py::fuse_decision()` — but only on the SHADOW copy, never on the real
   decision returned by `decide_from_context()` itself (see §12).
8. Override authority: nothing overrides `decide_from_context()`'s own output for the
   REAL trade path. AI can only downgrade the SHADOW copy (§12).
9. Final authority: `decide_from_context()`, gated additionally by `safeguards.check_entry()`
   before a real position opens (a real-world/risk gate, not a signal-logic gate).

**SOURCE OF TRUTH = `app.engines.scalp_strategy.decide_from_context()`**

## 4. Entry Logic (CE and PE, as implemented — not idealized)

Both sides share one code path (`want = "CE" if state in BULLISH else "PE"`); the
asymmetry is only in which `state_classifier` state maps to which side.

| Condition | Value/Threshold | File:Function | Mandatory/Optional |
|---|---|---|---|
| S/R must resolve | `sr.status == "OK"` | sr_engine.py::compute_sr | Mandatory |
| Clean 4-state classification | `state != "NONE"` | state_classifier.py::classify | Mandatory |
| Min state score | `min_state_score` = 45.0 | state_classifier.py:351 | Mandatory |
| Regime not blocked | `block_regimes=["UNSTABLE"]` | scalp_strategy.py:38 | Mandatory (configurable) |
| Signal type not blocked | `block_signal_types=["RESISTANCE_BREAKOUT"]` | scalp_strategy.py:39 | Mandatory (configurable; see §21 for why RESISTANCE_BREAKOUT is blocked — documented OOS evidence, not a guess) |
| Time-of-day not blocked | `block_tod=["AFTERNOON"]` | scalp_strategy.py:40 | Mandatory (configurable) |
| MTF not strongly opposing | `htf_dominant and not aligned` blocks | scalp_strategy.py:360-363 | Mandatory |
| CE/PE cross-check agrees | `ce_pe_confirmation` != CONFLICT/OPPOSING | option_engine.py::ce_pe_confirmation | Mandatory |
| Tradeable contract exists | a candidate strike has real LTP | scalp_strategy.py:401-410 | Mandatory |
| Min option quality | `min_option_quality` = 45.0 | scalp_strategy.py:411 | Mandatory |
| EV gate | `ev_gate()` pass, cost-adjusted | option_engine.py::ev_gate | Mandatory |
| Confidence floor | `require_min_confidence` (default LOW = no-op) | scalp_strategy.py:492 | Optional (config) |
| Chain gate (OI/PCR) | opt-in, `enabled: False` default | scalp_strategy.py:64-79 | Optional, OFF |
| Live SR confirmation gate | opt-in, `enabled: False` default | scalp_strategy.py:94-106 | Optional, OFF |
| Safeguards (real trade only) | session hours, feed freshness, max concurrent, duplicate underlying+side, daily loss cap, consecutive losses, premium sanity | autoscalp/safeguards.py::check_entry | Mandatory, real-trade path only |

No cooldown/duplicate-setup condition exists inside `decide_from_context()` itself — the
only real-trade-level duplicate guard is safeguards.py's `(underlying, side) in open_keys`
check (§9, §12).

## 5. Exit Logic

`_plan_from_leg()` (scalp_strategy.py:251-274) computes target/stop DETERMINISTICALLY at
signal time: `stop_loss = entry - sl_atr*ATR` (clamped to respect the leg's own support),
`target_1 = entry + t1_atr*ATR` (clamped to the leg's own resistance), `target_2` similarly,
plus a `trailing_stop` distance and `max_hold_sec` (time-based exit, default 1500s = 25min).
All of this is computed once, at entry, from the option leg's own ATR/S&R — not
re-evaluated live tick-by-tick inside this module. Actual position monitoring/exit
execution (checking live price against these levels, firing the trailing stop, the
time-based exit) happens in `autoscalp/runner.py`'s position-monitoring loop, not in
`scalp_strategy.py` — this audit did not trace that loop function-by-function (out of the
"decision engine" scope the user's Phase 1 diagram implies), flagged as an INVESTIGATE
item for a follow-up if the user wants exit-execution audited with the same rigor.
Exit rules are **deterministic** (ATR-multiple based), not AI-dependent — AI/shadow never
touches SL/target/trailing values anywhere in the codebase (confirmed by grep: no
`app/ai/*` module writes to `stop_loss`/`target_1`/`trailing_stop`).

## 6. Indicator Audit

Every component `state_classifier._eval_break()`/`_eval_reversal()` computes, per the
`_W` weights (price_action 0.20, level_strength 0.16, volume 0.12, oi 0.12, momentum
0.14, vwap 0.08, atr 0.08, htf 0.06, retest 0.04):

| Component | Timeframe | Calculation | Independent or correlated? |
|---|---|---|---|
| price_action | `sr.tf` (usually 5m) | `close_beyond` bool or `atr_dist/brk_min` | **Correlated with `atr`** — see §7, real finding |
| level_strength | n/a | `zone["strength"]` from sr_engine's own clustering (touch count/age, not re-derived here) | Independent |
| volume | same tf | `vol_ratio` = bar volume / 10-bar SMA | Independent (real volume data) |
| oi | n/a | `_oi_at()` — OI-write near the level, from the live option chain | Independent (real OI) |
| momentum | same tf | `roc` = 3-bar rate of change | **Correlated with price_action/atr** — same underlying price series, different transform |
| vwap | same tf | `close > vwap` boolean, direction-aligned | Correlated with price_action (same close price, different reference) |
| atr | same tf | `atr_dist` (same variable as price_action's fallback branch) | **Correlated with price_action** — literal shared variable, see §7 |
| htf | 30m/15m (`_htf_trend`) | EMA20 vs EMA50 slope | Independent (different timeframe) |
| retest | same tf | whether a later bar re-tested the level and held | Partially independent (depends on price_action's own break/reversal detection but adds new temporal information) |

`_rsi`/`_ema_series`/`_atr`/`_sma` (from `signal_engine.py`, reused not reimplemented) are
the only indicator primitives; no MACD/Supertrend/Bollinger anywhere in this codebase
(confirmed by grep — matches today's earlier PDF-vs-architecture audit finding that
Bollinger Bands are absent).

## 7. OI / Option Data Audit

Traced: `state_classifier._oi_at()` reads OI/OI-change from the `chain` argument (the
live option chain, passed in from the caller — ultimately `app/optionchain/` and
`app/histcap/`'s captured data). `scalp_strategy._chain_bias()` (the opt-in chain gate)
separately computes PCR + fresh-OI-writing bias over an ATM window. Fallback behavior,
verified in code:
- `_oi_at()`: `if not chain or level is None: return 0.0` — a **zero**, not a `None` or a
  flag. This means missing OI data becomes a *neutral-low* score contribution (0.0 *
  weight = 0 contribution to state_score), not literally "bullish" or "bearish" evidence
  — it does NOT silently become directional evidence, but it also isn't distinguishable
  from "OI genuinely showed no writing," which a stricter contract would flag separately.
  Confidence-wise this is fine (a missing signal just doesn't move the score); an
  INVESTIGATE item only in the sense that `_oi_at` can't be told apart from a real
  weak-OI reading downstream.
- `_chain_bias()` (the opt-in gate): explicitly returns `{"verdict": "INSUFFICIENT", ...}`
  when `coverage < min_coverage` — this ONE path does correctly distinguish "not enough
  data" from "data says neutral," and an INSUFFICIENT verdict is a documented no-op (never
  vetoes, per the module's own docstring, confirmed in code at line 148-150).
- Greeks/IV: not read anywhere in `scalp_strategy.py`'s decision path at all — confirmed
  by grep, no `delta`/`gamma`/`theta`/`vega`/`iv` reference in scalp_strategy.py or
  state_classifier.py. `option_engine.py::analyse_leg()` (not fully traced line-by-line in
  this pass — INVESTIGATE if the user wants option_engine.py audited with the same depth)
  is the only place Greeks might enter option-leg selection/quality scoring.

## 8. Scoring / Probability Audit

**`state_score`** (state_classifier.py:110-111): `_score(comp, weights) = round(100 *
sum(comp[k] * weights[k] for k in weights), 1)` — a straight weighted linear sum, each
`comp[k]` pre-clamped to [0,1] by its own formula, weights sum to exactly 1.00
(0.20+0.16+0.12+0.12+0.14+0.08+0.08+0.06+0.04 = 1.00, verified by arithmetic) so the raw
sum is bounded [0,100] by construction — **cannot exceed its intended range**.
`false_risk` then applies a penalty multiplier (0/0.25/0.5/0.8) on top, and a hard cap to
20.0 if `LIKELY_FALSE` without a held retest (line 344) — this is a second, independent
adjustment layer, not part of the weighted sum itself.

**`blended` score** (scalp_strategy.py:424): `0.62*state_score + 0.24*option_quality +
0.14*mtf_magnitude(capped)` — weights sum to 1.00, each input already 0-100 bounded, so
`blended` is bounded [0,100] before the `_DEFAULT_FILTERS` multipliers (which only ever
*reduce* it, since every configured multiplier found is <=1.0) — cannot exceed range.

**Double-counting**: found a real one (§6/§7 note) — `price_action` and `atr` are both
derived from the SAME `atr_dist` variable in `_eval_break()`'s non-`close_beyond` branch
(`price_action = atr_dist/brk_min`, `atr = atr_dist/1.0`) — two different linear
transforms of one measurement, feeding two separately-weighted components (0.20 + 0.08 =
0.28 combined weight for what is, in that branch, one underlying observation: "how far
past the level, in ATR units"). This does not make the score exceed [0,100] (both are
still clamped to [0,1] before weighting) but it does mean ~28% of the state_score's
weight, in that branch, is really tracking one signal counted twice under two names —
see INVESTIGATE item #1.

**Confidence vs. probability**: genuinely distinct in code. `probability` = calibrated
sigmoid output (0.0-1.0, `_score_to_prob`); `confidence` = a 3-bucket label (LOW/MEDIUM/
HIGH) derived FROM probability via fixed thresholds (`_confidence()`, line 185-192: HIGH
>=0.66, MEDIUM >=0.56, else LOW) plus false-risk/MTF-conflict overrides. Not conflated —
confirmed these are two separate fields on every decision dict, never aliased.

**Historical win rate as probability**: this is exactly what `calibration.py::_fit_logistic()`
does, deliberately and by design (bucket empirical win rate, fit a logistic curve to it) —
not a bug, that IS the calibration methodology (spec-5, documented in the module's own
docstring). The bug fixed today (commit `04136c7`) was that thin/unsupported empirical
buckets got extrapolated/floor-inflated into overconfident curves — the methodology itself
(use resolved-trade win rate as the basis for probability) is sound and standard; the
implementation had two concrete defects, both fixed (verified: `_K_LO` floor removed,
`x_lo`/`x_hi` extrapolation clamp added, sample-size shrinkage added — read fresh from
`app/backtest/calibration.py` this pass, matches what was shipped).

## 9. Data Leakage / Look-Ahead Audit

Checked `decide_from_context()` and `state_classifier.classify()`: both are pure functions
over `bars_by_tf`/`chain`/`calib` passed in — no internal fetch of "current" or "future"
data, no global mutable state read. `_htf_trend()` only reads `bars_by_tf.get(tf)` (passed
in, already-closed bars per the caller's contract). `classify()`'s breakout/reversal
detectors (`_eval_break`, `_eval_reversal`) only index into `C[i-1]`/`C[i]` for `i` in the
last 4-6 bars of the ALREADY-PASSED-IN series — no forward indexing (`C[i+1]` or similar)
found anywhere in either file (verified by reading every loop). `calibration.fit()`/
`predict()`: pure functions over passed-in samples, no I/O.

The one place look-ahead COULD enter is upstream, in whatever assembles `bars_by_tf` for
a given decision timestamp (live: `autoscalp/runner.py`'s bar aggregation; backtest:
`app/backtest/runner.py`'s `ReplayHarness`) — not re-audited line-by-line in this pass
(ReplayHarness's no-lookahead property IS already covered by an existing passing test,
`test_backtest_no_lookahead_calibration_is_frozen`, confirmed reading that test file
earlier this session). **No look-ahead found in the core decision/scoring functions
themselves.** Severity: none found at HIGH confidence for the audited files; upstream bar
assembly is INVESTIGATE only in the sense of "not re-verified in this specific pass," not
because any suspicious code was seen.

## 10. Repeated Signal / Setup Audit (verifying the setup-dedup fix)

Traced `app/ai/shadow_outcome.py::group_into_setups()` (built today, commit `ab40e0a`) —
confirmed by reading the current file:
- **What is a setup?** A contiguous run of `shadow_decisions` rows for one symbol sharing
  direction + resolved option leg (`option_token`, or `strike`+`expiry` fallback), with no
  gap `> 120s` (4x the default 30s tick cadence) and no intervening real `NO_TRADE`/no-leg
  row breaking the chain.
- **What keeps it alive?** Consecutive ticks on the same leg within the gap tolerance —
  `fused_final_state` oscillating (AI confidence noise) does NOT break the chain; only the
  DETERMINISTIC engine's own decision/leg identity does (confirmed: test
  `test_group_into_setups_fused_state_oscillation_does_not_break_the_chain` passes).
  A different leg, a real `NO_TRADE`, or a `>120s` gap starts a new setup.
- **Invalidation/cooldown**: this function is read-only ANALYTICS over an already-logged
  history — it does not implement or enforce a live cooldown; it only counts, after the
  fact, how many real opportunities a batch of ticks represents.

**CRITICAL DISTINCTION** (get this right, per the task's own instruction): this dedup
fix lives ENTIRELY in `shadow_outcome.py`, which is a reporting/review module. It does
**not** gate or dedupe the live decision loop, `autoscalp/runner.py`, or real trade entry.
The REAL production dedup for actual money-adjacent decisions is
`safeguards.check_entry()`'s `(underlying, side) in open_keys` check (§4, §12) — a
completely separate, already-existing mechanism this audit re-confirmed by reading
`safeguards.py` fresh (line 135: `if (underlying, side) in open_keys: return False,
f"duplicate: {underlying} {side} already open"`). Today's setup-dedup fix corrected how a
human/analyst COUNTS shadow-log rows after the fact; it did not (and did not need to) fix
anything in the real trade-opening path, which was never miscounting to begin with.

**Replay verification** (today's real NATURALGAS episode, re-confirmed from
`data/research/naturalgas_dedup_audit_2026-09/VERDICT.md`, itself cross-checked against
`data/chanakya.db` today):
- RAW EVALUATIONS: 16 (`shadow_decisions` rows, same leg, ~30s apart)
- UNIQUE SETUPS (post-fix): 1
- ENTRY EVENTS (real `scalp_signals` rows for NATURALGAS today): 0
- COMPLETED TRADES: 0 — the deterministic signal was vetoed by AI/fusion (shadow path
  only) on every tick and never reached a tradeable state; separately, no real trade for
  NATURALGAS exists in `scalp_signals` today at all.

## 11. AI Layer Audit

Two providers, confirmed live in `app/ai/`:

| Call | Model | Purpose | Frequency | Timeout | Fallback | Affects trading? | Can override deterministic? |
|---|---|---|---|---|---|---|---|
| `ai_client.chat_completion_json` (Groq primary) | env `GROQ_MODEL` (`openai/gpt-oss-120b` observed live) | market-regime/anomaly classification, `behavior_ai.py`'s system prompt | Once per tick where `behavior.ai_required` (shadow.py:44), NOT every tick | `GROQ_TIMEOUT_SEC` default 8.0 | Falls through to OpenAI on any non-OK status | **No** — shadow-only, see below | **No** |
| `ai_client` OpenAI fallback | env `OPENAI_MODEL` (`gpt-4o-mini` observed live) | same, only invoked when Groq fails | Same tick, only on Groq failure | `OPENAI_TIMEOUT_SEC` default 8.0 | none further (returns ERROR with both providers' reasons) | **No** | **No** |

Classification per the spec's own taxonomy: **SHADOW MODE**. Confirmed by re-reading
`app/ai/fusion.py::fuse_decision()` fresh this pass: the function's only authority is
`_weaken()` (moves the fused state TOWARD `NO_TRADE`, never away from it, never crosses
from BUY-side to SELL-side or vice versa — verified by re-reading the `_weaken()` index
arithmetic). There is no code path in `fusion.py`, `shadow.py`, or anywhere in
`autoscalp/runner.py` where an AI result can (a) create a decision the deterministic
engine didn't already produce, (b) flip CE<->PE, or (c) touch the REAL trade path at all
— `shadow.run_shadow_decision()`'s output is written only to the `shadow_decisions`
table and (optionally) Telegram, never fed back into `decide_from_context()` or
`safeguards.check_entry()`. **AI cannot silently override deterministic trading rules —
confirmed structurally, not just by convention.**

## 12. Backtest Methodology Audit

`app/backtest/runner.py::run_backtest()` — chronological TRAIN/TEST split (disjoint date
ranges passed by the caller), calibration fit on TRAIN only and frozen before scoring TEST
(`_build_decide(calib, ...)` closure, confirmed by reading `run_backtest()` fresh: `calib`
is computed once from `_train_samples()` before `res_te` is ever built). Entry/exit price:
uses the same `_plan_from_leg()` as live (real leg LTP at signal time, ATR-based SL/target)
— no separate, more-optimistic backtest-only pricing model found.

**Cost/slippage/spread: NOT modeled by `run_backtest()` itself.** Confirmed by grep — zero
references to `est_cost_r`, `slippage`, or `cost_model` anywhere in `app/backtest/runner.py`.
`decide_from_context()` DOES have a cost hook (`cfg.get("est_cost_r", 0.0)`, scalp_strategy.py:441,
fed into `ev_gate`), but `run_backtest()` passes whatever `config` dict the CALLER supplies
— if a caller doesn't explicitly set `est_cost_r` in that config, every symbol's backtest
runs cost-free, not just NIFTY. This generalizes what today's `ta_options_pdf_upgrade/
VERDICT.md` flagged specifically for NIFTY (`est_cost_r=0 by engine design`) — it's not a
NIFTY-specific gap, it's that the canonical backtest entrypoint has **no symbol-aware cost
defaults at all**, unlike the live path (`effective_profile`/`instrument_profiles`, which
DOES set real per-symbol cost figures — confirmed present in the codebase from earlier
session work, not re-verified line-by-line in this pass). `app/institutional_edge/` (the
module built for real-cost EV) is referenced NOWHERE in `app/backtest/` or
`app/autoscalp/` (confirmed by grep, zero hits) — it is a fully disconnected/dark module,
matching its own prior EXPERIMENTAL classification, not silently wired in anywhere.
**A backtest PF/win-rate number from `run_backtest()` without an explicitly-supplied
`est_cost_r` is a pre-cost number, not a real-world one.** See INVESTIGATE item #2.

Position sizing, overlapping-trade handling: `ReplayHarness(..., max_concurrent=cfg.get(
"max_concurrent", 1))` — default 1 concurrent position, so the backtest engine does not
by default allow overlapping trades either (mirrors the live `safeguards` intent, though
via a different mechanism — not literally the same code path).

## 13. Current Logic Diagram

```
DATA (angelone / histcap)                                    GREEN — real, captured, tested
 |
FEATURES (sr_engine ATR/VWAP/GEX, state_classifier 9-comp)    GREEN — deterministic, bounded
 |                                                             YELLOW — price_action/atr double-count (see #1)
ENGINES (regime_mtf, option_engine)                            GREEN
 |
SCORES (state_score, blended)                                  GREEN — bounded by construction
 |
FUSION (calibration.predict -> probability)                    GREEN — fixed today (04136c7), validated OOS
 |
SETUP (decide_from_context's own state/leg selection)           GREEN
 |
CONFIRMATION (ce_pe_confirmation, optional chain/sr gates)       GREEN (gates OFF by default)
 |
RISK CHECK (ev_gate, safeguards.check_entry)                    YELLOW — ev_gate's cost input often 0 in backtest (see #2)
 |
FINAL SIGNAL (decide_from_context's return dict)                 GREEN
 |
EXECUTION/SHADOW (autoscalp real trade / shadow.py log)          GREEN — structurally separated, confirmed
```

## 14. NO_TRADE Logic

Every `NO_TRADE`/`WATCH`/advisory path returns a `reason` string (confirmed: every
`out_none(...)` call site in `decide_from_context()` passes a specific reason — "S/R
unavailable", "no clean state", "filter: regime X blocked", "MTF: strong opposing
higher-timeframe structure", "CE/PE confirmation CONFLICT/OPPOSING", "no tradeable
contract on the wanted side", "option quality N < min", "EV gate: <gate reason>", "option-
chain bias contradicts a marginal setup (...)", "SR confirmation contradicts a marginal
setup (...)", "confidence LOW -> watch only"). This already satisfies the spec's Phase 8
ask (reason codes on every NO_TRADE) — no new code needed, confirmed by direct reading, not
assumption.

## 15. KEEP UNCHANGED

- `app/engines/scalp_strategy.py::decide_from_context()` — bounded scoring, real veto
  chain, no look-ahead found, structurally separated from AI.
- `app/engines/state_classifier.py::classify()` — deterministic, bounded, real false-
  breakout detection; the one correlation issue (#1) is a documented finding, not
  evidence the module is broken.
- `app/backtest/calibration.py` — fixed today with real OOS validation (§8).
- `app/ai/fusion.py::fuse_decision()` — downgrade-only authority re-verified structurally
  sound.
- `app/ai/ai_client.py`, `behavior_ai.py` — Groq/OpenAI orchestration, shadow-only,
  confirmed no path to override.
- `app/autoscalp/safeguards.py::check_entry()` — real, working overlap/risk gates.
- `app/ai/shadow_outcome.py` — today's setup-dedup fix, correctly scoped as analytics-only.
- Chain gate / SR confirmation gate (`_chain_bias`, `evaluate_sr_confirmation`) — both
  default OFF, byte-identical-when-disabled by the code's own design, no evidence either
  needs a change.

## 16. INVESTIGATE

**#1 — `price_action` and `atr` components share one underlying measurement in
`_eval_break()`'s non-`close_beyond` branch.**
- Evidence: `state_classifier.py:184` (`"price_action": ... atr_dist / brk_min`) and
  `state_classifier.py:190` (`"atr": ... atr_dist / 1.0`) — same `atr_dist` variable,
  two linear transforms, two separately-weighted components (0.20 + 0.08 = 0.28 combined).
- Observed problem: in that branch, ~28% of `state_score`'s weight tracks one real
  observation ("distance past the level in ATR units") counted under two component names,
  not two independent pieces of evidence.
- Possible consequence: `state_score` can be somewhat inflated specifically for setups
  where `close_beyond` is False but `atr_dist` is large — a milder, more localized version
  of the same "correlated evidence overweighted" failure mode as the calibration bug fixed
  today, though NOT a bug in calibration itself (calibration operates on the final
  `signal_score`, agnostic to how it was composed).
- Confidence: HIGH that the code shares the variable (read directly); MEDIUM on
  practical impact size (not backtested in this audit — Phase 13 forbids doing so).
- Test required: a real backtest A/B (current 9-component formula vs. a variant merging
  price_action+atr into one component with combined weight 0.28, or replacing atr with an
  independent measure) — out of scope for THIS audit per the user's own "do not optimize"
  instruction; flagging only.

**#2 — `run_backtest()` has no symbol-aware transaction-cost default; PF/win-rate numbers
from it are pre-cost unless the caller explicitly threads `est_cost_r` through `config`.**
- Evidence: zero references to `est_cost_r`/`slippage`/`cost_model` in
  `app/backtest/runner.py` (grep-confirmed); `decide_from_context()`'s cost hook
  (scalp_strategy.py:441) defaults to `cfg.get("est_cost_r", 0.0)` — silently 0 unless set.
  `app/institutional_edge/` (the real-cost-EV module) has zero references anywhere in
  `app/backtest/` or `app/autoscalp/` (grep-confirmed) — fully disconnected.
- Observed problem: any backtest run via the bare `run_backtest(symbol, train=..., test=...)`
  call (no explicit cost config) reports a cost-free PF/win-rate, for every symbol, not
  just NIFTY. This generalizes today's `ta_options_pdf_upgrade` finding.
- Possible consequence: any future backtest result quoted without checking whether the
  caller passed `est_cost_r` risks being read as more profitable than a real-cost run
  would show — exactly the class of error this session's entire NO-GO research history
  (SSL Hybrid, StochRSI+Supertrend, trend-swing, etc.) has repeatedly guarded against by
  explicitly modeling real AngelOne costs where those studies did it right.
- Confidence: HIGH (grep-confirmed absence, not inferred).
- Test required: none needed to fix this file (it's not broken, it's just caller-
  dependent) — the actionable follow-up is a review/documentation task: audit every
  place `run_backtest()` or `run_ablation()` is called and confirm which ones pass a real
  `est_cost_r`, flag the ones that don't. Not done in this pass (scope: core decision
  engine, not every research script that calls the backtest runner).

**#3 (lower severity) — Exit-execution loop (position monitoring / SL-target-trail firing
in `autoscalp/runner.py`) was not traced function-by-function in this pass.**
- Evidence: `_plan_from_leg()` computes levels at signal time; this audit did not verify
  HOW/WHERE those levels get checked against live price to trigger a real exit.
- Observed problem: none found — this is an honest scope gap, not a defect.
- Confidence: N/A (not investigated).
- Test required: a follow-up audit pass specifically on the position-monitoring loop, if
  the user wants the same rigor applied there.

## 17. Critical Issues

None found. No look-ahead, no fabricated-data-on-missing, no AI-override path, no
unbounded-score risk.

## 18. Medium Issues

- #2 above (backtest cost defaults) — affects the TRUSTWORTHINESS of any backtest number
  quoted without checking the cost config, not the live system's safety.

## 19. Low Issues

- #1 above (price_action/atr correlation) — a scoring-composition nuance, not a safety
  issue; the score stays bounded and NO_TRADE-biased regardless.
- #3 above (exit-loop not traced) — scope gap, not a finding.

## 20. Recommended Tests (not implemented in this audit, per its own read-only mandate)

- A backtest A/B specifically isolating the price_action/atr correlation (#1) — requires
  the same before/after, real-cost, walk-forward discipline as every other change this
  session, and explicit approval before touching `state_classifier.py`.
- A grep-based CI check (or a one-time repo-wide audit) confirming every `run_backtest`/
  `run_ablation` call site either passes `est_cost_r` or documents why it's intentionally
  omitted (e.g. a pure signal-detection study that isn't claiming profitability).
- If the user wants it: a follow-up forensic pass on `autoscalp/runner.py`'s position-
  monitoring/exit-execution loop with the same rigor as this audit gave the entry path.

## 21. Filter Policy Provenance (context for §4, not a new finding)

`_DEFAULT_FILTERS` (scalp_strategy.py:26-44) is not arbitrary — its own comment cites real
OOS evidence: RESISTANCE_BREAKOUT was net-negative in two independent OOS slices
(P6: -17.5/n11, P6.1: -17.5/n11) before being blocked; AFTERNOON similarly underperformed
in both slices; RANGE was only marginally negative and got a 0.7 down-weight rather than
an outright block to preserve sample. This audit did not re-verify those historical P6/
P6.1 backtest numbers from scratch (out of scope — they predate this session), but the
code's own comment is specific and falsifiable (cites exact figures), not a vague
rationale.

## 22-24. AI Role Summary, Backtest Methodology Summary, Look-Ahead Summary

Consolidated into §11, §12, §9 above respectively — kept together rather than repeated,
per this session's own "no gold-plating" convention (the user's earlier session-wide
"minimize tokens" instruction).

---

## Verification

```
$ git diff --stat -- app/
(empty)
```

No file under `app/` was modified by this audit. Only new files were created:
`data/research/trading_logic_forensic_audit_2026-09/AUDIT.md` (this file).

Baseline commit at time of audit: `ab40e0a` (HEAD, `main`).

**NO PRODUCTION LOGIC WAS MODIFIED DURING THIS AUDIT.**
