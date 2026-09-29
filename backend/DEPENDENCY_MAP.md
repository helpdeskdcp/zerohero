# Dependency Map — zerohero / Chanakya AI

Phase 1 deliverable. Who calls whom, for the components most relevant to
the new autonomous-hedging spec. Not exhaustive of the whole repo (see
ARCHITECTURE_AUDIT.md's "not yet audited" list) -- exhaustive for the
trading/risk/execution path specifically, since that's what the new spec
extends.

## Shared foundation layer (everything below depends on these)

```
app/db.py                    <- every engine's settings/signals persistence
app/instruments.py           <- instrument-master resolution (NEW today:
                                 resolve_nse_option / resolve_mcx_option /
                                 resolve_bse_option, all via _resolve_option())
app/connectors/angelone.py   <- REST/WS broker client (login, fetch_market_quote,
                                 fetch_mcx_quote, order-adjacent reads)
app/optionchain/resolve.py   <- get_chain(): 3-tier chain source failover
app/backtest/calibration.py  <- shared probability-calibration fit/predict
app/market_calendar.py       <- session calendar
app/telegram_dispatcher.py   <- canonical outbound notification path
```

## AutoScalp (the primary live PAPER signal)

```
app/autoscalp/runner.py (AutoScalpRunner)
  -> app.engines.scalp_strategy.decide_from_context()
       -> app.sr_dynamic.{live_state, signal_confirm}
       -> app.engines.{option_engine, regime_mtf, sr_engine, state_classifier}
       -> app.backtest.calibration.predict()   (via _score_to_prob, now the
                                                 ONLY implementation -- see
                                                 DUPLICATE_LOGIC.md)
  -> app.db.insert_scalp_signal()               (calib_version fix today)
  -> app._maybe_recalibrate()
       -> app.backtest.calibration.fit()
       -> app.db.set_setting("autoscalp_calibration")
  -> app.hcs.calibrate / app.hcs.adaptive[_mc]   (reads the SAME resolved rows)
  -> app.structural_break.performance_monitor    (reads the SAME resolved rows)
  -> app.sl_hit_analysis.engine                  (reads the SAME resolved rows)
```

Consequence: a fix to AutoScalp's underlying `scalp_signals` data quality
(e.g. today's `calib_version` fix, or the `_DEFAULT_FILTERS` update)
automatically improves HCS/structural_break/sl_hit_analysis's own
diagnostics too, since they're all reading the same table, not
independent models. A new spec component that ALSO reads `scalp_signals`
inherits this same property -- do not build a parallel signal table
without a clear reason.

## Hedging (the only component that autonomously opens PAPER positions)

```
scripts/hedging_scan_cron.sh (flock, cron every 5min)
  -> scripts/hedging_scan.py
       -> app.hedging.runner.scan()             <-- THE locked entry point
                                                     (fixed today: was only
                                                     on scan_and_record(),
                                                     which this script never
                                                     called)
POST /api/hedging/scan-now (API, async background task)
  -> app.hedging.runner.scan_and_record()
       -> app.hedging.runner.scan()             <-- same lock, same function

app.hedging.runner.scan()
  -> check_exits()
       -> _current_quotes()
            -> _live_leg_premium()               (NEW today)
                 -> app.connectors.angelone.fetch_market_quote()
            -> app.optionchain.resolve.get_chain()   (fallback path)
       -> app.hedging.position.check_exit()
       -> app.hedging.position.close_position()
            -> app.hedging.capital.release()
  -> evaluate_symbol() (per configured symbol, only if armed)
       -> app.optionchain.resolve.get_chain()
       -> app.hedging.primary_selector.select_primary()
       -> app.hedging.selector.select_hedge()
            -> app.hedging.risk.{net_delta_exposure, credit_spread_economics,
                                 risk_reduction_pct}
       -> app.hedging.runner._leg_token()          (NEW today)
            -> app.instruments.{resolve_nse_option, resolve_mcx_option,
                                resolve_bse_option}
       -> app.hedging.position.open_position()
            -> app.hedging.capital.allocate()
       -> app.telegram_dispatcher (via runner._notify)
```

**Single point of failure, disclosed**: `app.hedging.position.load_all()`/
`_save_all()` read-modify-write ONE JSON blob (`db.get_setting`/
`set_setting("hedging_open_positions")`) with no optimistic-concurrency
check. `scan()`'s new lock (today) prevents two SCANS from racing each
other, but does NOT prevent a scan from racing a manual, direct call to
`position.open_position()`/`close_position()` from anywhere else (there
is currently no such caller, but nothing stops one being added later
without also taking the lock). A future spec component (e.g. Position
Reconciliation, section 30) that writes to this same store must either
go through `scan()`'s lock or get its own.

## Option Chain (read-only, consumed by almost everything above)

```
app.optionchain.api.api_optionchain()
  -> resolve AUTO/NEXT/LATEST/CURRENT via app.instruments (NEW, fixed today
     for the "next expiry" bug -- see prior session commit 594b142)
  -> app.optionchain.resolve.get_chain()
       -> app.optionchain.sources.angelone_chain.fetch()   (captured DB)
       -> app.optionchain.sources.upstox_open.fetch()      (live, keyless)
       -> app.optionchain.sources.nse_v3.fetch()           (live, keyless)
  -> app.optionchain.analytics.compute_all()
  -> app.optionchain.structure.analyze()
  -> app.optionchain.qualify.qualify()
```

Consumed by: `app.hedging.runner` (evaluate_symbol, _current_quotes),
`app.engines.scalp_strategy` indirectly via S/R, `app.signals_dashboard`,
the frontend's optionchain view, `app.smart_index_scalper`.

## Institutional Edge / Structural Break / Microstructure (read-only overlays)

```
app.institutional_edge.evaluator
  -> app.institutional_edge.{conditions, costs, ev, edge_score}
  -> reads its OWN store (app.institutional_edge.store) -- currently empty
     (tracked_edges() == []), not wired to any live signal generator today

app.structural_break.evaluator
  -> app.structural_break.{drift, performance_monitor, prediction_drift}
       -> reads AutoScalp's scalp_signals (see above)
  -> app.structural_break.{adaptation, shadow, audit_log}

app.microstructure
  -> reads app.orderflow.h1h7_state's classifier output directly
     (explicitly "not a new detector" per its own docstring)
```

## Research-only, NOT production-wired (do not assume these feed anything live)

```
data/research/orderflow_system/   -- standalone CLI (main.py backtest/
                                      walkforward), reads market_history.db
                                      directly, writes only to its own
                                      reports/ dir. Zero import path from
                                      any app/ production module.
app.liquidity_sweep                -- imported by tests and its own backtest
                                      script only; no cron, no API route
                                      found wired to a live decision path.
app.smart_index_scalper.replay     -- only reachable from its own test
                                      harness (no CLI, no API route).
```

## Cross-cutting: three separate SQLite databases

```
data/chanakya.db            <- app.db (settings, scalp_signals, ai_paper_trades,
                                smart_scalper_signals, order_events)
data/market_history.db      <- quote_snapshots, option_greeks (histcap capture)
data/oi_dashboard/
  oi_history.db             <- cycles, strikes (app.backtest.oi_history_adapter,
                                real data ONLY for 2026-07-13..2026-08-28)
```

No single module reads across all three; each subsystem picks one. A new
spec component needing "the full picture" (e.g. Feature Lineage, section 4)
must explicitly decide which of these three is authoritative for which
field, or risk silently mixing incompatible cadences/coverage.
