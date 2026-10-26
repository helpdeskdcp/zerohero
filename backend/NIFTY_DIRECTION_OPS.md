# NIFTY 50 Constituents Direction Engine — operations (paper only)

## What runs
Library + read-only API; **no background worker, no scheduler, no order path**. `ENABLE_LIVE_ORDERS` is never read for enabling anything: if it is set truthy, `PaperBook` and `service.evaluate` raise `LiveOrdersForbidden`.
Routes (all in `app/nifty_direction/api.py`, registered in `app/main.py`; GET unless noted):
`/api/nifty-direction/status`, `/overview?mode=live|replay&as_of=&persist=`, `/signals`, `/health-events`, `/paper`, `/config`, `/replay?sessions=&end=`, `POST /refresh-master` (only write: downloads the official constituent CSV).
UI: the "NIFTY 50 constituents direction" panel at the bottom of `/static/chartscan.html` (`static/js/nifty_direction.js`). The panel shows a data-kind chip: LIVE / HISTORICAL / MOCK / NONE.

## Configuration (env)
| var | default | meaning |
|---|---|---|
| `NIFTY_DIR_ADAPTER` | unset | registered adapter name for mode=live. **No live adapter exists yet** (only `mock`); unset => `NO_ADAPTER_CONFIGURED` |
| `NIFTY_DIR_MAX_QUOTE_AGE_SEC` | 5 | a constituent quote older than this is dropped |
| `NIFTY_DIR_MIN_WEIGHT_COVERAGE` | 0.90 | share of index weight with valid quotes needed to score |
| `NIFTY_DIR_MAX_DERIV_AGE_SEC` | 60 | futures/chain freshness |
| `NIFTY_DIR_DB`, `NIFTY_DIR_HIST_DB` | data/nifty_direction.db, data/historical/stocks_1m/stocks_1m.db | stores |
Strategy / score / paper parameters are dataclasses (`StrategyConfig`, `ScoreConfig`, `PaperConfig`); `store.save_config(name, dict)` versions a change, `/config` shows the latest.
Data files (data/nifty_direction/): `constituents.csv` (official), `weights.csv` (operator-supplied; absent today), `master.json` (built, with sha256 of both inputs).

## Startup / restart
Nothing to start. The router loads with the main service (`oi-dashboard.service`); picking up `main.py` needs one service restart (do it outside trading hours / when no participant has an active trade). Rebuild the master: `curl -X POST :7060/api/nifty-direction/refresh-master` (or `C.refresh()`).

## Health checks / logs
`/status` (blockers, master checksums), `/overview` -> `health` block (valid quotes, weight coverage, invalid reasons, derivative availability), table `health_events` in nifty_direction.db. A failed router import prints `[nifty_direction] router disabled: ...` in the service log and leaves everything else running.

## Official weights (BLOCKED)
Constituents/ISIN/sector: official NSE Indices CSV (downloaded). Per-stock weights: NOT published in that CSV; the factsheet PDF lists only the top 10 and sector weights; nseindia.com API answers 403 to this host. To unblock: put `symbol,weight_pct` (50 rows, sum within 0.5 of 100, from NSE Indices) at `data/nifty_direction/weights.csv`, `POST /refresh-master`. Until `weights_status == COMPLETE` contribution/breadth/sector components are disabled and the signal is never actionable. No equal/estimated weights are ever substituted.

## Known limitations
No tested live adapter (constituent quotes, futures OI, option chain are only exercised via the mock). Replay covers the 15m-trend + 5m-entry sub-engine on NIFTY index bars only (no volume on the index, so VWAP/rel-volume are inactive there). The paper cost (3 bps round trip) is an assumption. Result of the 60-session replay (2026-07-29..10-23): gross -403 pts, net -1556 pts after assumed cost, 163 trades — **no edge**; see `data/nifty_direction/replay_60s.json`.

## Rollback
Code: `git reset --hard pre-nifty-direction-2026-10-26` (or revert the nifty_direction commits). Data: `rm data/nifty_direction.db` is safe (history only). Removing the `# ---- NIFTY 50 constituents direction engine` block from `app/main.py` unmounts the API; the chart-scan page degrades (panel shows an error) but its scans keep working.
