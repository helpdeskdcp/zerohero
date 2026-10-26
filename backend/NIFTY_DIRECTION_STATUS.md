# NIFTY 50 Constituents Direction Engine — status 2026-10-26 (PARTIAL, paper only)
Checkpoint tag: `pre-nifty-direction-2026-10-26`. Ops/config/rollback: `backend/NIFTY_DIRECTION_OPS.md`.

## Item status
| item | status | evidence |
|---|---|---|
| Adapter contract, registry, mock, capabilities | COMPLETE | tests/test_nifty_direction*.py, contract test per adapter, no order surface |
| Official constituent master (50, ISIN, sector, sha256) | COMPLETE | `refresh()` downloaded the official CSV; schema check; cached copy not overwritten by a bad download |
| Official per-stock weights | **BLOCKED** | CSV has none; factsheet only top-10 + sector; nseindia.com 403. Drop `data/nifty_direction/weights.csv`. Never estimated |
| Fail-closed validation (5 s age, coverage) | COMPLETE | tests |
| Contribution / breadth / sector | COMPLETE code, DISABLED at runtime | `NO_SIGNAL: UNIVERSE_NOT_TRADABLE` until weights COMPLETE |
| Indicators (EMA/SMA/ATR/rel-vol/VWAP/OR/swings/S-R/breakout/breakdown/retest/rejection), closed-candle only | COMPLETE | no-look-ahead tests |
| 15m trend + 5m entry, states, dedup, invalidation | COMPLETE | tests |
| Futures + option-chain confirmation | COMPLETE code, mock-tested only | no live provider tested |
| 100-pt scoring (30/20/15/15/10/10), thresholds 70/55/45 | COMPLETE | non-actionable while contribution blocked; missing dependency listed |
| Chart Scanner UI (overview, 50 heatmap, breadth, sectors, contribution rank, derivatives, reasons, health, data-kind chip) | COMPLETE (not browser-verified; node logic test only) | static/chartscan.html + js/nifty_direction.js |
| DB persistence + versioned migrations (v2) | COMPLETE | store.py, migration tests incl. v1->v2 |
| API routes | COMPLETE | /status /overview /signals /health-events /paper /config /replay, POST /refresh-master (tests: no other write/order route) |
| Paper simulation (costs, slippage, risk caps, dedup, ENABLE_LIVE_ORDERS guard) | COMPLETE | tests |
| Historical replay + OOS split | COMPLETE for the 15m/5m sub-engine on NIFTY index only | HistoricalAdapter causal test; 60 sessions: 163 trades, gross -403 pts, net -1556 pts (assumed 3 bps cost), IS -1512 / OOS -44 -> **no edge** |
| Live provider adapter | **NOT BUILT / NOT TESTED** | only mock + historical exist; mode=live returns NO_ADAPTER_CONFIGURED |
| Ops docs | COMPLETE | NIFTY_DIRECTION_OPS.md |
| Full regression suite | **4006 passed, 1 failed** (46:43, no skips/errors) | see 'Verified test results' below; the 1 failure is environmental, not a regression |

## Files
New: app/nifty_direction/{adapters,constituents,validation,engine,indicators,strategy,derivatives,scoring,store,paper,historical,service,replay,api}.py; tests/test_nifty_direction{,_engine,_api}.py; frontend/static/js/nifty_direction.js; frontend/tests/nifty_direction.test.js; backend/NIFTY_DIRECTION_OPS.md.
Modified: backend/app/main.py (router block), frontend/static/chartscan.html (panel + script). Not changed: scanner modules, connectors.
Tests run: targeted 40 python (pass) + node test (pass). Service NOT restarted: the router goes live at the next restart.

## Verified test results (2026-10-26)
- Run 1 (commit 4730880, 45:30): **4 failed, 3997 passed**.
  1. `test_rules_registry::test_every_env_flag_is_classified` — **real regression from my change**: `ENABLE_LIVE_ORDERS`, `NIFTY_DIR_ADAPTER`, `NIFTY_DIR_MAX_QUOTE_AGE_SEC`, `NIFTY_DIR_MIN_WEIGHT_COVERAGE`, `NIFTY_DIR_MAX_DERIV_AGE_SEC` were read but not classified in rules.yaml. **Fixed** (commit 552720c, classification only, test untouched).
  2. `test_signals_dashboard::test_build_is_cached` and 3. `test_smart_scalper_scheduler::test_start_stop_lifecycle...` — timing under load (build took 6 s > cache TTL; leader election within 50 ms); both **pass when run alone** (re-run verified), not related to this work.
  4. `test_angel_ws_loop_responsiveness` — see below.
- Run 2 (after the fix + 6 new gate tests, commit bc51997, 46:43): **1 failed, 4006 passed**, no skipped/error lines. The one failure is #4:
  `test_receive_loop_and_event_loop_stay_responsive_during_slow_credential_call` asserts >40 updates from a 50 Hz source in a 2.2 s window (theoretical max 44). It fails deterministically here (21-23 updates, repeated runs) **and failed in the pre-change baseline run too**; `connectors/angel_ws.py` has no change since 2026-10-15. Measured cause: on this 2-vCPU host (load ~6) `asyncio.sleep(0.02)` ticks only 39 times in 2 s instead of 100, so the test's own source emits ~20 Hz. Environmental; test not weakened. Re-run at low load (e.g. night/weekend) to confirm green — NOT yet verified green.
- New tests: 40 (engine/API/base) + 6 auth-gate tests on the real `app.main` (401 without/with wrong token, 200 with a throwaway token set in-process; production credentials never read) = 46 Python, all pass; node `nifty_direction.test.js` passes; existing `chartscan.test.js` runs clean.
- DB migrations: v1->v2 upgrade + idempotency tested. Live DB untouched (wrapper guard).

## Backtest review (15m-trend + 5m-entry sub-engine, NIFTY index 1m, params fixed, nothing tuned)
Four disjoint 60-session blocks (data/nifty_direction/replay_blocks.json): 
| block | trades | gross pts | cost pts | net pts |
|---|---|---|---|---|
| 2026-07-29..10-23 | 163 | -403 | 1153 | -1556 |
| 2026-01-19..04-17 | 167 | -476 | 1231 | -1707 |
| 2025-07-23..10-17 | 168 | -54 | 1256 | -1310 |
| 2025-01-20..04-17 | 173 | +226 | 1193 | -967 |
Arithmetic verified: gross/cost/net recomputed independently from every trade row (671 trades), 0 mismatches. Block A reproduced the earlier run exactly (deterministic).
Where the loss comes from: mean gross -1.05 pts/trade (95% bootstrap CI -4.2..+2.1, i.e. indistinguishable from zero) vs the assumed cost 7.2 pts/trade (3 bps) = 19% of 1R. Gross win rate 45.6% vs ~46.6% needed at a 1.5R payoff after cost. STOP exits (36% of trades) average -42.7 pts, TARGET +56.2, TIMEOUT +10.1; LONG +1.6 / SHORT -3.7 gross per trade (not significant). So: **no edge; even at zero cost the gross is ~0, and a cheaper cost assumption (e.g. 1 bp) would still leave it negative.** Not claimed profitable.
Limitations: index bars only (no volume => VWAP/rel-volume inactive), the 3 bps cost is an assumption, contribution/breadth/sector/derivatives are not replayed (no official weights, no historical chain/futures OI here), the four blocks are sequential not walk-forward-tuned (there is nothing tuned), fills are next-bar open with 1 bp slippage.

## Deployment status
NOT deployed, service NOT restarted (oi-dashboard.service still runs the pre-change code). Exact plan, checks and rollback: `NIFTY_DIRECTION_DEPLOY_PLAN.md`.

## Remaining blockers
1. Official per-stock weights (BLOCKED, nothing estimated; contribution scoring + actionable signal stay disabled). 2. No tested live adapter (live mode = NO_ADAPTER_CONFIGURED). 3. Angel WS timing test needs a low-load re-run. 4. Dashboard not browser-verified (node logic + API tests only). 5. Owner approval for the deploy window.
