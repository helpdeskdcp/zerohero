# NIFTY 50 Constituents Direction Engine — status 2026-10-26 (PARTIAL, paper only)
Checkpoint tag: `pre-nifty-direction-2026-10-26` (rollback: `git reset --hard` to it; code only).

## Audit (VERIFIED)
- Chart Scanner = `app/scanner/*` + `frontend/static/chartscan.html` + `js/chartscan.js` (daily/intraday DSL scans, observation only). Broker layers present: Angel (REST+WS), Dhan, Upstox read-only; `app/instruments.py`, `feed_registry`, `market_hub`.
- `app/scanner/stocks.py` hardcodes a 53-symbol UNIVERSE (not the official 50) — pre-existing, left untouched; the new engine does not use it.
- Official constituent CSV (symbol/industry/ISIN, 50 rows) is downloadable from niftyindices.com. The factsheet PDF publishes only the TOP-10 weights and sector weights. nseindia.com API returns 403 from this VPS.

## BLOCKER (not invented around)
Official weights for constituents #11-50 are not obtainable here. The master reports `weights_status=INCOMPLETE/MISSING`, the engine returns `NO_SIGNAL (UNIVERSE_NOT_TRADABLE)`. Unblock = drop a `symbol,weight_pct` CSV (50 rows, sum ≈ 100, from NSE Indices) into the loader; checksums are recorded.

## Built (tests: tests/test_nifty_direction.py, 9 pass)
`app/nifty_direction/`: adapters.py (base protocol, registry, mock; no order surface, tested), constituents.py (CSV parse, weights file, checksums, fail-closed validate), validation.py (5 s quote age, coverage >= 90 %), engine.py (contribution/breadth/sector).
## Not built yet
Indicators + 15m/5m strategy, derivatives confirmation, 100-pt scoring, DB persistence, API routes, dashboard A-I, replay, real Angel/Dhan adapter wrapper, deploy/rollback doc. ENABLE_LIVE_ORDERS is not read anywhere; no order API exists in this package.
