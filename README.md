# Chanakya AI — Trading Control Room

Full backend + responsive web UI, ported 1:1 from your n8n workflows
(`AI-SIGNAL-ENGINE`, `AI-OI-OPTIONS`, `AI-RISK-ENGINE`, `AI-MASTER-ORCHESTRATOR`,
`AI-ANGELONE-CONNECTOR`, `AI-PAPER-TRADING`, `AI-RESEARCH-ANALYSIS`) into a
standalone Python/FastAPI service with a single-page dashboard that
auto-adapts between mobile and desktop.

**Paper mode only.** `live_trading` is hard-coded `false` everywhere in the
pipeline and is never derived from input — same fail-closed design as your
n8n `NO-TRADE GATE` node.

## Architecture

```
backend/
  app/
    engines/
      signal_engine.py       ← AI-SIGNAL-ENGINE  (RSI/EMA/ATR/ADX/VWAP/MACD, flat-market + stale guards)
      oi_options_engine.py   ← AI-OI-OPTIONS      (PCR, max pain, strike selection score)
      risk_engine.py         ← AI-RISK-ENGINE     (hard gates, ATR-based position sizing)
      paper_trading.py       ← AI-PAPER-TRADING   (OPEN/mark/CLOSE lifecycle)
    connectors/
      angelone.py            ← AI-ANGELONE-CONNECTOR (TOTP login + historical candles)
      telegram.py            ← Signal/trade alerts to your bot + channel
    orchestrator.py          ← AI-MASTER-ORCHESTRATOR (fail-closed gate, chains everything)
    research.py              ← AI-RESEARCH-ANALYSIS (descriptive stats only, no forward prediction)
    db.py                    ← SQLite: ai_signals_log, ai_paper_trades
    main.py                  ← FastAPI app, REST + WebSocket, serves the frontend
  .env                        ← your credentials (chmod 600, never commit)
  requirements.txt

frontend/
  index.html                 ← single-page dashboard shell
  static/css/style.css       ← responsive: sidebar+tabs on desktop, bottom tab bar on mobile
  static/js/app.js           ← view routing, live WebSocket feed, REST calls

scripts/
  chanakya-app.service       ← systemd unit
  nginx-chanakya-app.conf    ← reverse proxy template (WebSocket-aware)

install.sh                   ← one-key Debian installer
```

## Quick install (Debian VPS, e.g. bramha.cloud)

1. Copy this whole folder to your server, e.g.:
   ```bash
   rsync -avz chanakya-app/ you@bramha.cloud:/home/you/chanakya-app/
   ```
2. SSH in and run the installer as root:
   ```bash
   ssh you@bramha.cloud
   cd chanakya-app
   sudo bash install.sh                      # HTTP only, bound to server IP
   # or, with a domain for automatic HTTPS:
   sudo bash install.sh chanakya.yourdomain.com
   ```
3. Open the URL it prints. That's it — systemd keeps it running and
   restarts it on crash/reboot; Nginx reverse-proxies port 8420 including
   the `/ws` WebSocket route for the live feed.

The installer is idempotent — re-run it any time you `rsync` updated code
over; it rebuilds the venv, reinstalls deps, and restarts the service.

## Local dev (no install script)

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8420
# open http://localhost:8420
```

## Credentials (`backend/.env`)

Already populated from the files you uploaded (Angel One SmartAPI + TOTP,
Telegram bot/chat/channel IDs). The file is `chmod 600` by the installer.
Rotate any of these values by editing `.env` on the server and running
`sudo systemctl restart chanakya-app` — nothing is hardcoded elsewhere.

`GET /api/env-check` reports which variables are set (booleans only,
values are never sent to the browser).

## REST API

| Method | Path                  | Purpose |
|--------|------------------------|---------|
| POST   | `/api/run`             | Runs the full pipeline: connector → signal → OI → risk → gate → paper trade |
| POST   | `/api/engine/signal`   | Signal engine only (raw candles in, decision out) |
| POST   | `/api/engine/oi`       | OI/options engine only |
| POST   | `/api/engine/risk`     | Risk engine only |
| GET    | `/api/signals`         | Signal ledger (from `ai_signals_log`) |
| GET    | `/api/trades`          | Paper trades (`?status=OPEN\|CLOSED`) |
| POST   | `/api/trades/mark`     | Mark-to-market an open trade with a new LTP (auto-closes on SL/T1) |
| POST   | `/api/trades/close`    | Manually close a trade at a given exit price |
| GET    | `/api/research`        | Descriptive aggregation (win rate, profit factor, regime breakdown) |
| GET    | `/api/health`          | Liveness check |
| GET    | `/api/env-check`       | Which credentials are configured (no values) |
| WS     | `/ws`                  | Live signal/trade stream powering the dashboard feed |

## Notes carried over from your n8n workflows

- **Flat-market detection** and the **RSI neutral-50 fix** (zero gain + zero
  loss → 50, not 100) are preserved exactly.
- **Staleness guard**: candles older than `max_stale_sec` (default 900s)
  force `NO_TRADE`.
- **ATR-based sizing** in the risk engine floors to whole lots and caps by
  available margin; kill switch, daily-loss, consecutive-loss, and
  max-open-position gates are hard rejects before any sizing math runs.
- **Strike selection** in the OI engine is a deterministic score (liquidity
  + spread + moneyness + OI momentum), not "nearest strike."
- The **orchestrator gate** only approves a trade when data is `OK`, the
  signal decision is `TRADE`, risk is `APPROVED`, and — for options — the OI
  engine also independently returns `TRADE`. Any one failure logs the
  attempt as `NO_TRADE` and nothing is opened.
- Verified against the scenarios in your `AI-TEST-MATRIX.json`
  (insufficient candles, flat market, stale data, uptrend/downtrend,
  determinism, thin/illiquid chains, kill switch, zero-stop, wrong-side
  stop, daily-loss limit) — all pass against this Python port.

## What wasn't ported

- `AI-APP-CONNECTOR` (generic outbound webhook forwarder) and
  `AI agent chat` (a general-purpose n8n LLM chat agent using SerpAPI) are
  n8n-specific integration conveniences, not core to the trading pipeline.
  If you want either wired in (e.g. forwarding approved signals to another
  app, or an in-dashboard chat assistant), say so and I'll add it.
