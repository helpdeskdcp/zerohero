# ZeroHero Arena — unification audit (Phases 1-4 + baseline), 2026-10-30

Status: **PARTIALLY COMPLETE.** Discovery, baseline, duplication analysis and target design are done with measured evidence. Nothing was consolidated in production, on purpose (reasons in section 7). No restart was made for this mission, no order was placed, no data was deleted.

## 0. URGENT — disk (data integrity outranks everything else)
`/` is **96 % full, 3.9 GB free** (91 G used of 99 G). `backend/data` is 39 G: `market_history.db` 21 G, `historical` 8.6 G, `l2_capture.db` 3.7 G, `chanakya.db` 2.6 G, `platform_ticks.db` 2.4 G. After the 24-Oct clean-up the disk was at 83 %.
Measured growth of `quote_snapshots`: 27.0 M rows, ~0.8 KB/row (21 G); the last 3 M rows were written in ~16 hours of session time (~2-4 M rows/day, ~2-3 GB/day). In the last 60 000 rows 81 % were `DHAN_CHAIN`, 5 % `ANGELONE_WS_SNAP` (the tick tape), 5 % `ANGELONE_QUOTE_FULL`. SQLite free pages: 1 005 (4 MB) — deleting old rows frees almost nothing until the DB is VACUUMed (needs free space = DB size) or the freed pages are reused by new inserts.
At this rate the disk is full in **about 1.5 days**. A full disk stops DB writes and the append-only Arena ledger (`human_signals.jsonl`) — i.e. an integrity incident. Decision needed from the owner (not taken here: it deletes data): archive+delete `quote_snapshots` older than N days (the nightly `data_retention.py` already archives to Drive before deleting; today's setting is 60 days and nothing is old enough), reduce `DHAN_CHAIN` capture frequency/strikes, move `data/historical` (8.6 G) to Drive, trim journald (719 M) / caches (516 M).

## 1. Inventory (Phase 1)
* 612 Python files, 84 932 lines under `app/`, 262 route decorators, 28 cron entries for this repo, ~4 330 tests.
* Live runners started by `app/main.py:_lifespan`: platform_live, chartink tracker, zone_breakout, winners_scalper (+ arena pine_submitter), arena safe_ai, `runtime.scalp_runner` (legacy), `runtime.autoscalp`, histcap, priority capture, arena tick_tape, l2capture, smart_scalper_scheduler, dhan_orderflow worker+engine, hybrid_orderflow shadow. Plus `arena-tg-reader.service` (Telegram) and ~20 cron jobs.
* Importer counts (static, regex) separate **core** (connectors 39 importers, engines 32, histcap 24, autoscalp 14, orderflow 13, backtest/strategy 11) from **research/dark** packages (0-1 importers: reverse_engineering, strategy_mtf, of_candidate, option_selling, smart_uturn, nse_official, research_strategy, smart_chart (1), nifty_direction (1), smart_index_scalper (1) ...).

## 2. Baseline (Phase 2), measured 2026-10-30 ~07:50 IST, pre-market
* Host: 7.9 GB RAM (3.1 GB used, 4.8 GB available), load 0.5-1.5. `uvicorn` 620 MB RSS.
* `/api/health` 17-30 ms; static `arena.html` 3 ms. (Authenticated Arena endpoints and market-hours latencies are NOT measured yet — a market-open check is scheduled for 09:23.)
* Tests: full suite 4 332 passed (one known flaky test deselected), 15 min.
* Angel session: one shared session now (commit bf23d20); a new process reuses it in 0.000 s.

## 3. Duplication analysis (Phase 3) — verified, not assumed
**Paper-trading engines (>= 10, each with its own state):** `arena` (event-sourced ledger + bid/ask settlement — the contest ledger), `autoscalp` (`ai_paper_trades`), `scalper.py`/`scalp_runner` (`scalp_signals`, legacy), `platform/paper/engine.py`, `smart_index_scalper/paper_engine.py` (tables empty: 0 rows), `smart_chart/paper.py`, `nifty_direction/paper.py`, `execution/paper_broker.py` + `shadow_broker.py`, `engines/paper_trading.py`, `zone_breakout/live.py`, `hybrid_orderflow/trade_engine.py`, `hedging/capital.py`. DB tables: `ai_paper_trades` 1 647 rows, `paper_trade_events` 2 892, plus `*_dupe_archive` tables (278 / 146 rows) from an earlier dedupe.
**Indicator copies:** ATR 14 definitions in 14 files, EMA 10, SMA 8, VWAP 6, RSI 5, swing 10. `scripts/indicator_parity_audit.py` (read-only) ran every ATR/EMA copy on the same 400-bar series against a TradingView-style reference: **8 of 10 ATR copies and 9 of 9 EMA copies are numerically identical (difference 0.0)**; 2 ATR copies (`reverse_engineering.regime.atr`, `research_engines.order_pressure.formulas.atr`) differ by 3.79 % (a different smoothing — a deliberate variant or a bug, to be decided per use); `scanner.dsl._atr` takes pandas Series (harness input mismatch, not a defect). => A single canonical indicator module is safe for the identical group.
**Cost models:** Arena `settle.cost_total_rs` and `institutional_edge.costs.option_round_trip_cost_rs` agree to the paisa on 5 option cases and the equity case (e.g. NIFTY 100->120 x75 = Rs 67.86 both). Other charge models exist in `strategy_lab/costs.py`, `hybrid_orderflow/trade_engine.charges_inr`, `research_engines/hybrid_mp/option_model.py`, `smart_uturn/option_replay.py`, `option_selling/backtest.py` (research; not compared yet).
**Signal generators:** each strategy package produces its own signal path (winners_scalper, arena safe_ai, autoscalp, smart_index_scalper, zone_breakout, liquidity_sweep, smart_uturn, hybrid_orderflow, nifty_direction, scanner, chartink ...). Several are research-only with NO-GO verdicts in the project memory (no edge after costs) — consolidating them into the live path would add risk, not value.
**Frontend polling:** `app.js` 13 intervals, `smartchart.js` 3, `arena_terminal.js` 2 (1 s order/position poll + chart tick), others 1.
**Credentials/sessions:** was 2 login implementations + a cron login every 5 min — fixed (bf23d20): single-flight, shared 0600 session file, backoff.

## 4. Target architecture (Phase 4) — staged, each stage gated
1. **Market data:** keep one read path (`market_hub` / histcap). Provider order stays **Angel primary (REST+WS) with Dhan chain/orderflow**, because Dhan's token expires daily and needs a manual login (project memory: never auto-login with stored credentials). Making Dhan primary needs a daily token procedure first — not changed.
2. **Indicators:** `app/indicators` (canonical ATR/EMA/SMA/VWAP/RSI from the identical group) + parity tests against each retired copy; switch call sites one package at a time.
3. **Strategy adapter:** a `StrategyResult` dataclass (direction, entry basis, SL, T1-T3, strategy id, event id = hash(strategy, instrument, bar time, side)); existing engines get thin adapters; no engine is deleted.
4. **Signal registry:** event-id dedup + lifecycle (candidate -> confirmed -> eligible/rejected -> expired) written next to, not instead of, the Arena ledger. Confidence stays uncalibrated until out-of-sample outcomes exist (project: no strategy has shown a net edge; calibration NOT VALIDATED) — no invented percentages.
5. **One paper engine:** the Arena settlement (ask-in / bid-out, 60 s gap rule, hash-chained ledger) is the canonical model because it is the only one validated against real quotes; other engines migrate to submit signals into it (as winners_scalper and safe_ai already do via `pine_submitter` / ledger signals).
6. **DB:** compatibility views over existing tables first; no table drops; counts/checksums before and after.

## 5. What was changed during this mission (all reversible, nothing deployed beyond what was already approved)
* `scripts/indicator_parity_audit.py` (new, read-only), this document.
* Earlier today (separate requests): Angel adapter hardening bf23d20, 0913379; U-turn/volume fixes.

## 6. Acceptance criteria status
1 inventory: done (sections 1-3). 2 duplicates addressed: **no** (analysed, ranked). 3 unique logic preserved: yes (nothing removed). 4 one signal generator active: **no**. 5 one paper path: **no** (the Arena ledger is the de-facto canonical one). 6 one lifecycle: no. 7 Telegram validated: not in this mission (reader audited 28-Oct, Phase 1 shipped). 8 broker normalisation validated: Angel session only. 9 duplicate prevention: partial (existing). 10 DB integrity verified: not run. 11 critical bugs: Angel login/session fixed with tests. 12 replay reproducible: not re-run. 13 look-ahead: Safe U-turn replay is causal (checked); others not re-audited. 14 performance measured: baseline only. 15 Arena features working: yes (health 200, services active). 16 no live orders: true. 17 rollback: git revert per commit. 18 no unverified production changes: the 07:46 restart carried the Angel session fix.

## 7. Why the consolidation was not executed now (and what unblocks it)
* The contest is in its live trial week; Arena stability and data integrity outrank code simplicity. Replacing ten paper engines or the live signal path needs shadow runs on real sessions (each stage: at least several trading days of side-by-side output), which cannot be compressed into one morning.
* The rules forbid restarting production; new modules only take effect after a restart, so they cannot be validated live anyway.
* The disk will fill in about 1.5 days — fixing that first is the real integrity work.
* Benchmarks for Java / Next.js were not run: no measured bottleneck shows Python or the current frontend as the limit (the measured latency costs were the Angel gateway tail and SQLite I/O), so introducing a language boundary is not justified by evidence.
Unblockers: (a) owner decision on the disk (section 0); (b) a restart window after market (22:00-23:30) per stage; (c) the market-hours latency benchmark (scheduled 09:23).
