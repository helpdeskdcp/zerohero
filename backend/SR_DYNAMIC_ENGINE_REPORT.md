# Dynamic Quantitative Support/Resistance Engine — Build & Validation Report

## Architecture (additive, existing engine untouched)

New package `app/sr_dynamic/`, built incrementally with tests at each stage.
Reuses `app.engines.sr_engine`'s private helpers (`_bars`, `_swings`, `_cluster`, `_atr`)
directly — the same cross-module reuse pattern already used elsewhere in
this codebase (e.g. `app.engines.state_classifier` importing `_swings`).
`app/engines/sr_engine.py` was not modified; `compute_sr()`/`classify()`
continue to serve production unchanged.

| File | Pipeline stage | Reuses |
|---|---|---|
| `pivots.py` | confirmed swing high/low | `sr_engine._swings` (already anti-repaint-safe) |
| `clustering.py` | price-distance clustering into zones | `sr_engine._cluster` |
| `touches.py` | touch count, rejection count, rejection **strength**, volume ratio | new (extends `sr_engine._touches`'s convention) |
| `breakout.py` | confirmed breakout (close-only) + retest (successful/failed) + flip | new, pure causal state machine |
| `mtf_confirm.py` | cross-timeframe confirmation | new |
| `scoring.py` | weighted 0-100 strength (never called a probability) | new, same renormalize-over-available-data pattern as `app.strategy.scoring` / `app.strategy_mtf.mtf_aggregator` |
| `engine.py` | orchestrator, exact output contract | composes all of the above |

## Tests: 39 new, all passing; existing suite untouched

- `test_pivots.py` (5): confirmed-swing detection, and the defining
  no-lookahead property — a pivot within `right` bars of the array's end is
  correctly withheld, not fabricated; growing history never repaints an
  already-confirmed swing.
- `test_clustering.py` (5): merge/split behavior, sorted output.
- `test_touches.py` (7): touch/rejection counting, rejection magnitude,
  volume-ratio confirmation, graceful degradation with no real volume data
  (returns `None`, never fabricates a ratio).
- `test_breakout.py` (8): confirmed breakout, successful retest → flip
  (both directions), failed breakout (immediate reversal and post-retest
  reversal), and a growing-history anti-repaint test (same methodology as
  `app.strategy_mtf.htf_resample`'s mutation tests).
- `test_mtf_confirm.py` (4): cross-timeframe confirmation logic.
- `test_scoring.py` (6): weights sum to 1.0, renormalization when
  components are missing (never fabricated), monotonicity checks.
- `test_engine.py` (5): end-to-end schema match, empty/insufficient-data
  degradation, MTF wiring, and a full-pipeline anti-repaint check.

Existing `tests/test_sr_engine.py`: 28/28 still passing, unchanged.
**Full backend suite: 1313 passed** (was 1274 before this work — the +39
delta is exactly this module's new tests; nothing else changed).

## Historical validation (real data, no synthetic bars)

**NATGAS**: real MCX futures 5m bars from this session's earlier backtest
(3.75 months, 12,854 bars). **NIFTY**: real Kaggle 5m index bars (1 year,
25,463 bars) — a genuine data-quality issue was found and fixed *in the
validation script* during this work: the Kaggle dataset's tail is 25
flat-lined placeholder bars (H==L==C repeated, a known data-refresh
artifact of that dataset, not a bug in the new engine — the engine
correctly computed ATR=0 and correctly returned zero zones on that dead
data). Trimmed to the last bar with real movement (2026-05-18) before
scoring.

Methodology: because the live engine's swing detector intentionally only
looks at the most recent 80 bars (correct for live use), a single call on
a multi-month array only sees the tail. The validation **rolls the
discovery step forward through history** (every 200/70/35 bars for
5m/15m/30m) to build the full structural inventory, matching how a live
system would actually discover levels over time, then scores each
discovered level once against its complete subsequent history.

| Metric | NATGAS 5m | NATGAS 15m | NATGAS 30m | NIFTY 5m | NIFTY 15m | NIFTY 30m |
|---|---|---|---|---|---|---|
| SR levels found | 114 | 84 | 53 | 133 | 82 | 48 |
| Successful retest % | 32.4% | 41.1% | 37.0% | 40.9% | 39.6% | 41.0% |
| False breakout % | 65.5% | 55.7% | 59.1% | 57.6% | 58.3% | 56.1% |
| S/R reaction rate (rejections/touches) | 79.0% | 71.5% | 80.8% | 84.6% | 84.9% | 80.0% |
| Avg touches/zone | 147.9 | 89.8 | 59.1 | 247.3 | 137.1 | 110.6 |

**Reading these numbers honestly**: false-breakout rate (~56-66%) is
higher than successful-retest rate (~32-41%) across both instruments and
all three timeframes — consistent (not identical) across two very
different instruments, which is a real, if modest, cross-instrument
consistency check. The remainder (~2-4%) are breakouts still unresolved
(neither confirmed successful nor failed) as of the last real bar —
reported explicitly so the two percentages are never mistaken for summing
to 100%. Support/resistance reaction rate is high (~71-85%) in both cases,
meaning most touches DO produce a real rejection — the zones themselves
are structurally meaningful, even though a plurality of confirmed
breakouts ultimately fail to hold on retest.

**Live single-call snapshot** (what a fresh boot sees right now, for
comparison against the rolled historical inventory): NATGAS 17 zones,
7 confirmed on 2+ timeframes; NIFTY 13 zones, 8 confirmed on 2+ timeframes
— real multi-timeframe agreement is visible in live-mode, not just a
theoretical field.

## Comparison with the existing SR Engine

The existing `compute_sr()` is a stateless, single-snapshot scorer with no
breakout/retest/flip concept — this is a structural comparison, not a
like-for-like backtest:

| | NATGAS | NIFTY |
|---|---|---|
| Existing engine: avg zones/snapshot | 18.85 | 17.6-18.0 |
| Existing engine: avg zone strength | 37.3 | 49.0-49.2 |
| New engine: live-mode zones | 17 | 13 |
| New engine: live-mode avg strength | 65.2 | 66.6 |

The new engine finds fewer, more evidence-backed zones (it only surfaces a
level once a confirmed swing cluster exists and has real touch/breakout
history) and scores them meaningfully higher on average — expected, since
its strength formula rewards accumulated real evidence (swing count,
touches, rejections, volume, retests) that the existing engine's
single-snapshot confluence score doesn't track over time.

## What this validates, and what it doesn't

This confirms the pipeline runs correctly end-to-end on real data across
two different instruments and three timeframes, and that its internal
consistency checks (reaction rate, retest/false-breakout split) look
structurally sane. **It does not establish a trading edge** — no entry/exit
threshold was changed, and per instruction this was not tuned against a
single live trade or a single dataset. The false-breakout rate exceeding
the successful-retest rate is a real, useful diagnostic if this engine is
ever wired into a trading decision (a naive "trade every breakout" rule
would lose more often than it wins) — but that is a decision for a future,
separate piece of work, not something addressed here.

## Status

Not committed. Reusable artifacts: `app/sr_dynamic/` (7 modules),
`tests/sr_dynamic/` (39 tests), `scripts/sr_dynamic_validation.py`,
`data/research/sr_dynamic/validation_report.json`.
