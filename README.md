# Chanakya AI — Trading Control Room

A single-process **FastAPI modular monolith** for deterministic (rule-based, *not*
ML) options-trading signals + live position monitoring on Angel One, with a
vanilla-JS dashboard, SQLite storage, and an in-process background runner.

**Execution is disabled by default.** The runner supports PAPER, SHADOW and a
triple-gated LIVE adapter. LIVE order routing requires all server-side guards:
`CHANAKYA_API_TOKEN`, `CHANAKYA_ALLOW_LIVE=1`, and a non-empty
`CHANAKYA_LIVE_CONFIRM_TOKEN`, plus `execution_enabled=true` and
`execution_mode=LIVE` in the runner config. The confirmation secret is never
stored in SQLite or returned by an API. Leave these unset for paper/monitor use.

---

## Architecture at a glance

```
Browser SPA ── HTTP + /ws ──▶ FastAPI (uvicorn, --workers 1)
                                │  startup hook
                                ▼
                         ScalpRunner._loop  ── single active instance via a
                         (asyncio task, in-process)   SQLite lease (runner_lease)
                                │
         ┌──────────────────────┼───────────────────────────┐
         ▼                      ▼                           ▼
  Trading engines        Angel One connectors         SQLite (WAL, 1 file)
  (pure functions)       REST: candles / getPosition  ai_signals_log
  signal · scalp · OI    WS : SmartWebSocketV2 LTP    ai_paper_trades
  risk · reversal        (binary parser + reconnect)  app_settings (KV)
  paper_trading · combos
```

- **Modular monolith.** One deployable, one DB. `connectors/`, `engines/`,
  pipelines, runner, registry are cleanly separated (no circular imports).
- **Runtime.** `ScalpRunner` runs on the web server's event loop. A cross-process
  **lease** (`db.lease_acquire`) guarantees exactly one active runner even under
  multiple uvicorn workers; the rest are hot standbys that serve the leader's
  published view.
- **Adaptive cadence.** Markets closed + nothing open → 60 s loop, broker poll
  ≥ 5 min, reversal/wrong-side scans skipped. Market hours + open positions →
  1 s (`fast_mode`) with REST candles cached ≤ 60 s.

---

## Directory map

```
backend/app/
  main.py            FastAPI app — 34 routes + /ws, opt-in bearer auth, SPA
  db.py              SQLite: schema, idempotent migrations, indexes, singleton lease
  instruments.py     friendly-name → {exchange, symboltoken}; timeframe→interval; lookback window
  orchestrator.py    CORE pipeline  : connector → signal → OI → risk → gate → log → paper
  scalp_pipeline.py  SCALP pipeline : connector → scalp → risk → gate → log → paper (no OI)
  pipeline_core.py   shared plumbing for the two pipelines (signal-id, log+notify, open_trade map)
  scalper.py         ScalpRunner: monitor + broker-sync + scalp automation + reversal scan
  combos.py          strangle/combo grouping — combined-exit alerts (no auto-close)
  reversal.py        S/R reversal detector → CE/PE + entry/stop/target
  research.py        descriptive aggregation over logged rows (45 s cached)
  mcp_server.py      stdio MCP server exposing the engines as tools (manual entry point)
  connectors/
    angelone.py      REST: TOTP login, getCandleData, getPosition (+ retry)
    angel_ws.py      AngelMarketFeed: SmartWebSocketV2 binary LTP cache + 1m OHLC ring
    telegram.py      alert sender (never raises)
  engines/
    signal_engine.py     EMA/RSI/ATR/VWAP/MACD/ADX · regime · sigmoid probability · ATR targets
    scalp_engine.py      VWAP_RECLAIM / EMA_PULLBACK / MOMENTUM_BREAK · tight ticks · session filter
    oi_options_engine.py PCR · max-pain · OI S/R · strike-selection score  (needs a chain input)
    risk_engine.py       hard gates + ATR position sizing
    paper_trading.py     OPEN/mark/CLOSE · MFE/MAE · SCALP trail+time-stop · MANUAL = monitor-only
frontend/              index.html + static/js/app.js + static/css/style.css  (no build step)
backend/tests/         pytest — engines, gate, combos, WS parser, DB lease (29 tests)
scripts/               systemd unit (--workers 1) + nginx vhost
install.sh             one-key Debian installer (venv, systemd, nginx, ufw, certbot)
```

Entry points: `uvicorn app.main:app` (the app) and `python -m app.mcp_server` (manual tool).

---

## Run

**Local dev**
```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill in Angel One SmartAPI + TOTP + Telegram
uvicorn app.main:app --reload --port 8420 --env-file .env
# open http://localhost:8420
```

**Tests**
```bash
cd backend && ./venv/bin/python -m pytest tests/ -q
```

**Deploy (Debian VPS)** — `sudo bash install.sh [yourdomain.com]`. Idempotent;
re-run after `rsync`-ing updated code. systemd keeps it running; Nginx
reverse-proxies including `/ws`.

---

## Configuration

`backend/.env` (chmod 600, never committed):

| Var | Purpose |
|---|---|
| `ANGEL_API_KEY` / `ANGEL_CLIENT_ID` / `ANGEL_PASSWORD` / `ANGEL_TOTP_SECRET` | Angel One SmartAPI login |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` / `TELEGRAM_SIGNALS_CHANNEL_ID` | alerts |
| `CHANAKYA_DB_PATH` | SQLite file location |
| `CHANAKYA_API_TOKEN` | **optional** — if set, `/api/*` (except `/api/health`) and `/ws` require `Authorization: Bearer <t>` or `?token=<t>`. Unset = open dashboard. |
| `CHANAKYA_ALLOW_LIVE` / `CHANAKYA_LIVE_CONFIRM_TOKEN` | Server-only LIVE-execution gates. Set only together with `CHANAKYA_API_TOKEN`; otherwise LIVE remains blocked. |

Runner config lives in `app_settings.scalp_config` (JSON) and is edited live via
`POST /api/scalp/config` or the Scalping tab. Instrument tokens: seeds for the
liquid indices + `POST /api/instruments` to add/correct (MCX contracts are
expiry-dated — roll them each expiry).

`GET /api/env-check` reports which credentials are set (booleans only).

---

## REST API (selected)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/run` | Core pipeline (connector → signal → OI → risk → gate → paper) |
| POST | `/api/scalp/run` · `/api/scalp/signal` | Scalp pipeline / engine only |
| GET  | `/api/scalp/status` · `/api/scalp/feed` | Runner + WebSocket-feed health |
| GET/POST | `/api/scalp/config` · `/api/scalp/{arm,disarm}` | Runner config + arm switch |
| GET  | `/api/monitor` | One-shot Live Monitor snapshot (positions, scalps, combos, reversals, feed) |
| POST | `/api/positions/track` · `/api/positions/levels` · `/api/positions/untrack` | Monitor-only external position tracking |
| GET  | `/api/positions` · `/api/broker/positions` | Tracked mirrors / live Angel One net positions |
| GET/POST | `/api/positions/combo*` | Strangle/combo groups |
| GET  | `/api/reversal?symbol=&timeframe=` | S/R reversal read with a CE/PE + levels |
| GET  | `/api/signals` · `/api/trades` · `/api/research` | Ledger / paper trades / descriptive stats |
| GET  | `/api/instruments` GET/POST · `/api/health` · `/api/env-check` | Registry / liveness / creds |
| WS   | `/ws` | Live stream: `signal`, `scalp_*`, `position_*`, `combo_*`, `reversal_signal` |

---

## What it is NOT

- **No ML / no LLM in the app.** "AI-" names are legacy n8n labels. Probabilities
  are deterministic sigmoid-of-evidence — no calibration, no forward claim.
- **LIVE is opt-in and guarded.** Never enable it from an open dashboard.
  Keep a broker-side GTT/OCO as the independent protection layer.
- **No option-chain fetcher / Greeks.** The OI engine works only with a chain
  passed in.
