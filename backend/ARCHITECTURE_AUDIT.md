# Architecture Audit — zerohero / Chanakya AI

Phase 1 deliverable for the "Autonomous Hedging MCP v3.0" spec. This maps
what already exists in this repository against the spec's own component
list, so later phases build ON this, not a duplicate of it. Grounded in
real code inspection this session (not guessed) — every claim below was
either read directly from the file cited or verified via a live call
during this session's earlier work (cited by memory/finding name where
relevant).

**Naming collision, disclosed up front:** `app/mcp_server.py` already
exists and is called "MCP" in this codebase, but it means something
different (a Model-Context-Protocol tool server exposing read-only
Angel One data functions to an LLM client like Claude Desktop — "no
order-placement tool exists here"). The new spec's "MCP" means an
orchestration/control layer for an autonomous trading system. These are
NOT the same thing and must not be confused or merged.

## Market data

- `app/market_hub.py` — single read path for live index data (WS feed +
  histcap DB), replacing an earlier N-poller design that tripped AngelOne's
  rate limit (~85-90% fewer REST calls).
- `app/feed_registry.py`, `app/connectors/angel_ws.py` — live WS feed
  subscription/registry.
- `app/connectors/angelone.py` — REST client: login/TOTP, `fetch_market_quote`
  (any NSE/NFO/MCX/BFO instrument by token, live LTP+full depth),
  `fetch_mcx_quote`, position/order-adjacent read calls. Confirmed live
  today: real quote latency is seconds, not tick-level continuous (no
  standing WS subscription per option leg exists yet).
- `app/connectors/dhan_ws.py`, `dhan_parser.py`, `dhan_historical.py` —
  separate Dhan broker integration: live orderflow WS (quote-rule/tick-rule
  aggressor classification) + historical backfill. Only the tick-rule
  fallback has been backtested; quote-rule has no historical depth data to
  validate against (no historical L2/tick data exists anywhere in this repo
  or from any connected source).
- `app/histcap/` — the periodic (poll-based, not streaming) capture
  pipeline that snapshots option chains/spot into `market_history.db`.
  Real, measured cadence for NIFTY ~2.7min; confirmed today for MCX
  NATURALGAS options: ~4.5min observed staleness. This is the DEFAULT data
  source for nearly every read-only engine in this repo (`allow_network=False`
  by convention) -- deliberately NOT real-time.
- `app/market_calendar.py` — NSE/MCX session calendar.

## Option chain / Greeks / OI

- `app/optionchain/` — the read-only option-chain subsystem: `resolve.py`
  (`get_chain()`, 3-tier source failover: angelone_captured -> upstox_open
  -> nse_v3), `chain.py` (OptionChain/OptionLeg dataclasses -- NOTE:
  OptionLeg carries NO broker symboltoken, by design, since it's meant to
  be source-agnostic across 3 different providers), `analytics.py`
  (max-pain/PCR/GEX/skew), `structure.py` + `qualify.py` (QUALIFIED/WATCH/
  NO_TRADE gate), `sources/angelone_chain.py` (captured-DB resolver),
  `sources/upstox_open.py` + `sources/nse_v3.py` (keyless live fallbacks).
- Greeks: dense from AngelOne's own `option_greeks` capture for NSE
  indices; MCX/BSE options are quote-only (LTP/bid/ask/OI, **no broker
  Greeks at all** -- confirmed structurally, not a bug, per
  `_ASSET_KEY`/`SUPPORTED` sets in `sources/upstox_open.py` and
  `optionchain/api.py`'s own `_GROUPS` comment).
- `app/instruments.py` — the instrument-master resolver: `resolve_nse_option`,
  `resolve_mcx_option`, `resolve_bse_option` (added today, all 3 share
  `_resolve_option()`), `resolve_mcx_future`, `resolve_index_future`,
  `master_rows()`/`_master_meta()`. **Real bug found+fixed today**: strike
  scaling was conditionally `/100 if raw>100000`, silently wrong for
  MCX/commodity-scale strikes (raw stays under 100000 even after the real
  x100 encoding) -- now unconditional `/100`, verified against AngelOne's
  raw master data across NFO/MCX/BFO.
- `app/greeks_engine/` — exists, not yet audited in depth this session
  (flag for a deeper look in a later phase, not guessed at here).
- OI: `app/optionchain/analytics.py` (PCR/max-pain/OI-walls) +
  `histcap`'s captured `oi_change` (materialised at capture time since the
  broker itself sends none).

## Probability / calibration engine

- `app/backtest/calibration.py` — THE shared probability layer (closed-form
  logistic fit + shrinkage, `MODEL_VERSION="scalp-calibration-v2"`).
  Consumed by `app/hcs/`, `app/autoscalp/runner.py`, `app/engines/
  scalp_strategy.py`. **Real bug found+fixed 2026-09-21** (K8: a hard `k`
  floor forced every fitted slope >=1.0 regardless of data) and
  **independently re-validated live today** (post-fix predictions cluster
  near the honest base rate; pre-fix legacy predictions in the same DB
  still show the old inversion, which is expected, not a regression).
- `app/hcs/` — SHADOW meta-engine (quality bar + hard-veto layer on top of
  `decide_from_context`), its own `calibrate.py` reusing the same
  `calibration.fit()`. NOT VALIDATED per its own verdict (insufficient
  regime coverage), by design stays SHADOW.

## S/R, mathematics, volume, strategy engines

- `app/sr_dynamic/` — live S/R computation + confirmation gate.
- `app/mathematical_confluence/` — confluence/market-map engine
  (`engine.py`, `scoring.py`, `oi_confluence.py`). Its "confidence" field is
  a deterministic composite (`int(min(95, cscore*0.9))`), NOT a fitted
  probability -- confirmed today it has ZERO persisted outcome log of its
  own; its only downstream consumer with a resolvable-outcome table
  (`smart_index_scalper`'s `smart_scalper_signals`) has 0 rows in the live
  DB, meaning it isn't currently accumulating any real trade history at
  all.
- `app/orderflow/` — `h1h7_state.py` (categorical classifier, NOT a
  probability model; every state carries a hardcoded, backtest-derived
  `research_status` tag: SUPPORTED/NOT_VALIDATED/RESEARCH_ONLY_PROMISING/
  NO_ESTABLISHED_EDGE), `service.py` (spike-based smart-money signal
  generator, separate from h1h7).
- `data/research/orderflow_system/` — a SEPARATE, versioned research
  framework (not production-wired) built this session to test a manual
  4-setup order-flow rulebook end-to-end (v1-v8, all tested and REJECTED/
  NOT ADOPTED on real NIFTY data; see that directory's own CHANGELOG.md
  for the full, decisive history). This is research evidence about what
  DOESN'T work, valuable input for Phase 5-7 (do not re-derive these
  conclusions from scratch).
- `app/engines/scalp_strategy.py` — the live AutoScalp decision function
  (`decide_from_context`): S/R -> 4-state classify -> regime/MTF ->
  CE/PE leg selection + EV gate -> calibrated probability -> final
  decision. `_DEFAULT_FILTERS` (block_regimes/block_signal_types) updated
  today with a real, walk-forward-validated finding (TRENDING_UP/RANGE
  regime and SUPPORT_REVERSAL/RESISTANCE_REVERSAL signal type are net
  losers on real live outcomes).
- `app/institutional_edge/` — conditional-edge + real-cost EV layer.
  `costs.py`'s cost model is honestly 3-tiered (OK real-observed >
  FORMULA_ESTIMATE formula-not-observed > UNCALIBRATED) -- only NatGas/
  CrudeOil MCX costs are "OK" (validated against real AngelOne contract
  notes); NSE index options use FORMULA_ESTIMATE (a real regulatory-rate
  formula, never claimed as "OK" observed data). `tracked_edges()` is
  currently empty (no live data accumulating).
- `app/liquidity_sweep/` — index-first SMC strategy (sweep+CHoCH/BOS+
  CISD/FVG/OB). Frozen NOT READY: backtested on 2yr real NIFTY 5m with a
  clean look-ahead audit, but the underlying setup-scoring shows
  literally zero discriminative power (coin-flip accuracy). This is a
  feature-construction problem, not a calibration problem -- more data
  will not fix it.
- `app/smart_index_scalper/` — MATHEMATICAL_CONFLUENCE-based scalper
  (`paper_engine.py`, `replay.py`, `selection_score.py`). Has its own
  replay/backtest scaffolding (`replay_metrics.py`'s `_cal_verdict`/
  `INSUFFICIENT_SAMPLE` logic) but currently ZERO rows in its own signal
  table -- not accumulating live data right now, and has no CLI/API entry
  point for its replay (only reachable from its own test harness).
- `app/structural_break/` — drift+performance+prediction monitors feeding
  an explainable break-score state machine. Reuses AutoScalp's resolved
  rows directly (`from ..autoscalp.calibration_report import _reliability`),
  so it benefits automatically from any AutoScalp-side calibration fix.
  Currently reports NORMAL (no anomalous drift) for NIFTY.
- `app/microstructure/` — translates the existing h1h7_state classifier
  into BUYING_PRESSURE/SELLING_PRESSURE/ABSORPTION/REJECTION/etc labels.
  Explicitly NOT a new detector.
- `app/premium_decoupling/` — shadow-only premium-vs-spot decoupling scan,
  no order path.
- `app/sl_hit_analysis/` — stop-hit-rate diagnostic over the SAME resolved
  scalp_signals pool used elsewhere; purely descriptive, no new model.

## Risk / margin / hedging

- `app/hedging/` — the ONLY component in this repo that currently opens
  actual (PAPER) multi-leg positions autonomously. `runner.py` (scan/
  arm/disarm/scan_and_record, cron-driven every 5min via
  `scripts/hedging_scan_cron.sh` -> `scripts/hedging_scan.py`, ALSO
  reachable via `POST /api/hedging/scan-now`), `selector.py` (protective
  further-OTM leg selection: liquidity/spread/cost/risk-reduction gates --
  confirmed today these gates do NOT hard-require Greeks; `net_delta` is
  informational only, returns UNAVAILABLE rather than fabricating a value
  when Greeks are missing), `primary_selector.py`, `capital.py` (PAPER
  ledger, persisted via `db.get_setting`/`set_setting`), `position.py`
  (open/check_exit/close, persisted the same way), `risk.py`
  (`net_delta_exposure`, `credit_spread_economics`, `risk_reduction_pct`).
  **max_loss_per_lot is a self-computed strike-distance estimate, NOT a
  real broker SPAN+exposure margin number** -- confirmed today there is no
  broker margin-calculator API integration anywhere in this repo. This is
  the single biggest gap versus a broker-grade margin engine (spec
  sections 22/23).
  **Two real bugs found+fixed today**: (1) default PAPER capital
  (Rs50,000) was structurally too small to ever size 1 lot at the 2% risk
  cap for a real hedge structure (needs ~Rs27 lakh+); every scan since the
  engine was armed had been silently hitting a sizing-rejected NO_TRADE.
  (2) `scan_and_record()` had no lock, so an API-triggered scan and a
  near-simultaneous second trigger raced past the "already has an open
  position for this symbol?" check and each independently opened a real
  position -- fixed with a non-blocking file lock (needed as a file lock,
  not `threading.Lock`, since this function is also invoked from a
  separate OS process via cron).
  **Live-quote wiring added today**: `_current_quotes()` now tries a
  genuinely live LTP (via `fetch_market_quote`, using a `symboltoken`
  resolved once per leg at open time and persisted on `HedgePosition`)
  before falling back to the ~5-min-stale captured chain. Not yet a
  standing WS subscription (spec's own "real-time" bar) -- this is
  poll-on-demand-at-check-time, a meaningfully smaller latency
  improvement (seconds vs minutes), not full tick-level streaming.
  Validated end-to-end live today for NATURALGAS.
- `app/execution/` — "Chanakya Order Adapter": `BrokerBase`/`OrderManager`/
  `Reconciler`/`TradeMonitor` for a LIVE order lifecycle. **LIVE is
  triple-gated OFF**; PAPER is the default. Per prior-session memory, this
  exists as real infrastructure but has not been exercised against a real
  broker order in production. Needs re-verification in a later phase
  before any spec section referencing live execution assumes it's ready.
- No portfolio-level risk engine exists yet (spec section 31) -- each
  engine/position is currently evaluated independently
  (`hedging/capital.py`'s `max_concurrent_positions`/
  `max_positions_per_symbol` are the closest thing, and those are simple
  counters, not a delta/gamma/vega portfolio aggregate).
- No kill switch (spec section 48), emergency-unwind-engine-as-a-module
  (hedging has `emergency_exit_all` for ITS OWN positions only, not a
  system-wide kill switch), or margin-shock-protection component exists
  yet.

## Broker adapters

- AngelOne: `app/connectors/angelone.py` (REST + TOTP + SDK), `angel_ws.py`
  (live WS). This is the PRIMARY, most-used connector across the repo.
- Dhan: `app/connectors/dhan_ws.py`/`dhan_parser.py`/`dhan_historical.py`/
  `dhan_client.py`. SECONDARY, used mainly for its own orderflow research
  arc; live order path not evaluated here.
- Upstox: `app/connectors/upstox.py` -- read-only analytics connector +
  expired-options importer, no order path.

## Scheduler / execution isolation

- Cron (`crontab -l`), NOT a dedicated in-app scheduler/worker process --
  every "autonomous" engine (autoscalp, hedging, option-chain watch,
  orderflow watch) runs as a `*/5 8-15 * * 1-5` cron-triggered script, or
  is triggered manually via an API route. **Real, session-defining bug
  fixed earlier today**: none of these had a lock against overlapping
  invocations -- a host CPU-starvation event caused 71 stuck cron
  processes to pile up across 3 scripts. Fixed with `flock` at both the
  shell-wrapper level (5 scripts) and, for hedging specifically, inside
  the Python function itself (since it's also API-triggered from within
  the same running server process, which a shell-level flock can't see).
  There is currently no unified "scheduler" abstraction (spec section 49)
  -- each engine's cadence is independently hardcoded in its own crontab
  line.

## Database

- `app/db.py` -- the single shared SQLite database (`chanakya.db`)
  backing `scalp_signals`, `ai_paper_trades`, `smart_scalper_signals`,
  `order_events`, generic key/value `settings` (used by autoscalp config,
  hedging config/capital/positions, HCS, etc.).
- `data/market_history.db` -- SEPARATE db for `quote_snapshots`/
  `option_greeks` (histcap capture), ~8GB+.
- `data/oi_dashboard/oi_history.db` (referenced by `app.backtest.
  oi_history_adapter`) -- a THIRD, older archive DB (`cycles`/`strikes`
  tables) with real captured data from 2026-07-13..2026-08-28 only, used
  by `app/backtest/runner.py`'s `run_backtest`/`run_ablation`. Disclosed
  known limitations (from that module's own manifest): candles are
  synthesised from LTP (not real OHLC ticks), ~40% Greeks NULL, ~90%
  tokens/expiry NULL, poll cadence changed mid-archive (7-11s then 45s).
  **Three separate SQLite databases exist for overlapping purposes** --
  this is a real fragmentation the DEPENDENCY_MAP/DUPLICATE_LOGIC reports
  flag explicitly; do not add a fourth without first checking whether one
  of these three already covers the need.

## Telegram / dashboard

- `app/telegram_dispatcher.py` -- the canonical dispatcher (consolidated
  from 5 separate broadcasters in a prior session).
- `frontend/` (single-page app, `static/js/app.js`, no build step) served
  by `app/main.py`'s FastAPI route -- covers signals-hub, overview,
  signals, trades, monitor, scalp, research, system, autoscalp,
  mathscalp, orderflow, optionchain, hedging, runner views.
- Auth: a single ASGI middleware (`_AuthGateMiddleware` in `app/main.py`)
  gates EVERY path except `/api/health` and Dhan's own exempt callback
  paths, accepting either HTTP Basic (env-configured admin credential) or
  a Bearer token (`CHANAKYA_API_TOKEN`, currently unset -- only Basic auth
  is active). The frontend's own JS only ever sends Bearer; it works
  anyway because a browser that authenticates via the native Basic-Auth
  popup on `/` auto-attaches that credential to every subsequent
  same-origin fetch. Confirmed this is NOT a bug, just an unconventional
  (browser-native-popup-based) login flow rather than a dedicated login
  page.

## Backtest / research

- `app/backtest/` -- `runner.py` (`run_backtest`/`run_ablation`, real
  event-driven replay against `oi_history_adapter`'s archive),
  `replay.py` (`ReplayHarness`, real per-bar decide_from_context loop),
  `calibration.py` (see Probability section above).
- `data/research/orderflow_system/` -- see Strategy engines section
  above; this session's largest single research artifact, fully
  version-controlled with its own CHANGELOG discipline.
- Dozens of prior NO-GO research memories (SSL Hybrid, StochRSI+
  Supertrend, Regime-Adaptive, Tri-compare, Trend-swing, Inside-Bar,
  H1/H7-on-premium, NIFTY weekly IC, Liquidity Sweep, orderflow_system
  v1-v8) -- collectively, this is the single most important piece of
  context for spec sections 36/37/59: this codebase has already tested
  many independent strategy families on real data and found no stable,
  OOS-surviving retail edge on NSE/MCX index derivatives at intraday
  timeframes. A new autonomous system built on top of this repo inherits
  that reality; it does not get a fresh start just because the
  orchestration layer around it is new.

## Logging / observability

- Standard `logging` module throughout, no centralized structured-log
  sink or correlation-ID scheme found. Per-engine log files under
  `data/*.log` for cron-triggered scripts.
- No existing "decision JSON" (spec section 51) or audit-trail schema
  (spec section 52) -- `scalp_signals`/`hedging_open_positions` rows are
  the closest existing analogue, and they don't carry the full
  reason-code/rejected-candidate trail the spec wants.

## Existing paper trading

- AutoScalp (`app/autoscalp/`) -- PAPER by default, `LIVE_TRADING=false`,
  real resolved-outcome history (195+ rows) used throughout this audit.
- Hedging (`app/hedging/`) -- PAPER only, `paper_mode: true` in its own
  status endpoint, no live order path exists in this module at all.
- `app/execution/` -- has LIVE order-lifecycle code but triple-gated OFF;
  PAPER default.

## What this audit does NOT cover (disclosed gap, for a later phase)

`app/ai/`, `app/greeks_engine/`, `app/expiry_zero_to_hero/`,
`app/index_signal_research/`, `app/research_engines/`,
`app/research_strategy/`, `app/reverse_engineering/`, `app/signal_gate/`,
`app/strategy/`, `app/strategy_mtf/`, `app/l2capture/`,
`app/behavior_engine.py`, `app/combos.py`, `app/diagnostics.py`,
`app/effective_profile.py`, `app/orchestrator.py`, `app/pipeline_core.py`,
`app/regime_profiles.py`, `app/research.py`, `app/reversal.py`,
`app/runtime.py`, `app/scalper.py`, `app/scalp_pipeline.py`,
`app/tp_calibration.py` were not individually inspected for this pass --
listed in DEPENDENCY_MAP.md as "not yet audited," not silently assumed
empty or irrelevant.
