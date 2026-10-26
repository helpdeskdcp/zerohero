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

## Deployment + post-deploy follow-up (2026-10-26 evening)
**Deployed:** `oi-dashboard.service` restarted 21:11:21 IST per NIFTY_DIRECTION_DEPLOY_PLAN.md (PID 1392451, 0 restarts; health 200; journal shows no "router disabled"; arena-tg-reader/nginx active; Winners-Pine NG heartbeat resumed). Pre-restart: no active/pending arena trades, NSE closed. Rollback anchors: tag `pre-nifty-deploy-202610262110`, data copy `data/nifty_direction.bak.202610262110`, tag `pre-nifty-direction-2026-10-26`.
**Running code vs repo:** the running service has the engine as of commit bd4bc9c. The follow-up below (layout move, last_close mode, meta line) is committed after that and is **NOT yet loaded by the running API** (static HTML/JS are served from disk immediately; the API change needs one more restart, not done, awaiting approval). Until then the UI's "Last market close" option gets a 400 from the old API.

### Changes in this follow-up
- `frontend/static/chartscan.html`: the panel moved from the page bottom into the left column directly under the chart (same `.grid > div` as `#chart`; Scan results stays the right column); inner grid is `.nd-two` (auto-fit, 260 px min) so it fits the narrower column; <=600 px breakpoint for the heatmap/as-of input; mode select = Live market / Last market close / Historical replay; new `#ndMeta` line.
- `frontend/static/js/nifty_direction.js`: `metaLine()` (mode label, data kind, timezone, source, freshness, session, as-of, "NSE closed now", note, "closed-candle only; intrabar provisional not computed"); a 401 now shows "not signed in (401)" instead of a silent failure; changing mode reloads.
- `backend/app/nifty_direction/api.py`: `mode=last_close` (latest completed session present in the stored 1-minute history, as-of 15:30 IST, always labelled HISTORICAL) + `meta` block on every overview; `_last_close()` also reports the exchange calendar's own latest session and a `data_lag` flag. Today it shows session 2026-10-23 vs calendar 2026-10-26 (`data_lag: true`): the stored history is one session behind and the UI says so.
- Tests added: last_close labelled HISTORICAL + lag flag, live-without-adapter meta, bad mode 400; node `metaLine` test.

### Verification actually performed
- Python: `test_nifty_direction{,_engine,_api,_gate}.py` + `test_rules_registry.py` = **59 passed**. Node: `nifty_direction.test.js` ok; `chartscan.test.js` 2 pass / 0 fail. The FULL regression suite was **not** re-run after this follow-up (last full run: 4006 passed, 1 failed = Angel WS timing test, see above).
- Layout: structural DOM parse only — `#nd` is in the same left-column container as `#chart`, is the next element after it, `#bt` follows it, `#res` is the right column; div/section tags balanced. **No rendered-browser verification**: no Chrome is connected to this session (list_connected_browsers = []) and no headless browser is installed on the VPS. Pixel alignment, the responsive layout at desktop/tablet/mobile, browser console errors and screenshots are therefore NOT verified.
- Reproducible check for the owner: open `/static/chartscan.html` in your already-authenticated browser, hard-refresh; expect the panel right under the chart with the chart/panel/Scan-results columns aligned; switch the Mode select through the three modes (Live => "data: NONE / NO_ADAPTER_CONFIGURED"; Last close => HISTORICAL + the lag note; Replay => HISTORICAL); open DevTools console: no errors; narrow the window to <900 px (columns stack) and <600 px.

### The HTTP 401s
Expected, not a bug: every `/api/*` call and the static pages go through `_AuthGateMiddleware` (Bearer `CHANAKYA_API_TOKEN` or HTTP Basic from `.env`); my curl had no credentials. The gate is unchanged. Authenticated behaviour (401 without/with a wrong token, 200 with a valid one, routes mounted) is covered by tests/test_nifty_direction_gate.py against the real app; production credentials were never read.

### Status of the other requested items (honest)
| item | status |
|---|---|
| Live / last-close / replay modes with timezone, source, freshness, as-of | IMPLEMENTED (API + UI), tested via API tests; live mode has no adapter so it reports NO_ADAPTER_CONFIGURED; not browser-verified |
| Official versioned weights | **BLOCKED** (no authoritative full-50 weights obtainable: CSV has none, factsheet top-10 only, nseindia 403). VERIFIED_OFFICIAL_WEIGHTS / UNIVERSE_NOT_TRADABLE are therefore genuine blockers, not mapping bugs; nothing was hardcoded |
| NO_ADAPTER_CONFIGURED | genuine: no live adapter implemented/tested |
| NO_FUTURES / NO_CHAIN / NO_VALID_DERIVATIVES_DATA | genuine for the same reason (derivative logic is mock-tested; no provider wired to the engine) — NOT investigated against the instrument master/permissions in this run |
| TREND_NEUTRAL | legitimate result (last-close 2026-10-23: NEUTRAL), not an error |
| DATA_GAP | arena-side status, unrelated to this engine |
| Advancing/declining/unchanged counts | NOT done: breadth shows advancers/decliners only and is itself disabled without weights |
| Provisional intrabar signals | NOT computed (stated in the meta line) |
| Per-instrument engine (BANKNIFTY/SENSEX constituents, commodities) | NOT implemented; BANKNIFTY/SENSEX weights would hit the same blocker; commodities would need their own underlying-direction path |
| Live orders | still disabled (ENABLE_LIVE_ORDERS unset; engine refuses to run if set) |

### Remaining actions for the owner
1. Approve a second restart so the API serves `last_close` + `meta` (plan unchanged: same checks/rollback). 2. Browser check per the procedure above (or connect Chrome to this session). 3. Supply the official weights file. 4. Decide whether to scope the per-instrument (BANKNIFTY/SENSEX/commodities) extension.
**Overall status: DEPLOYED (engine as of bd4bc9c), PARTIALLY VERIFIED** — API/auth/tests verified; rendered UI not verified; follow-up changes committed but not yet running.

## Disabled-feature audit + step-1 repairs (2026-10-26 night)
See `NIFTY_DIRECTION_AUDIT_2026-10-26.md` (root cause per feature, evidence, proposed actions, repair status). Summary: breadth and sector strength were disabled by an unnecessary coupling to the weights gate -> FIXED as clearly-labelled UNWEIGHTED counts (real last-close: 14/34/0 of 48 valid); weights validation now requires effective date + source and rejects stale files; neutral trend explained in the UI (correct result, NOT_APPLICABLE). Futures/chain/derivatives-scoring: the data exists in `market_history.db` (NIFTY futures with OI/basis, 121k option rows on 2026-10-23) and the gap is adapter wiring -> not yet repaired. Official full-50 weights: BLOCKED (no authoritative source reachable). Live: BLOCKED (no stock-quote source). Two constituents (`BSE`, `TMPV`) are missing from the stored stock history (valid quotes 48/50) and the history is one session behind.
Verified: 65 targeted Python tests + node tests pass; layout confirmed by the owner in the browser ("panel disat aahe, layout barobar aahe"). Not re-run: full suite. Not deployed: repo is ahead of the running service (needs one more restart, approval pending).

## Futures + option chain wired (2026-10-27)
New `app/nifty_direction/captured_derivatives.py` (point-in-time reader over the histcap capture) + `HistoricalAdapter.futures()/option_chain()` + cadence-aware freshness in `derivatives.py`. Result on the real last close (2026-10-23 15:30 IST): futures LISTED_IN_MASTER, LONG_UNWINDING (price -0.60%, OI -7.52%, basis -25.8, 96 s old); chain 21 near-money strikes, PCR 0.77, OI walls 22500/23000, IV 16.1%, 103 s old; derivatives score -2.5; whole overview 0.7 s. NO_FUTURES / NO_CHAIN / NO_VALID_DERIVATIVES_DATA are therefore FIXED for last-close/replay. NOT done: LIVE derivatives (no live constituent feed; capture cadence exceeds the 60 s live limit), full-chain OI (capture only has near-money OI), BSE/TMPV history, official weights (BLOCKED), live adapter (BLOCKED).
Verified: 80 targeted Python tests, node tests (derivText added). Full suite NOT re-run. NOT deployed (running service = eea2ee2): the next restart loads steps 1+2. Commit below.
