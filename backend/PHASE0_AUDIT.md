# Phase 0 — Complete ZeroHero Audit

Read-only inventory. No code changed, nothing deleted, nothing stopped/restarted.
Produced per the "ZEROHERO — FINAL PRODUCTION ARCHITECTURE, CLEANUP & RESEARCH-GRADE
ADAPTIVE SIGNAL INTELLIGENCE UPGRADE" brief, section 2 (Phase 0) / section 31 (Phases 1-2).

Safety confirmed unchanged throughout this audit: `live_trading=False` /
`paper_mode=True` hardcoded in `autoscalp/runner.py`; `smart_index_scalper`'s
scheduler defaults DISARMED (DB-backed flag); no order-placement tool exists
in `mcp_server.py`; killswitch present in `app/execution/killswitch.py`.

---

## 0. Critical context: this VPS is SHARED, not dedicated to ZeroHero

`df -h /`: **99G total, 82G used, 13G free (87% full)** — tight headroom, the
single most urgent risk found in this audit.

`/root` hosts **~15 unrelated applications** besides zerohero, several of them
confusingly also named "chanakya-something" (a different, older, separate
codebase — NOT this repo):

| Dir | Size | What |
|---|---|---|
| openmythos | 7.9G | unrelated |
| **zerohero** | **5.8G** | **this repo** |
| chanakya_v5 | 5.5G | different app, own venv, `chanakya.service` (gunicorn :5000) |
| ai_trading_real | 2.8G | different app |
| llm-local | 2.7G | zerohero's own on-demand llama.cpp server (docs: :8080, not running now) |
| ai_trading_backup | 2.7G | backup tree |
| oi_dashboard | 2.0G | (name collision with zerohero's own systemd unit — different dir) |
| idaddy_ai | 1.7G | different app |
| vibe | 1.5G | different app, `vibe-web.service` |
| openalgo | 1.2G | installed per an earlier session, **not currently running** (no service/process) |
| angleone | 957M | different app |
| production_cutover_backups | 869M | backup tree |
| SEBI-RA-Workstation | 782M | different app (cloned earlier this session's history) |
| codex-ollama-proxy | 569M | LiteLLM translation proxy, `codex-ollama-proxy.service` (active, :11435) |

Plus 40+ dated `chanakya_*.tar.gz` snapshots (2.8-3.7M each) and full backup
trees (`chanakya_v5.backup` 298M, `chanakya_ai_old_20260829_232444` 145M,
`ai_trading_backup_v2` 135M) — an accumulate-and-never-clean pattern, all in
**other apps' directories, not zerohero's**.

**Two crash-looping unrelated services**: `chanakya-natgas.service` and
`camera-monitor.service` (both in permanent `activating (auto-restart)` /
crash state) — neither belongs to zerohero, but they're generating restart
churn on a shared, disk-tight box.

**Implication for every later cleanup phase**: any disk/service/package
cleanup must verify a candidate belongs to zerohero specifically before
touching it. Filenames containing "chanakya" are NOT a safe signal — most of
them belong to a different, older application on this same box.

---

## 1. Backend architecture map

**Entrypoint**: `app/main.py` — FastAPI composition root. CORS
(`CHANAKYA_CORS_ORIGINS`, default `https://chanakya.datacarepoint.com`) +
opt-in bearer/basic auth gate (`CHANAKYA_API_TOKEN` or
`CHANAKYA_ADMIN_USERNAME`/`CHANAKYA_ADMIN_PASSWORD`, default `admin`/
`admin@1234`) on every `/api/*` route except `/api/health`. Single `/ws`
WebSocket endpoint (`ConnectionManager` in `app/runtime.py`).

**Routers**: 20 router modules, **110 `@router.get/post` routes total**:
- `app/api/*.py` (10 modules): engines, instruments, analysis, scalp,
  execution, monitor, autoscalp, positions, data, system — the original
  monolithic route set, one module per logical group.
- 10 further modular subsystem routers, each wired in `main.py` inside its
  own `try/except` (so one subsystem failing to import never takes the app
  down): `histcap`, `greeks_engine`, `mathematical_confluence`,
  `smart_index_scalper`, `orderflow`, `research_strategy`, `hcs`,
  `signals_dashboard`, `optionchain`, **`structural_break`** (this session's
  new layer).

**Background workers/schedulers started at app startup** (`main.py`
`@app.on_event("startup")`):
- `runtime.scalp_runner.start()`, `runtime.autoscalp.start()` — the two live
  (paper) decision loops.
- `histcap.worker.CaptureWorker` — market-data capture.
- `l2capture.worker.L2CaptureWorker` — no-op unless `L2_CAPTURE_ENABLED=1`.
- `smart_index_scalper.scheduler.SCHEDULER` — DISARMED by default (DB flag).

**Database**: single SQLite file `backend/data/chanakya.db` (475M, resolved
via `app/db.py`'s `LIVE_DB_PATH`, overridable by `CHANAKYA_DB_PATH` — the
existing test-isolation mechanism). 12 tables: `ai_signals_log`,
`ai_paper_trades`, `app_settings`, `tp_predictions`, `broker_orders`,
`order_events`, `scalp_signals`, `live_market_snapshots`,
`trade_entry_features`, `trade_exit_outcomes`, `smart_scalper_signals`,
`smart_scalper_states`. Separate dedicated DB files for other subsystems
(deliberate isolation, not an oversight): `market_history.db` (3.86G,
histcap captures), `l2_capture.db` (506M), `expiry_z2h.db` (3.4M),
`monitor.db` (49K), and this session's own `structural_break.db` (not yet
created on disk — created lazily on first write).

**Stray/dead DB files found**: `/root/zerohero/data/chanakya.db` (4096
bytes) and `/root/zerohero/data/market_history.db` (0 bytes) — a duplicate
`data/` directory one level up from `backend/data/`, NOT on the path
`db.py` actually resolves (`LIVE_DB_PATH` is relative to `app/db.py`'s own
location, i.e. always `backend/data/`). These two files are empty/near-empty
and unreferenced by the app — safe ARCHIVE/REMOVE candidates for a later
phase, flagged here rather than deleted now.

**Tests**: 87 backend test files (934 tests passing as of this session's
last full run), executed via `backend/scripts/run_tests.sh` with DB-path
isolation (`TEST_DATABASE_URL`/`CHANAKYA_DB_PATH` → temp file, inode/size
of the live DB asserted unchanged after the run).

---

## 2. Signal engines map

**Live decision path** (imported by `app/autoscalp/runner.py`, the one
engine actually placing PAPER trades):
`engines/scalp_strategy.py` (`decide_from_context`) →
`engines/sr_engine.py` (S/R) + `engines/state_classifier.py` (BULLISH/etc) +
`engines/regime_mtf.py` (`detect_regime`, `mtf_alignment`) +
`engines/option_engine.py` (leg analysis, CE/PE confirmation, EV gate,
option selection) + `engines/oi_math.py` (max-pain). `engines/paper_trading.py`
executes the (paper-only) open/update/close.

**Standalone/research/shadow engines** (not imported by the live runner —
each independently valid, deliberately dark or research-only, not dead
code):
- `engines/oi_options_engine.py`, `engines/risk_engine.py`,
  `engines/scalp_engine.py` — older/alternate engines, referenced by other
  route modules (`app/api/engines_routes.py` etc), not by the live loop.
- `engines/ssl_hybrid_pro_engine.py`, `engines/stochrsi_supertrend_engine.py`
  — this session's earlier NO-GO research (see memory `ssl-hybrid-nogo.md`,
  `stochrsi-supertrend-nogo.md`) — validated dead ends, kept for the record.
- `engines/turning_point_engine.py` — the Chanakya Turning-Point engine
  (memory: gates default OFF).

**Independent subsystems** (each its own package under `app/`, each with
its own `api.py` unless noted):
- `optionchain/` — data layer + analytics (max-pain/PCR/GEX/skew) +
  structure/qualify gate + `history_analytics.py` (added this session,
  separately, before structural_break).
- `greeks_engine/` — derived Greek exposure over captured broker Greeks.
- `mathematical_confluence/` — pivots + Gann + OI confluence ("Math
  Scalper").
- `smart_index_scalper/` — ranks the index universe over the confluence
  engine, own paper engine + scheduler (DISARMED default).
- `hcs/` — High-Confidence Signal meta-engine (SHADOW re-scorer, memory:
  calibration NOT VALIDATED).
- `orderflow/` — Volume Profile / Market Profile / TPO / smart-money,
  own `notify.py` broadcaster (see §4).
- `expiry_zero_to_hero/` — expiry-day premium-blowup research engine.
- `research_engines/` — four independent research trees (`orderflow`,
  `order_pressure`, `trend_swing`, `tri_compare`) — all prior sessions'
  hypothesis tests, several concluded NO-GO per existing memory.
- **`structural_break/`** — this session's new Adaptive Model layer
  (drift/performance/prediction monitors → explainable break-score state
  machine → shadow validator → regime-profile registry → audit log →
  read-only API → backtest comparison). Dark: not called by the live
  runner, `Safeguards.set_external_halt()` exists but nothing invokes it
  yet outside this layer's own tests.

**Execution layer** (`app/execution/`): `broker_base.py`,
`angelone_broker.py`, `paper_broker.py`, `shadow_broker.py`,
`order_manager.py`, `reconciler.py`, `trade_monitor.py`, `killswitch.py`,
`idempotency.py`, `ratelimit.py`, `staleness.py`, `audit.py` — the real
order-lifecycle machinery (memory: LIVE triple-gated OFF, PAPER default).
Untouched by this or any recent session's engine work.

---

## 3. Frontend map (full findings from the dedicated audit)

Single-page app: one `index.html` (13 views), one monolithic
`static/js/app.js` (2865 lines, no other JS modules), one `style.css` (674
lines). One WebSocket connection (no SSE), dispatching on `msg.type` for
live feed updates + selective view refresh.

All 13 views are wired to real, existing backend routes **except**:
- **`/api/structural-break/*` and `/api/histcap/*` have zero frontend
  callers** — both routers exist and work, neither has any UI.
  (Expected for structural_break, built this session with no UI task in
  scope; histcap appears to have always been backend-only.)

**Duplication flagged**: three separate scalping UI surfaces exist
side-by-side — legacy `view-scalp` (`/api/scalp/*`), `view-autoscalp`
(`/api/autoscalp/*`), and `view-mathscalp`/`smart_index_scalper`
(`/api/smart-scalper/*`, `/api/mathematics/*`) — each with its own
status/table rendering, not consolidated.

Mobile bottom-tab bar surfaces only 6 of 13 views; the rest are
desktop-sidebar-only (may be intentional information density choice, not
confirmed dead code).

Tests: 3 Node scripts (`render_smoke`, `focus_combo`, `live_monitor`), no
framework, DOM/render smoke coverage + XSS-escaping checks + request-
sequencing regression checks.

---

## 4. Telegram map (full findings from the dedicated audit)

**Single sender**: `app/connectors/telegram.py` (`_send()` → one
`requests.post` to the Telegram Bot API). Single bot+chat pair in `.env`
(`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, + optional
`TELEGRAM_SIGNALS_CHANNEL_ID` override). No inbound polling/webhook
anywhere in the app.

**But at least 5 independent call sites trigger it**, each with its own
format and its own (or no) dedup logic — exactly the duplication the mega-
brief's section 19 wants consolidated:
1. `pipeline_core.log_and_notify()` — shared by `orchestrator.py` and
   `scalp_pipeline.py`, on `final_decision == "APPROVED"`.
2. `engines/paper_trading.py.close_trade()` — fires on every paper-trade
   close, shared by every engine that opens paper trades.
3. `app/scalper.py`'s own monitor loop — **6 distinct ad-hoc raw-HTML
   sends** (wrong-side alert, target/stop-hit, strangle combo, S/R
   reversal, turning-point signal, order-adapter alert), bypassing the
   shared card builders entirely.
4. `autoscalp/notify.py` + `runner.py:push()` — the "IDADDY AI SIGNAL"
   card, with its own confidence/dedup gating.
5. `orderflow/notify.py.push_new_signals()` — smart-money breakout card,
   invoked only from a 5-min cron script, own separate dedup key.

Plus several cron scripts calling the same connector or raw `curl`
independently of the FastAPI process.

**Concretely double-sourced**: `scalper.py`'s target/stop-hit alert (while
still open) and `paper_trading.close_trade()`'s close alert both fire for
the *same* underlying position lifecycle, from two different call sites.

**Three inconsistent message formats** across the above (inline HTML,
"IDADDY AI SIGNAL" bar-box, "ORDERFLOW SIGNAL" bar-box) — no shared schema.

This is the clearest, most concrete, lowest-risk win available in the whole
mega-brief (section 19's canonical dispatcher) — consolidating reduces
noise without touching any signal-generation logic.

---

## 5. AI / local-model map

- **Ollama**: `ollama.service` active, `127.0.0.1:11434`, 8 models pulled
  (~12G total: qwen3-mini, qwen3:1.7b, qwen3-codex, qwen2.5-coder:1.5b/3b,
  qwen-codex, tinyllama, gemma2:2b). Nothing in the zerohero backend code
  itself calls Ollama directly (confirmed via grep — zero hits in
  `app/**/*.py`).
- **codex-ollama-proxy.service** (active, :11435, 569M on disk) — the
  LiteLLM translation proxy from memory `codex-ollama-local.md` (built to
  bridge Codex's Responses-API expectation to Ollama's `/v1/chat/completions`
  shape). Its presence as an *active* systemd service suggests the earlier
  "turns still 500" problem noted in memory may since have been resolved —
  worth a quick functional recheck before assuming it's fixed.
- **llama.cpp local server** (memory `llamacpp-local.md`, `/root/llm-local`,
  Qwen3-4B Q4_K_M): **not currently running** (no :8080 listener, no
  matching process) — matches its documented "start on demand" design, not
  a fault.
- **No Claude/Anthropic API usage inside the backend app itself** — the
  only Claude-adjacent file is `app/mcp_server.py`, which exposes Angel One
  data/analysis as MCP *tools* for an external Claude client to call (no
  order-placement tool), not the reverse.
- No XGBoost training/inference code found in the backend (matches the
  mega-brief's own "Do not train XGBoost yet" instruction — nothing to
  audit here yet).

---

## 6. System map (full findings from the dedicated audit)

See §0 for the full shared-VPS picture. Summary specific to zerohero:

- **zerohero's only systemd unit**: `oi-dashboard.service` (active,
  enabled) — `uvicorn app.main:app --port 7060`, WorkingDirectory
  `/root/zerohero/backend`. This is the live process.
- **Cron** (root crontab): zerohero-owned jobs live under
  `backend/scripts/`, running at fixed IST times or every 5 minutes during
  market hours (`market_map_daily_summary.sh`, `orderflow_signal_watch.sh`,
  `stale_feed_watchdog_cron.sh`, `orderflow_h1h7_perf_cron.sh`,
  `check_calibration_subgroups.sh`, `l2_capture_health_cron.sh`,
  `trending_up_haircut_monitor_cron.sh`, `monitor_market_map.sh`) — all
  read-only monitors/notifiers, none place orders.
- **Docker**: installed, running, but zero zerohero images/containers
  (both containers found — `n8n`, `ai_trading_real-redis-1` — belong to
  other apps).
- **OpenAlgo**: present at `/root/openalgo` (1.2G), **no running
  service/process found** — installed per an earlier session
  (`stochrsi-supertrend-nogo.md` memory), currently idle. Confirms it's a
  real removal candidate per mega-brief section 5, but needs an explicit
  go/no-go decision first (still may be wanted for future multi-broker
  paper testing — that decision belongs to the user, not this audit).
- **Ports actually in use by zerohero**: 7060 only, confirmed. Memory's
  documented :8888 (turning-point engine) and :8080 (llama.cpp) are
  currently NOT listening — both are on-demand/optional per their own
  design, not evidence of breakage.
- **19 separate virtualenvs** on the box, one per project — zerohero's own
  (`backend/venv`) is normal and required; the other 18 belong to unrelated
  apps and are out of scope for zerohero cleanup.

---

## 7. Classification summary (KEEP / MERGE / DISABLE / ARCHIVE / REMOVE candidates)

Per the mega-brief's own instruction ("Remove only after dependency
verification... prefer ARCHIVE before permanent deletion when uncertainty
exists"), nothing below has been touched — this is a candidate list for the
user to approve before any Phase 5+ action:

| Candidate | Evidence | Suggested classification |
|---|---|---|
| `/root/zerohero/data/chanakya.db` + `market_history.db` (stray, 4K/0 bytes) | Not on `db.py`'s resolved path, empty | REMOVE (near-zero risk, but confirm first) |
| Telegram: 5 independent broadcasters, 3 message formats | §4 | **DONE (2026-09-12)** — `app/telegram_dispatcher.py` built + all 5 in-scope "new signal" call sites (pipeline_core, autoscalp entry, orderflow smart-money, scalper.py reversal-scan + turning-point) now route through it for cross-engine agreement/conflict + dedup. Lifecycle/exit alerts deliberately untouched (see module docstring). 949/949 tests passing. |
| `/root/openalgo` (1.2G, idle) | §0, §6 | Needs a user decision: keep for future multi-broker paper testing, or REMOVE |
| `~/.cache/pip` (448M), `~/.npm` (1.2G) | System audit §4 | Safe to clear (reclaimable caches), but these are user-wide, not zerohero-specific — check impact on other projects first |
| `engines/ssl_hybrid_pro_engine.py`, `engines/stochrsi_supertrend_engine.py` | Confirmed NO-GO in memory, not imported by live runner | ARCHIVE (keep for the record per this session's own established practice, don't delete research history) |
| `/api/structural-break/*`, `/api/histcap/*` frontend gap | §3 | Not a defect — no UI task was ever scoped for either; note for Phase 14 (frontend/research dashboard) if the user wants one |
| Other apps' crash-looping services, backup trees, tar.gz snapshots | §0 | **Out of scope for zerohero** — flagged only because they affect shared disk headroom; not zerohero's to clean up without the user's explicit say-so, since they belong to different applications |

---

## What Phase 0 deliberately did NOT do

No file was moved, deleted, disabled, or reconfigured. No service was
stopped or restarted. No package was removed. This document is the
"architecture/dependency map... BEFORE modifying anything" the brief's
Phase 0 asks for — the next step is the user's call on which Phase 1-16
item (per the brief's own section 31 ordering) to actually act on, since
several of the biggest wins here (Telegram consolidation, OpenAlgo
removal, stray DB cleanup) are cheap and low-risk, while VPS-wide disk/
service cleanup necessarily touches other people's applications on a
shared box and needs explicit scope confirmation first.
