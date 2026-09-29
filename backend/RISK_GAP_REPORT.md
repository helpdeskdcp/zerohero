# Risk Gap Report — zerohero / Chanakya AI

Phase 1 deliverable. Real gaps between what exists today and what the new
"Autonomous Hedging MCP v3.0" spec requires, mapped against the spec's own
section numbers. Ordered by severity, not by spec section order. Every
item is either a bug found and fixed this session, or a genuinely absent
capability -- none of this is speculative.

## SEVERITY: HIGH — found and fixed TODAY, during this same audit

**1. Position-store lock scope gap (spec §30 Position Reconciliation, §49
Runtime Isolation).** `app.hedging.runner.scan_and_record()` got a
non-blocking file lock earlier today after a race produced a real
duplicate PAPER position. That lock was placed on the WRONG function: the
actual cron entry point (`scripts/hedging_scan.py`) calls `scan()`
directly and never went through `scan_and_record()` at all, leaving the
cron path completely unprotected. A subsequent race (a cron-triggered
scan overlapping a manually-triggered one, most likely during this
session's own service restart) **lost an entire position record** (not
closed, not archived -- the JSON-blob-in-settings store's
read-modify-write silently dropped it) and left the capital ledger
double-locking margin for a position that no longer existed
(`allocated_margin` was found to be exactly 2x the real open position's
`margin_locked`). Both bugs are now fixed (lock moved into `scan()`
itself, verified with a concurrent-thread test that both the cron-style
direct call and the API-style call now serialize correctly; capital
ledger manually recomputed from the real position-store state). **This is
exactly the failure mode spec §30/§49 exist to prevent, and it happened in
production (paper) today** -- strong evidence those sections are not
optional hardening for a future phase, they are needed from day one of
any component that mutates a shared position/capital store.

**2. No real broker margin number anywhere (spec §22/§23 Margin Engine +
Margin Shock Protection).** `app.hedging.risk.credit_spread_economics()`'s
`max_loss_per_lot` is a strike-distance estimate the module computes
itself; there is no broker margin-calculator API call in this entire
repo. The spec's own §22 states this exactly: "Estimated margin is NOT
authoritative. Actual broker margin must override estimate whenever
available." Today's fix only addressed *capital sizing against the
estimate* (raising PAPER capital so the estimate-based sizing could
actually place a paper lot) -- it did NOT add a real margin check. §23's
margin-shock-protection has no possible implementation yet without this.

**3. MCX option strike-scaling bug in the shared instrument resolver
(no direct spec section, but affects §5 Option Normalization broadly).**
`app.instruments._master_meta()` used a conditional strike-scaling
heuristic (`/100 if raw>100000`) that silently produced wrong strikes for
any MCX/commodity-scale contract (verified live: a real NATURALGAS "...140CE"
contract has `raw_strike=14000`, needing `/100`, but the old condition
left it un-divided since 14000 is not `>100000`). This was LATENT (no
prior production code called an MCX options resolver by exact strike, so
it never manifested) until today's live-quote work exposed it. Fixed to
unconditional `/100`, verified against real NFO/MCX/BFO master rows. **If
the new Structure Generator (§11) or Strike Optimizer (§13) ever resolves
an MCX options contract by numeric strike through this same code path,
this would have silently corrupted every MCX candidate's strike.**

## SEVERITY: HIGH — pre-existing, not yet fixed

**4. Calibration overconfidence, K8-class, only partially remediated (spec
§35 Model Drift, §36 Anti-Overfitting).** The K8 fix (2026-09-21) corrected
the fitting logic, and today's re-validation confirmed it IS behaving
correctly on new data. But the aggregate `calibration_report()` diagnostic
still reads as "OVERCONFIDENT" because most resolved history predates the
fix -- there is no mechanism today that would let an operator (or an
automated drift monitor, spec §35) distinguish "the model is still broken"
from "the model is fixed but old data dominates the aggregate" without a
manual chronological split, which is exactly the kind of manual
investigation §35's automated drift monitoring is meant to replace.
`calib_version` was ALSO found silently un-populated on every live signal
(a separate bug, fixed today) -- meaning even the data needed to do this
split automatically didn't exist until today.

**5. Every backtested strategy family in this repo has failed to show a
stable OOS edge (spec §36 Anti-Overfitting, §59 Success Criteria).** Not
a bug -- a real, load-bearing fact for the new spec's Structure Generator
(§11) and Decision Engine (§41): SSL Hybrid, StochRSI+Supertrend,
Regime-Adaptive Trend+Momentum, Tri-compare, Trend-swing, Inside-Bar,
H1/H7-on-option-premium, NIFTY weekly Iron Condor, Liquidity Sweep, and
the orderflow_system framework's 8 versions (v1-v8) were ALL independently
tested on real data and rejected or left INSUFFICIENT_SAMPLE. The new
spec's §59 ("System is successful only when: positive out-of-sample
expectancy + ... stable across regimes + stable in walk-forward") sets
exactly the bar every one of these failed to clear. A new Structure
Generator producing 16+ options-strategy candidates does not start from
zero risk here -- it inherits a repo-wide, empirically-earned skepticism
that retail-accessible technical/order-flow signals have a real edge on
these instruments at these timeframes. This should directly inform §37's
strategy x regime matrix: expect most cells to be negative or
insufficient, because that has been the actual, repeated finding.

**6. No portfolio-level risk aggregation (spec §31).** Every engine
(AutoScalp, Hedging) tracks its own capital/position count independently.
`hedging.capital`'s `max_concurrent_positions`/`max_positions_per_symbol`
are simple counters, not a delta/gamma/vega portfolio aggregate. If
AutoScalp and Hedging were ever both live simultaneously on the same
underlying, nothing today would see their COMBINED directional exposure.

**7. No kill switch (spec §48).** No independent, system-wide
STOP_NEW_TRADES mechanism exists. `hedging.runner.disarm()` stops ONE
engine's new entries; nothing stops all engines at once, and nothing
triggers automatically on max-daily-loss/max-drawdown/broker-mismatch.

**8. No emergency-unwind-as-a-module (spec §29).**
`hedging.position.emergency_exit_all()` exists but is scoped to hedging's
own positions only, requires the caller to already have current prices in
hand, and has no leg-failure/partial-fill/broker-disconnect handling
(there is no live order path in hedging at all, so "partial fill" isn't
currently a real event that can happen to it -- but would need to be
built alongside any future live order integration, not bolted on after).

**9. No position reconciliation against a broker (spec §30), beyond the
position-store-internal-consistency bug in item 1.** `app.execution/`
(the separate "Chanakya Order Adapter") has a `Reconciler` component per
prior-session memory, but it is for the LIVE (triple-gated-off) order
path, not for hedging's PAPER positions, and has not been re-verified
this session.

## SEVERITY: MEDIUM — structural, disclosed, not urgent

**10. Data freshness ceiling (spec §17 Intraday Scenario Engine implicitly
assumes near-real-time data).** Today's hedging live-quote fix improves
freshness from ~5min-stale to seconds-fresh ON DEMAND (poll-at-check-time),
but this is NOT a standing tick-level WS subscription. `app.optionchain`'s
default `allow_network=False` convention across nearly every OTHER
read-only engine in this repo means most of the system still reads
5-min-stale captured data by design. A spec section assuming continuous
live Greeks/IV/OI (§3, §6, §8) would need this addressed repo-wide, not
just in hedging.

**11. No historical tick/L2 data anywhere (affects spec §43 Backtest
Engine, §45 Monte Carlo).** Confirmed repeatedly across many research
threads this session (Dhan quote-rule orderflow, liquidity_sweep,
orderflow_system): no historical bid/ask or L2 depth data exists in this
repo or from any connected source. `app.backtest.oi_history_adapter`'s
own manifest discloses "candles are synthesised from
cycles.underlying_ltp / strikes.*_ltp" -- not real OHLC ticks. Any new
Backtest Engine (§43) inherits this ceiling; it cannot be fixed by better
code, only by a new, currently-absent data source.

**12. Symbol/exchange metadata scattered across 6+ independent lists (see
DUPLICATE_LOGIC.md item 7).** Not a correctness bug today, but a
real risk that adding an instrument to the new spec's universe (§11
Structure Generator implies "any of these instruments") silently misses
one of the existing lists, producing an inconsistency similar to what
almost happened with hedging's MCX addition today (three separate places
needed updating: `DEFAULT_CONFIG`, `_OPTION_RESOLVER`/`_SYMBOL_EXCHANGE`,
and the live config patch).

**13. Regime classification is 4-ways inconsistent (see DUPLICATE_LOGIC.md
item 3).** The new spec's Market Regime Engine (§7) needs to explicitly
decide its relationship to the 4 existing, differently-labeled
classifiers before adding a 5th.

## What is NOT a gap (verified working, do not rebuild)

- Real futures/options cost models exist and are independently verified
  against live broker rate cards (`app.institutional_edge.costs`,
  `data/research/orderflow_system/engine/costs.py`) -- spec §19's Cost
  Engine requirement is substantially already met for the instruments
  those cover (NIFTY/BANKNIFTY options, NatGas/CrudeOil futures & options).
- No-look-ahead discipline is real and tested (`orderflow_system`'s
  `test_no_lookahead.py` mutates future bars and asserts past decisions
  are unchanged, on real data) -- spec §36's "no future data, no leakage"
  requirement has a working, reusable pattern already in this repo.
- PAPER-only default, LIVE triple-gated off, is already the actual state
  of both AutoScalp and Hedging (spec §46/§47) -- not aspirational, verified
  live today (`"live_trading": false, "paper_mode": true"`).
