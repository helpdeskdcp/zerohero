# Smart Chart

Vela chart + PineTS Pine runtime + Upstox candles + paper-only strategy simulation, integrated into the ZeroHero dashboard.

* Page: `/static/smartchart.html` (dashboard sidebar → **Smart Chart**, next to *Chart Scan*; opens in a new tab like the other chart pages)
* API: `/api/smart-chart/*` (same auth gate as the rest of the app)
* Code: `backend/app/smart_chart/` (Python + Node worker), `frontend/static/smartchart.html`, `frontend/static/js/smartchart*.js`, `frontend/static/vendor/smartchart/`
* Own SQLite file: `backend/data/smart_chart.db` (never `chanakya.db`), additive migrations tracked by `PRAGMA user_version`

> **"Chart Arena" does not exist in this repository.** The only things named arena are the paper *contest* (`arena.html`) and its API. The existing chart pages are
> *Chart Scan* (`chartscan.html`) and the platform *live chart*. Smart Chart is therefore a new navigation entry alongside *Chart Scan*, using the same auth, theme tokens and
> static hosting. If a "Chart Arena" is meant to be a different product, it is not in this repo and nothing here touches it.

## What it does / does not do

| Does | Does not |
| --- | --- |
| Vela candlestick chart (workspace shell: zoom/pan/crosshair, symbol search, timeframes, drawing tools, indicator dialogs, mobile layout) on **real Upstox candles** | 1-second bars (Upstox serves 1-minute at best, no historical ticks) · a watchlist panel (Vela's workspace provides symbol search, not a watchlist) |
| Timeframes 1m 3m 5m 15m 30m 1h 1D (3m/5m/15m aggregated from 1m, 1h from 30m, session-anchored at 09:15 IST) | MCX / futures / options contracts (roll chaining is a separate design) · week/month bars |
| Pine v5/v6 editor, saved private scripts with versions, input settings, compile diagnostics with line:col | Pine v1–v4 · TradingView compatibility claims · other symbols/lower timeframes in `request.security()` |
| Indicators run **in the browser** (Vela-PineTS Web Worker); compile / backtest / paper / analysis run **on the server** (isolated Node process) with the same PineTS 0.13.0 | Live orders of any kind. There is no broker, order-manager or execution import in the package (enforced by a test) |
| Backtests with explicit assumptions, equity curve, stats, reconciliation, look-ahead (causality) check | Claims that a backtest predicts anything |
| Paper portfolios (forward-only, daily loss limit, idempotent, audited) | Real fills: no queue position, partial fills, rejections, latency |
| Deterministic analysis (trend, structure, S/R, ATR, reference levels) + optional AI commentary over that summary | Order book, bid/ask, OI, IV, aggressor data (not in this source; listed as unavailable, never invented) |

## Architecture

```
browser                                         server (FastAPI app, port 7060)                    node (one short-lived process per request)
-------                                         --------------------------------                    ------------------------------------------
Vela workspace ──getBars/subscribe──▶ /candles, /live ──▶ candles.py ──▶ upstox_ext.py ──▶ connectors/upstox.py (existing auth/refresh) ──▶ Upstox REST
 └ PineWorkerEngine (Web Worker, PineTS)                      │ cache (bounded LRU, single-flight, rate-spaced)
Smart Chart panels ─▶ /scripts /compile /run /backtests /paper /analysis ─▶ service.py ─▶ runner.py ─▶ `node --permission` worker.mjs (PineTS)
                                                              └ store.py (smart_chart.db)  backtest.py · paper.py · analysis.py · capabilities.py
```

Why two Pine execution places: the chart needs live, incremental, non-blocking execution next to the renderer (Vela-PineTS worker); compile diagnostics, backtests,
paper simulation and AI inputs need a server-side, auditable, resource-limited execution. **Cross-check (verified in a real browser, 2026-10-25):** the same strategy on the same 1,500 bars produces
identical entry/exit times and prices in the in-browser engine and in the server worker (`frontend` vs `runner`), once both use the same sizing rules
(`mincontract 1`, 0.05 tick, 100% margin).

## Licensing (read this)

| Component | Version | License |
| --- | --- | --- |
| `@luxalgo/vela` | 0.9.0 | Apache-2.0 **+ NOTICE**: the Vela attribution mark must stay visible (the page also carries a visible "Vela™" link) |
| `@luxalgo/vela-pinets` | 0.3.0 | **AGPL-3.0-only** |
| `pinets` | 0.13.0 | **AGPL-3.0-only** (dual-licensed: LuxAlgo also sells a commercial licence) |
| esbuild (build only) | 0.28.2 | MIT |

Findings and the chosen architecture:

* Vela core is Apache-2.0 and ships no Pine code; the Pine layer is a separate AGPL package, so the AGPL surface is confined to **`backend/app/smart_chart/`, `frontend/static/js/smartchart*.js` and the vendor bundle**. Nothing else in ZeroHero imports PineTS.
* AGPL-3.0 §13: users who interact with the program **over a network** must be offered the Corresponding Source of the AGPL-covered work, including modifications. Today ZeroHero is a single-owner, authenticated service (the only network user is the owner, who has the
  source). **If anyone else is ever given access, or the service is made public, the Smart Chart source (this repository's smart_chart code + the bundle's build inputs) must be offered to them** — or a commercial PineTS licence obtained from LuxAlgo, or the Pine layer replaced.
  `/api/smart-chart/license` states this to the user at runtime. This is a decision for the repository owner; nothing here is legal advice.
* Do not describe the combined product as MIT or as "TradingView compatible". PineTS and Vela are independent LuxAlgo projects, not affiliated with TradingView, Inc.; "Pine Script" and "TradingView" are TradingView trademarks. The UI says "independent of TradingView; practical compatibility only".
* Licence texts and a notice file ship next to the bundle in `frontend/static/vendor/smartchart/`.
* No TradingView data, charts, sessions or servers are used or scraped; all data is Upstox (authorised, existing token).

## Data (Upstox)

Verified against the live API on 2026-10-25:

* Historical intervals accepted: `1minute`, `30minute`, `day`, `week`, `month`. **`3/5/15minute` and 1h are rejected (HTTP 400 `UDAPI1020`)** — the connector's own `INTERVALS` constant lists them wrongly; Smart Chart aggregates instead and does not touch the connector.
* Max range per request: 1-minute ≈ 1 month (28 d used), 30-minute 90 d (88 d used), day ≈ 10 y (3600 d used). 1-minute history reaches back to at least 2022-03.
* Current session: intraday endpoint (`1minute`, `30minute`) via `upstox_ext.fetch_intraday`.
* Index volume is not published (always 0): flagged in the UI/analysis/backtest. Equity volume is real.
* Auth: reuses `app/connectors/upstox.py` (env tokens via the existing loader, redaction, retries). Nothing prints, stores or returns a token; health shows booleans only. Expired token → clear error, cached bars still served and labelled **STALE**.
* **Live = REST polling, honestly labelled** ("LIVE (polled)", `transport: rest-poll`). Server refreshes the current session at most every 3 s per instrument (shared by all viewers, single-flight, global ≥150 ms spacing between Upstox calls); the browser polls `/live` every 3 s while the market is open, 30 s when closed, 15 s when the tab is hidden, exponential back-off on errors. The Upstox WebSocket feed (protobuf, separate authorisation) is **not** used; the existing platform WS stream is AngelOne-sourced and is deliberately not mixed in (no silent source switching).
* Duplicates / out-of-order: server merges by bar open time (tail replace), the client `BarGate` drops stale/duplicate bars and never rewinds the chart.
* Status pill states: `LIVE (polled)`, `DELAYED` (refresh >20 s old), `STALE` (error, or no new bar for >2 bars while open), `MARKET CLOSED`, `OFFLINE`.

## Pine capability registry

`GET /api/smart-chart/capabilities` — generated from the pinned PineTS checkout's own coverage tables (`scripts/gen_pine_capabilities.py`, records version + commit) merged with what **our** tests verify.

* **supported** — verified by `tests/test_smart_chart_pine.py` against independent reference implementations (SMA/EMA/RMA/RSI/ATR/highest/lowest/change/crossover/crossunder, math.max/abs, plot, plotshape, hline, input.int, indicator/strategy declarations, strategy.entry/close, next-bar-open fills, bracket fills).
* **partial** — `request.security` (same chart symbol, timeframe ≥ chart, derived from loaded bars; other symbols/lower timeframes are rejected with an error, not faked), `strategy.exit` (intrabar stop-vs-limit order is an OHLC assumption), upstream "needs testing" items.
* **not_tested** — listed as implemented by PineTS, not verified by us (the large majority). **unsupported** — not listed upstream (static warning; an actual failure is an error).
* Compile = static capability scan + a PineTS dry run, so unknown functions / undeclared identifiers / syntax errors come back with code, line and column.

PineTS behaviours worth knowing (all handled/tested): fills are rounded to the 0.05 tick; `//@version=5` strategies default to **no margin** in PineTS (unlimited leverage) while TradingView and the in-chart engine use 100% — the server worker defaults to 100% unless the script declares `margin_long/short` (a bug found by cross-checking server vs chart).

## Backtest & paper assumptions (also returned with every result)

1. Signal on bar N close → fill at bar N+1 open (never the same bar). 2. Only closed bars (forming bar excluded). 3. Fills rounded to 0.05; slippage always adverse. 4. 100% margin, oversized orders rejected.
5. Bracket (stop/limit) evaluated on bar OHLC; same-bar ambiguity follows PineTS's OHLC-path convention. 6. No intrabar data. 7. Simulated ≠ broker fills (index symbols are not directly tradable). 8. Index volume = 0. 9. <30 trades ⇒ not reliable.

* **Causality check** (default on): the history is cut right after sampled trade entries/exits and the script re-run; every earlier trade must reproduce exactly. A repainting / future-peeking script fails (tested with a `lookahead_on` script). A pass is evidence, not proof.
* **Reconciliation**: net profit derived from the trade ledger must equal PineTS's own `netprofit` (flagged otherwise).
* **Paper**: forward-only from creation time (earlier history is never "traded"); `step` re-runs on closed bars and upserts by `(portfolio, entry_time:id:direction)` so a trade is recorded once; entries after the day's loss limit is hit are skipped and counted, the portfolio HALTs; everything audited (`paper.*` events). It advances when the page calls `step` (every 30 s while open) or when you press **Step now**; it **catches up on the next step** (it recomputes from the last 1,500 closed bars) — a server-side scheduler is not included.

## Security model

* Pine is untrusted. Server execution: one `node --permission --allow-fs-read=<package>` process per request — no fs write, no child processes, no worker threads, no native addons; **empty environment** (cannot see `.env`/tokens); `fetch`/`WebSocket`/`XMLHttpRequest` deleted; no network provider; wall-clock timeout (25 s, kills the process group), `RLIMIT_CPU`, `RLIMIT_FSIZE=0`, V8 heap cap 512 MB, max 2 concurrent runs (else 503), 30 runs/min/owner (429), 64 KB script cap, ≤10,000 bars, bounded stdout. PineTS's own loop guard stops infinite loops. JS escape attempts via Pine (`process`, `require`, `fetch`, `constructor.constructor`, `import`) are rejected (tested).
* Browser execution is the Vela-PineTS Web Worker (isolated from the page DOM).
* Auth: app-wide gate (Basic/Bearer). Every object route is owner-scoped in SQL (another owner's id ⇒ 404). **CSRF:** mutating routes require `X-SmartChart-CSRF: 1` (browsers attach Basic credentials cross-site but cannot add a custom header without a preflight).
* Pydantic validation on every body/query; SQL is parameterised; UI escapes every dynamic string (`esc()`), highlight output is escaped; error envelope never leaks internals/paths/secrets; secret-safe logging.
* Resource bounds: LRU candle cache (24 series × 80,000 bars), job/backtest retention, script/version caps, paper portfolio cap.

## Database (additive migrations)

`scripts`, `script_versions`, `compile_diagnostics`, `exec_jobs` (+bounded indicator output summary), `backtests` (stats, trades, equity, assumptions, data meta), `paper_portfolios`, `paper_trades` (UNIQUE per portfolio+trade_key), `user_prefs`, `audit_events`. Tick data is not stored (candles come from Upstox/cache).

## API (all under `/api/smart-chart`, JSON; errors `{"error":{"code","message","details"}}`)

| Method & path | Purpose |
| --- | --- |
| `GET /health` · `/meta` · `/license` · `/capabilities` · `/templates` | runtime/data health (no secrets), timeframes/limits/not-supported, licence notice, capability registry, starter scripts |
| `GET /symbols?q=` · `/symbols/all` | search; full picker list (5 indices + NSE EQ) |
| `GET /candles?symbol=&tf=&limit=&before=` · `/live?symbol=&tf=&since=` | candles + freshness metadata; cheap live poll |
| `GET/POST /scripts` · `GET/PUT/DELETE /scripts/{id}` · `GET /scripts/{id}/versions[/{v}]` | script library (owner-scoped, versioned) |
| `POST /compile` · `POST /scripts/{id}/compile` | diagnostics, inputs schema, capability scan (saved variant records status) |
| `POST /run` | execute an indicator/strategy on server candles (plots, shapes, drawings, strategy ledger) |
| `POST /backtests` · `GET /backtests[/{id}]` | run/list/read backtests |
| `GET/POST /paper` · `GET/DELETE /paper/{id}` · `POST /paper/{id}/start\|stop\|step` · `GET /paper/{id}/trades` | paper portfolios |
| `POST /analysis` | deterministic analysis (+ optional AI commentary) |
| `GET/PUT /prefs[/{key}]` · `GET /jobs` · `GET /audit` | preferences, execution history, audit trail |

## Tests (run by `backend/scripts/run_tests.sh`; frontend: `node frontend/tests/smartchart.test.js`)

`test_smart_chart_candles` (aggregation, cache, pagination, stale-on-error), `_pine` (conformance vs independent references, diagnostics, sandbox limits, templates, margin regression, >64 KB output regression),
`_backtest` (accounting math, reconciliation, causality incl. a look-ahead script, paper idempotency / loss limit / ownership), `_api` (CRUD, ownership on every route, CSRF, validation, rate limit, AI optional, auth gate on the real app), `_security` (no order imports, no eval/exec, permission model really denies writes/children/workers, injection round-trips, secret hygiene).
Browser verification (headless Chromium, real Upstox data): chart render, symbol/timeframe, save/compile/add indicator, error diagnostics, backtest, paper, analysis, runtime tab, reload persistence, mobile layout, browser-vs-server parity.

## Operate

```bash
# one-time / after a clean checkout
cd backend/app/smart_chart && npm ci && npm run build:web      # restores node_modules, rebuilds frontend/static/vendor/smartchart/
# service (runs in place from /root/zerohero/backend; same unit as the dashboard)
systemctl restart oi-dashboard.service ; curl -s localhost:7060/api/health
# health of the feature (needs the dashboard credentials)
curl -u "$USER:$PASS" localhost:7060/api/smart-chart/health
# tests
cd backend && scripts/run_tests.sh tests/test_smart_chart_*.py -q ; node ../frontend/tests/smartchart.test.js
# regenerate the capability registry after bumping pinets (use a checkout of the SAME version)
python3 backend/scripts/gen_pine_capabilities.py /path/to/PineTS
```

Limits can be tuned with `SMARTCHART_*` env vars (see `config.py`) in `.env`.

### Rollback

* Feature off without a redeploy of anything else: remove the 3-line include in `app/main.py` (`from .smart_chart import routes ... app.include_router(...)`) and restart `oi-dashboard.service`; the sidebar link then 404s harmlessly. The chart pages, `chanakya.db` and every other module are untouched.
* Full removal: also delete `backend/app/smart_chart/`, `frontend/static/{smartchart.html,js/smartchart*.js,vendor/smartchart}`, the nav `<a>` in `frontend/index.html`, `backend/tests/*smart_chart*`, `backend/scripts/gen_pine_capabilities.py`, and (optionally) `backend/data/smart_chart.db*` — the only data this feature owns.

## Known limits / open items

* **Live-session path not exercised under live conditions**: built and verified on a Sunday (market closed). The intraday endpoint returns an empty list while closed; its in-session behaviour (forming-bar updates, DELAYED/STALE transitions) is covered by unit tests and offline fakes but must be eyeballed on the next trading session.
* AGPL-3.0 §13 source-offer decision (above).
* Paper portfolios advance only while the page is open or via *Step now* (no server scheduler).
* The 3.1 MB vendor bundle is served uncompressed by StaticFiles (cached by the browser after the first load).
* `request.security` is limited (above); the capability registry's "not_tested" bucket is large by design.
