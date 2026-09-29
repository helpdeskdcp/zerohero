# Duplicate Logic Audit — zerohero / Chanakya AI

Phase 1 deliverable. Real duplication found (or already fixed) in this
codebase, grounded in actual reads, not assumed. The spec explicitly
requires this because a new orchestration layer on top of a codebase with
undetected duplicate logic will "fix" one copy and silently leave a
production bug live in the other -- this has ALREADY happened once in
this repo (see item 1).

## 1. Score-to-probability duplication — FIXED, closed

`app/engines/scalp_strategy.py::_score_to_prob` used to be a hand-copied
reimplementation of `app/backtest/calibration.predict()`'s exact sigmoid
formula and curve lookup. The K8 calibration-overconfidence fix (commit
`04136c7`, 2026-09-21) was applied to `calibration.py` but initially
missed the duplicate in `scalp_strategy.py` -- exactly the failure mode
this report exists to prevent. Consolidated: `_score_to_prob` now
delegates to `calibration.predict()` directly. **Lesson for the new spec's
Structure Generator / Strike Optimizer / Payoff Engine**: if any of those
need a probability, EV, or Greek calculation that already exists
elsewhere (see DEPENDENCY_MAP.md), import it -- do not reimplement even a
"simplified" version, because simplified copies are exactly how a later
fix misses one location.

## 2. Cost models — TWO, deliberately NOT duplicates, must not be merged

- `app/institutional_edge/costs.py` -- NSE index OPTIONS: STT 0.15%
  (buy+sell side).
- `data/research/orderflow_system/engine/costs.py` -- NSE index FUTURES:
  STT 0.05% (sell side only).

These are genuinely different real regulatory rates for different
instrument types, verified independently against live AngelOne/Zerodha
published rates in two separate sessions. A naive "de-duplication" pass
that notices "two cost models" and merges them into one would silently
introduce a real STT-rate bug. **Documented explicitly so a future
refactor doesn't do this.**

## 3. Regime/state classification — scattered, not consolidated, real risk

At least 4 independent places classify "what kind of market is this right
now," with different label sets and no shared enum:

- `app.engines.regime_mtf.detect_regime()` -- feeds AutoScalp's live
  decision (TRENDING_UP/TRENDING_DOWN/RANGE/UNSTABLE/BREAKOUT_REGIME/
  REVERSAL_REGIME/HIGH_VOLATILITY, confirmed live today).
- `app.orderflow.h1h7_state` -- its own state machine (H1_CONT/H7_TRAP/
  AMBIGUOUS/etc), unrelated label set.
- `app.mathematical_confluence.engine` -- its own regime read (NEUTRAL/
  TREND/etc, seen in signals_dashboard output).
- `app.backtest.oi_history_adapter`'s replay harness -- shows a
  `LOW_VOLATILITY` label (confirmed in a real ablation run today) that
  does NOT appear anywhere in `regime_mtf`'s live label set -- meaning
  either this is a genuinely separate classifier, or the label set has
  drifted between the live and backtest paths without anyone
  reconciling them.

**This is a real, unresolved risk**, not yet root-caused this session:
the new spec's Market Regime Engine (section 7) proposes a canonical
regime taxonomy (STRONG_BULL/BULL/RANGE_BULL/... /CHAOTIC). Before
building it, decide explicitly whether it REPLACES these 4, wraps them,
or is a 5th independent one -- do not let a 5th regime classifier join
the pile without a stated relationship to the existing 4.

## 4. Reversal/overshoot-and-reclaim pattern logic

`app.engines.state_classifier`'s 4-state classifier (used live by
AutoScalp) and `data/research/orderflow_system/strategy/_reversal.py`'s
`ReversalSetup` (research-only) both independently implement an
"overshoot beyond a trend extreme, then reclaim" pattern, with different
parameterizations, on different data (live index bars vs 5yr archive).
Not a bug (one is live production, one is a closed research experiment
that was REJECTED after 8 independent config attempts -- see that
directory's CHANGELOG.md), but worth knowing about if the new Structure
Generator (section 11) wants a "reversal" building block: the ALREADY
extensively-tested-and-rejected version is in orderflow_system, not
state_classifier -- read that CHANGELOG before re-deriving the same
negative result.

## 5. Margin/max-loss estimation — one real implementation, correctly NOT duplicated elsewhere

`app.hedging.risk.credit_spread_economics()`'s `max_loss_per_lot` is the
ONLY margin-adjacent calculation in this repo (confirmed today: no other
module computes anything margin-like). This is good (no duplication risk
here) but also means there is currently ZERO cross-check against a real
broker margin number anywhere -- see RISK_GAP_REPORT.md item 1.

## 6. Telegram notification — already consolidated

Prior-session work (`app.telegram_dispatcher`) already merged 5 separate
broadcaster implementations into one canonical dispatcher. Confirmed
still the sole path used by `app.hedging.runner._notify` and AutoScalp
today. No new duplication found here.

## 7. "Which symbols does this engine cover" lists — scattered, inconsistent membership

At least 5 independent places hardcode a symbol-group list with slightly
different membership:

- `app.optionchain.api._GROUPS` (NSE: NIFTY/BANKNIFTY/FINNIFTY/
  MIDCPNIFTY; BSE: SENSEX/BANKEX; MCX: CRUDEOIL/NATURALGAS)
- `app.hedging.runner.DEFAULT_CONFIG["symbols"]` (NIFTY/BANKNIFTY/SENSEX
  -- CRUDEOIL/NATURALGAS added today via a config PATCH, not a code
  default change, so a fresh `DEFAULT_CONFIG` reset would silently drop
  them again)
- `app.hedging.runner._OPTION_RESOLVER` / `_SYMBOL_EXCHANGE` (added
  today, NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY/SENSEX/BANKEX/CRUDEOIL/
  NATURALGAS -- a 6th list)
- AutoScalp's per-symbol resolved rows show NIFTY/BANKNIFTY/SENSEX/
  CRUDEOIL/NATURALGAS/NATGASMINI live today (a 7th, empirically-observed
  set, includes NATGASMINI which isn't in any of the above lists)
- `app.orderflow.service`'s default watch list (NIFTY/BANKNIFTY/
  FINNIFTY/SENSEX/NATURALGAS/CRUDEOIL per its cron env var default)

**Real, disclosed risk**: adding a symbol to one list (as done today for
hedging) does NOT propagate to the others. A new spec-wide "which
instruments does the system trade" concept (implied throughout the spec,
never made explicit as its own component) should be a SINGLE source of
truth these all read from, not a 6th list to maintain.
