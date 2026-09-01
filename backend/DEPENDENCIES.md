# Autoscalp — Dependencies & Next-Upgrade Readiness

**Commit:** `a466c28` · **Python:** 3.11.2 · **Mode:** PAPER only, LIVE hard-disabled

---

## Runtime dependencies (`requirements.txt`, all `==`-pinned)

| package | why |
|---|---|
| `fastapi==0.115.0` | HTTP API + WebSocket (`app/main.py`) |
| `uvicorn[standard]==0.30.6` | ASGI server — the service entrypoint |
| `pydantic==2.9.2` | request/response models (via FastAPI) |
| `requests==2.32.3` | AngelOne SmartAPI REST (`broker/angelone`, `app/connectors/angelone.py`) |
| `pyotp==2.9.0` | TOTP for AngelOne login |
| `websockets==13.1` | AngelOne SmartWebSocketV2 LTP feed (`app/connectors/angel_ws.py`) |
| `python-dotenv==1.0.1` | **optional** — only `python -m app.mcp_server` (dev tool); the service uses systemd `EnvironmentFile` |
| `pytest==8.3.3` | test-only |

Transitive (not pinned directly, pulled by fastapi/uvicorn): `starlette==0.38.6`, `anyio`, `httptools`, `watchfiles`.

Verified: every third-party import in `app/` + `broker/` is one of the above and is installed; `pip freeze` matches the pins exactly; **zero `ModuleNotFoundError`** on a full recursive import (incl. `mcp_server`).

### Deterministic reproduction
```bash
cd backend
python3.11 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python -m pytest -q          # expect: 277 passed
```
No lock file. Direct pins + fastapi/uvicorn's own constraints are the determinism boundary; a full `pip freeze > requirements.lock` is a future option, not a current blocker.

---

## Environment variables

### Required for live market data (service is degraded without them, never unsafe)
| var | purpose |
|---|---|
| `ANGEL_API_KEY` / `ANGEL_CLIENT_ID` / `ANGEL_PASSWORD` / `ANGEL_TOTP_SECRET` | AngelOne SmartAPI auth (candles, quotes, WS feed token) |

### Reporting / alerts (optional — absence disables the feature, not the engine)
| var | purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | daily session report + lifecycle cards |
| `TELEGRAM_SIGNALS_CHANNEL_ID` | legacy signals channel |

### App / auth
| var | default | purpose |
|---|---|---|
| `CHANAKYA_DB_PATH` | `./data/chanakya.db` | sqlite path |
| `CHANAKYA_ADMIN_USERNAME` / `CHANAKYA_ADMIN_PASSWORD` | `admin` / `admin@1234` | browser Basic-Auth (change in prod) |
| `CHANAKYA_API_TOKEN` | unset (open) | if set, all `/api/*` except `/api/health` need `Authorization: Bearer` |

### LIVE-execution gates — **leave unset to keep LIVE disabled**
| var | must be, for LIVE | current |
|---|---|---|
| `CHANAKYA_ALLOW_LIVE` | `1` | unset |
| `CHANAKYA_LIVE_CONFIRM_TOKEN` | non-empty | unset |
| `CHANAKYA_API_TOKEN` | configured | unset |
> LIVE also needs `execution_enabled=true` in runner config. The **autoscalp engine never imports the execution/broker layer at all** — these gates guard the *legacy* SCALP path only. Autoscalp is PAPER by construction.

### Optional overrides (safe code defaults; normally unset — now documented in `.env.example`)
`ANGEL_MASTER_CACHE`, `CHANAKYA_INSTRUMENT_MASTER`, `CHANAKYA_MARKET_WINDOWS`,
`CHANAKYA_MARKET_HOLIDAYS`, `CHANAKYA_MAX_DATA_AGE_SEC`, `OI_HISTORY_DB`, `CHANAKYA_ENV_FILE`.

---

## Database

- **Engine:** sqlite, single file (`CHANAKYA_DB_PATH`, default `./data/chanakya.db`).
- **Migrations:** none external. `db.init_db()` runs `CREATE TABLE IF NOT EXISTS` (full `SCHEMA`) then idempotent `ALTER TABLE … ADD COLUMN` from the `_MIGRATIONS` dict on every boot. An existing DB self-heals; a missing DB is created.
- **Tables the autoscalp engine uses:** `ai_paper_trades`, `scalp_signals`, `live_market_snapshots`, `app_settings` (config, lease, calibration, `autoscalp_report_sent:*`).
- **Test isolation:** `tests/conftest.py` sets `CHANAKYA_DB_PATH` to a tmp file at import time; tests never touch the live DB.
- **Backups:** none automated. `./data/chanakya.db{,-wal,-shm}` — copy while the service is stopped, or use `sqlite3 .backup`.

---

## External services

| service | required? | failure mode |
|---|---|---|
| AngelOne SmartAPI (REST + WS) | for live data | no ticks → aggregators go stale → `self_check.feed_fresh=false` while market open → engine emits `MARKET_CLOSED`/no-trade; **never** an unsafe action |
| Telegram Bot API | optional | cards silently dropped (`notify.push` is fire-and-forget) |

No database server, message broker, scheduler daemon, or cron. The only background worker is the in-process `AutoScalpRunner._loop` (asyncio task started by `/api/autoscalp/arm` or `auto_arm`).

---

## Known deferred dependencies / functions waiting on runtime evidence

| item | class | unblocks when |
|---|---|---|
| `broker_base.py` 13 `NotImplementedError` methods | intentional ABC | overridden by `PaperBroker` / `ShadowBroker` / `AngelOneBroker` — nothing to do |
| NIFTY `max_hold_sec` tuning decision | needs evidence | ≥20 closed NIFTY AUTOSCALP trades with `risk_ref` (`analyze_holdtime.py`) |
| CRUDEOIL block-gate confirmation | needs runtime | next MCX session stamps `BLOCKED[…]` (`crudeoil_block_evidence.sh`) |
| calibration model fit | needs runtime | ≥40 resolved LIVE samples → `status.calibration` non-null |
| live option greeks | intentionally deferred | `analyse_leg` uses its no-greek fallback by design |

---

## Safe extension points for the next upgrade

- **New symbol:** `POST /api/autoscalp/watchlist {"symbol":"X","action":"add"}`. Strike grid auto-inferred (`_sym_meta`); NSE index / MCX / NFO-stock routing is automatic (`_underlying_ref`, `market_data.selection_snapshot`). No code change.
- **Per-symbol tuning:** `config.symbol_profiles[SYM]` (merged over base `strategy`). NIFTY base stays frozen; expiry-day is a separate `expiry_day_profile`.
- **New safeguard:** add a key to `safeguards.DEFAULTS` + a check in `check_entry` returning `(False, reason)`. `entry_blocks` + the `BLOCKED[…]` snapshot stamp pick it up for free.
- **New report metric:** extend `report.session_report()` (read-only) — the endpoint and daily card render whatever it returns.
- **New health check:** add to `report.self_check()` `checks{}`; put it in the `gating` subset only if it's a true fault (not an operator choice / market-closed artifact).
- **Strategy changes:** `engines/scalp_strategy.decide_from_context` — evidence-gated, must not be touched without runtime data + regression tests + rollback.

---

## Operating commands

| action | command |
|---|---|
| test | `cd backend && venv/bin/python -m pytest -q` |
| static compile | `venv/bin/python -m compileall -q backend/app broker` |
| health check | `curl -s -u admin:admin@1234 localhost:7060/api/autoscalp/selfcheck` |
| session report | `curl -s -u admin:admin@1234 'localhost:7060/api/autoscalp/report?day=YYYY-MM-DD'` |
| hold-time analysis | `cd backend && venv/bin/python analyze_holdtime.py NIFTY` |
| CRUDEOIL block evidence | `bash backend/data/crudeoil_block_evidence.sh <IST-day>` |
| restart (app only) | `systemctl restart oi-dashboard.service` — **never** reboot the VPS/OS |
| service status | `systemctl status oi-dashboard.service` |
| logs | `journalctl -u oi-dashboard.service -f` |
| arm / disarm | `POST /api/autoscalp/{arm,disarm}` |
| kill switch | `POST /api/autoscalp/kill` (or `/api/execution/kill`) |

**Service:** systemd unit `oi-dashboard.service`, `ExecStart=venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 7060 --workers 1`, `WorkingDirectory=/root/zerohero/backend`, `EnvironmentFile=-/root/zerohero/backend/.env`. No cron, no timers, single worker (the leader lease assumes one).
