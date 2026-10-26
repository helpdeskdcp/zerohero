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
| Full regression suite | **PENDING** | run in progress at commit time (a failure seen at ~7% not yet investigated); baseline earlier stopped at 1 failure in a receive-loop test after 234 passed |

## Files
New: app/nifty_direction/{adapters,constituents,validation,engine,indicators,strategy,derivatives,scoring,store,paper,historical,service,replay,api}.py; tests/test_nifty_direction{,_engine,_api}.py; frontend/static/js/nifty_direction.js; frontend/tests/nifty_direction.test.js; backend/NIFTY_DIRECTION_OPS.md.
Modified: backend/app/main.py (router block), frontend/static/chartscan.html (panel + script). Not changed: scanner modules, connectors.
Tests run: targeted 40 python (pass) + node test (pass). Service NOT restarted: the router goes live at the next restart.
