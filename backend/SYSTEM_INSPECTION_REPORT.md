# ZeroHero / Chanakya AI — System Inspection & Architecture Report

Read-only inspection. No code changed, nothing deployed. Every claim below
was verified against the live file/DB/service state on 2026-09-28, not
recalled from memory — file:line citations or the check command are given
throughout. Existing deeper audits are cross-referenced rather than
re-derived (`PHASE0_AUDIT.md`, `CALIBRATION_OVERCONFIDENCE_AUDIT.md`,
`data/research/calibration_fix_2026-09/VERDICT.md`, and the day's own
research reports under `data/research/*_2026-09/`).

---

## 1. Application Overview & Architecture

**What it is**: a FastAPI options-trading research/paper-trading platform for
NSE (NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY), BSE (SENSEX/BANKEX) and MCX
(NATURALGAS/CRUDEOIL). Single process, single systemd unit, one SQLite DB
per subsystem — no microservices, no message queue.

**Process & serving**: `oi-dashboard.service` (`/etc/systemd/system/`) runs
`uvicorn app.main:app --workers 1` on `127.0.0.1:7060`. Nginx
(`/etc/nginx/sites-available/chanakya-dashboard`) reverse-proxies
`chanakya.datacarepoint.com:443` → `127.0.0.1:7060`, plain pass-through, no
caching directives. `app/main.py` (434 lines) mounts ~25 route modules via
`app.include_router`, mostly wrapped in `try/except` so one broken module
prints a warning and disables itself instead of taking the whole app down
(`app/main.py:249-253` is a representative example). The frontend
(`/root/zerohero/frontend/index.html` + `static/js/app.js`, no build step)
is served by the same process; `app/main.py`'s `_spa_html()` rewrites the
`<script src>` query string to each asset's file mtime on every request so a
browser never runs stale JS against fresh HTML.

**Auth**: a single `@app.middleware("http")` gate (`app/main.py:103-112`)
requires either HTTP Basic (`CHANAKYA_ADMIN_USERNAME`/`_PASSWORD`) or a
Bearer token equal to `CHANAKYA_API_TOKEN`; only `/api/health` is exempt.
Fails closed if neither admin credential is set. **Known limitation of this
middleware** (found and fixed this session, see §6): being a classic
`@app.middleware("http")`/`BaseHTTPMiddleware`, it silently drops any
`BackgroundTasks` attached to a downstream response — any *new* route using
`BackgroundTasks` will hit the same bug (`app/hedging/runner.py`'s
`scan_and_record` was moved to `asyncio.create_task` specifically to avoid
this; other route authors should do the same, not `BackgroundTasks`).

**Data ingestion**: `app/connectors/angelone.py` (REST — quotes,
`fetch_candles`, historical) and `app/connectors/angel_ws.py` (WebSocket —
live ticks). Two always-on capture workers, started from `app/main.py`'s
lifespan handler (line 173-186): `histcap.worker.CaptureWorker` (index/
option quote snapshots → `data/market_history.db::quote_snapshots`,
`market_candles`) and `l2capture.worker.L2CaptureWorker` (best-5 depth
snapshots → `data/l2_capture.db::snapquote_ticks`, gated by
`L2_CAPTURE_ENABLED`). **Neither workers, nor Angel One SmartAPI, ever gives
Chanakya/ZeroHero true tick-by-tick trade tape with aggressor
classification** — `snapquote_ticks` is periodic best-5 *resting-order*
depth, not executed-trade prints. This is the concrete reason a real
CVD/footprint/Volume-Profile engine cannot be built from this data (already
established: `orderflow-l2-imbalance-gate` memory; re-confirmed today when a
pasted spec asked for exactly this).

**Execution mode — confirmed PAPER-only**:
- `live_trading: False` is returned by `/api/health` (and by every
  runner's own `/status`) unconditionally.
- Real order placement lives in `app/execution/angelone_broker.py`, gated by
  three independent conditions, ALL required (`app/execution/angelone_broker.py:8-10,55,65,73-76`):
  `config["execution_mode"] == "LIVE"` AND env `CHANAKYA_ALLOW_LIVE == "1"`
  AND a non-empty `CHANAKYA_LIVE_CONFIRM_TOKEN`. Verified live: neither env
  var is set in `.env` (`grep -c` = 0 for both) — the gate is closed.
- A **separate, global** `execution_kill_switch`
  (`app/execution/killswitch.py`) independently blocks all new entries
  (`app/autoscalp/safeguards.py:105` — the very first check in
  `check_entry()`) regardless of the triple-gate above. **This was found
  ACTIVE (blocking every engine's entries) on 2026-09-21, apparently toggled
  on via the dashboard with no recorded reason, and left on for 3 days
  before being noticed and turned off on 2026-09-24 this session**
  (`user_confirmed_2026-09-24_resume_trading` — current live value, checked
  just now: `{"active": false, ...}`). No code bug — a real operational
  incident, now resolved, worth a standing reminder to check this switch
  after any "why isn't anything trading" report.

---

## 2. Strategy Inventory & Core Logic Audit

Two genuinely independent, currently-**ARMED** autonomous engines exist side
by side, plus one armed-by-user autonomous hedging engine, several
shadow/read-only layers, and a long tail of dormant research code.

### 2a. AutoScalp (`app/autoscalp/runner.py`) — **ARMED** (`autoscalp_armed=1`)
- **Pipeline**: `app/engines/sr_engine.py` (S/R zones, ATR, VWAP, GEX) →
  `app/engines/regime_mtf.py` (regime label + MTF alignment) →
  `app/engines/state_classifier.py` (`_W` weights, `state_classifier.py:26-27`:
  price_action 0.20, level_strength 0.16, volume 0.12, oi 0.12, + others) →
  `app/engines/option_engine.py::select_option()` (leg scoring) →
  `app/engines/scalp_strategy.py::decide_from_context()`.
- **Blend formula** (`scalp_strategy.py:424`, verified verbatim):
  `blended = 0.62*state_score + 0.24*final_quality + 0.14*min(100, mtf_magnitude+40)`,
  then regime/signal-type/time-of-day multipliers, then
  `_score_to_prob()` maps the 0-100 blended score to a win probability via
  `app/backtest/calibration.py::predict()`.
- **Calibration history (K8)**: overconfidence found (`CALIBRATION_OVERCONFIDENCE_AUDIT.md`),
  root-caused to a forced `k>=1.0` floor + 3-bucket extrapolation, fixed
  2026-09-21 (`data/research/calibration_fix_2026-09/VERDICT.md`) — floor
  removed, `_MIN_SPREAD` raised 3→5, sample-size shrinkage added. **Re-verified
  live today (2026-09-28)**: current curve `{"version":"live-2026-09-28-n156","global":{"k":0.0,"b":-0.0177,...}}`
  — still flat/honest. But only 2 new LIVE resolved signals exist since the
  kill-switch was cleared (2026-09-25), both with honest ~50% stated
  probability — the fix is behaving correctly, but there is not yet enough
  fresh data to certify the *live* system's calibration quality going
  forward (`/api/autoscalp/calibration-report` still shows `OVERCONFIDENT`
  because it pools all 184 historical resolved trades, 182 of them
  pre-dating the fix — a stale aggregate, not a new defect).
- **Risk/exit**: `app/engines/risk_engine.py` sizes by `capital*risk_pct /
  per_unit_risk`, floored at 0 (rejects if computed qty ≤ 0,
  `risk_engine.py`'s own `allowed_qty<=0` branch), and gates on `rr_min`
  (default 1.0, `risk_engine.py:68`) when `target_1` is supplied.
- **Safeguards** (`app/autoscalp/safeguards.py::check_entry`): kill-switch
  first, then weekend/holiday, then operator time window, then per-symbol
  config (`max_consecutive_losses`, spread/premium/feed-age limits).

### 2b. ScalpRunner (`app/scalper.py`) — **ARMED** (`runner_armed=1`)
A **second, structurally separate** autonomous engine — different scoring
(`app/engines/scalp_engine.py`, 3 deterministic setups: `VWAP_RECLAIM`,
`EMA_PULLBACK`, `MOMENTUM_BREAK`, priority-ordered), different pipeline
(`app/scalp_pipeline.py`), own `/api/scalp/*` routes, own Telegram cards
(`SCL-*` signal-id prefix vs AutoScalp's `SIG-*`). This engine ran unaudited
for the entire session until a merge-audit fork surfaced it. Two real,
now-fixed bugs found and shipped this session:
1. **Symboltoken never set** (`app/pipeline_core.py::open_from_contract`)
   → `ScalpRunner`'s `_ltp_for()` price-monitor loop had no reliable way to
   mark 2 real INDEX-scalp positions, which sat OPEN 3 days past their own
   target/stop before being manually closed. Fixed: `_resolve_symboltoken()`
   now resolves a real token for INDEX/FUTURE contracts via
   `app.instruments.resolve()`.
2. **`scalp_pipeline.py` never passed `target_1` into the risk-engine call**
   (`app/scalp_pipeline.py`, fixed commit `ad9d289`) → `risk_engine.py`'s own
   `rr_min` gate (§2a above) silently never evaluated a single ScalpRunner
   signal, ever, because `target` was always `None`. Verified this doesn't
   change current live behavior: default ATR multiples (`atr_target_mult=0.6`,
   `atr_stop_mult=0.5`, `scalp_engine.py:89,91`) yield R:R 1.2, already
   above the default `rr_min=1.0`.
- **Exit management**: `app/engines/paper_trading.py::update_trade_price` —
  breakeven-at-1R and a profit-lock ratchet, target/stop/time-stop
  (`max_hold_sec`, configured `45`s currently in the live `scalp_config`).
  A third bug class was **audited and found NOT to exist**: a pasted
  "developer prompt" this session claimed the breakeven-tightening
  comparison (`paper_trading.py:195-197`) was inverted; read against real
  logic it is correct (`entry > new_sl` for a BUY does raise the stop toward
  entry) — the proposed "fix" was a no-op refactor. Documented as a
  discarded false-positive, not a fix.
- **New defensive guard added this session**: `open_trade()` now rejects
  `quantity<=0` / `entry<=0` (real gap found: `/api/positions`'s
  `TrackPositionRequest.lots` has no `>0` Pydantic constraint, and
  `req.lots or 1` only substitutes on `0`/`None`, not on a negative value).
  Deliberately does **not** require `stop_loss` — `app/combos.py` tracks
  legs with no per-leg stop by design (combo-level exit instead), confirmed
  by real passing tests that would break under a blanket requirement.

### 2c. Hedging Engine (`app/hedging/`) — **ARMED by user** (`hedging_armed=1`), built this session
- `primary_selector.py`: picks the SOLD leg — largest open-interest wall
  ≥1.5% OTM, either side, direction-agnostic (deterministic, explicitly
  **not backtested**, same disclosure style as the rest of the module).
- `selector.py` (pre-existing, Phase 1): given that primary leg, evaluates
  every further-OTM strike on the real chain (liquidity/spread/cost-vs-credit/
  risk-reduction) and picks the cheapest accepted protective candidate.
- `capital.py`: two independent lot-sizing caps (risk-budget and
  available-capital), tighter one wins, never invents a lot count.
- `runner.py::scan()`: connects the above into one PAPER open/close cycle,
  run by cron (`scripts/hedging_scan_cron.sh`, `*/5 8-15 * * 1-5`) rather
  than a live WS worker — a deliberate lower-risk choice over adding a new
  always-on connection to `main.py`'s lifespan.
- **Real bug found+fixed this session**: `POST /scan-now` originally used
  FastAPI's `BackgroundTasks`, which — per §1's middleware note — the app's
  own auth gate silently drops; the scan was queued but never ran via the
  API path (confirmed live: direct function call completed and recorded in
  ~41s, the same call via the API endpoint never wrote a result). Fixed with
  `asyncio.create_task(asyncio.to_thread(...))`, verified live post-fix
  (`recorded_at` timestamp now matches the trigger time).

### 2d. Shadow / read-only layers (no order path)
- **`app/optionchain/`**: `QUALIFIED|WATCH|NO_TRADE` structure gate from
  real captured/keyless-fallback chain data (OI walls, max-pain, PCR, GEX).
  Not called by any of the armed engines above — informational only.
  `qualified_watch.py` (built today) polls this every 5 min via cron and
  pushes a Telegram alert the moment any watched symbol flips to
  `QUALIFIED`, explicitly labeled as structure-only (no entry/SL/target of
  its own).
- **`app/hcs/`**: a quality/veto layer *on top of* `scalp_strategy`'s
  `decide_from_context()` output — adds hard vetoes, never upgrades a
  decision. Calibration explicitly marked NOT VALIDATED in its own memory
  record.
- **`app/premium_decoupling/`** and **`app/sl_hit_analysis/`** (built
  today): descriptive, cron-driven (5 min) classifiers over already-captured
  ticks / already-closed trades. Neither writes to, nor is read by, any
  order-path code.

### 2e. Confirmed dormant
- `app/smart_index_scalper/`: `ARMED_KEY = "smart_scalper_armed"`
  (`scheduler.py:39`) — live value `None` → **disarmed**.
- `app/liquidity_sweep/`, `app/index_signal_research/`,
  `app/reverse_engineering/`, `app/research_strategy/`,
  `app/research_engines/`: **not imported anywhere in `app/main.py`** —
  verified by grep, zero hits. Pure dormant code + past NO-GO findings on
  disk (`liquidity_sweep`: NOT READY; `index_signal_research`: 0/8
  hypotheses passed).
- Standalone engine files (`app/engines/... ` — no `ssl_hybrid_pro_engine.py`
  or `stochrsi_supertrend_engine.py` actually present in the current
  `app/engines/` listing; those NO-GO reports live only under
  `data/research/ssl_hybrid_pro/` and are not wired as importable modules —
  worth noting the source files themselves may have been removed after the
  verdict, only the report survives).
- `app/structural_break/`, `app/institutional_edge/`, `app/microstructure/`:
  **do** have live, read-only API routes mounted (`main.py:280,289,299`),
  but none of the three feeds any gate or the order path — on-demand
  analytics only.

### 2f. This session's own strategy-hypothesis testing (all NO-GO/marginal)
Five hand-designed rules were backtested on real data today, none cleared a
realistic cost estimate: Key-Level Reversal Sweep (NO-GO), VWAP+9EMA
Pullback Scalp (data too thin to call), False-Breakout Reversal PDH/PDL+ORB
(marginal, fails at ~1pt cost), CPR+ORB (NO-GO, fails at ~1pt cost), Volume
Profile/Order-Block (**cannot be tested at all** — confirmed today, both
real historical NIFTY and BANKNIFTY 1-min datasets have zero usable volume
data). Full detail in `data/research/{reversal_sweep,vwap_ema_scalp,
false_breakout_reversal,cpr_orb}_2026-09/`.

---

## 3. Index-wise & Asset Configuration (Calibration)

**Governed-config system**: `app/instrument_profiles.py` defines a
`Param(value, status, source, rationale)` wrapper — every tunable carries
its own `ParamStatus` (`VERIFIED`/`UNVALIDATED`/`DEFAULT`/`UNKNOWN`), so a
guessed number can never silently masquerade as a checked one. Verified lot
sizes (all `ParamStatus.VERIFIED`, sourced from the live AngelOne
instrument master): NIFTY 65, BANKNIFTY 30, FINNIFTY 20 (typical),
NATURALGAS 1250, an MCX contract at 100 — an unmapped symbol correctly
returns `lot_size=Param(None, ParamStatus.UNKNOWN, ...)` rather than a
fabricated default (`instrument_profiles.py:273`).

**Token/strike resolution**: `app/instruments.py::resolve()` (generic
index/future lookup) / `resolve_nse_option()` / `resolve_mcx_future()` /
`resolve_index_future()` (BFO/SENSEX-aware, fixed earlier this session).
ATM selection is per-engine, not centralized: `option_engine.py::select_option()`
takes an explicit `atm` argument and a `strike_window`
(`autoscalp_config.strike_window`, live value `2`) rather than recomputing
ATM itself.

**Real, calibrated cost model exists for exactly two contracts**:
`app/institutional_edge/costs.py`'s `_PROFILES` dict has entries only for
`("MCX","NATURALGAS_OPTION")` and `("MCX","CRUDEOIL_OPTION")`
(`costs.py:65,75`). Any other `(exchange, segment)` pair —
**including NIFTY and BANKNIFTY options** — returns
`CostEstimate(status="UNCALIBRATED", ...)`. This is the same reason every
NIFTY/BANKNIFTY backtest this session (both the pre-existing ones and
today's 5 new hypothesis tests) had to use a disclosed *estimated* round-trip
cost rather than a verified one — it is a real, structural gap in this
codebase's calibration coverage, not an oversight in any one script.

**Per-index parameter presets are NOT uniformly calibrated.** Several
pasted specs today asserted specific numbers (e.g. "BankNifty Premium SL
15-20pts, Volume Multiplier 1.8x") with no backing evidence; none of those
were adopted. The only real, evidence-backed per-symbol tuning that exists
today is the `symbol_profiles` block inside the live `autoscalp_config`
(per-symbol `max_hold_sec`, `sl_atr`/`t1_atr`, `block_signal_types`,
`require_min_confidence`) — itself derived from real backtests, not
asserted defaults.

---

## 4. Risk Management & Safety Guardrails

- **Position sizing** (`risk_engine.py`): `qty = floor(capital*risk_pct/100 / per_unit_risk)`,
  floored to whole lots, capped again by `available_margin/entry`; rejects
  (`REJECTED`, not a fabricated small size) on zero/negative stop distance,
  wrong-side stop, or `qty<=0` after sizing.
- **Daily/session limits**: `max_daily_loss_pct`, `max_consecutive_losses`,
  `max_trades`, `max_open_positions` all read from `limits` in the request,
  each with a hardcoded sane default if omitted (`risk_engine.py:35-38`) —
  never silently `None`.
- **Kill-switch policy** (`app/execution/killswitch.py`): two explicit
  policies, `MONITOR` (alert only, default) and `FLATTEN` (may submit market
  exits for confirmed LIVE positions only) — an emergency exit is never
  implicit.
- **Time/calendar gates**: `app/autoscalp/safeguards.py` (operator window,
  weekend, `app/market_calendar.py::is_holiday()`); `app/hedging/position.py`'s
  own `DEFAULT_EXIT_CUTOFF_IST = 15:15` time-stop.
- **Development-safety net** (not a trading guardrail, but load-bearing for
  everything above staying trustworthy): `scripts/run_tests.sh` forces
  `TEST_DATABASE_URL` to a throwaway tmp file (`run_tests.sh:25`) plus a
  `pytest_configure` abort-on-live-DB-path check — the reason this session's
  ~1780 tests could be run repeatedly today without any risk to
  `data/chanakya.db`.

---

## 5. System Workflow & Notifications

- **Logging**: structured JSON lines to the systemd journal (`journalctl -u
  oi-dashboard.service`, e.g. `{"evt":"greeks_capability",...}` lines seen
  live), plus durable DB trails — `ai_signals_log` (every contract, approved
  or not), `ai_paper_trades` (lifecycle), `paper_trade_events` (OPEN/BREAKEVEN_SET/
  CLOSE audit trail per trade, `paper_trading.py::db.insert_paper_trade_event`).
- **Telegram**: `app/telegram_dispatcher.py` is the single choke point (per
  its own docstring, replacing 5 previously-independent call sites found in
  the Phase 0 audit) — tags every card with `source_engine`, dedups, and
  arbitrates when two engines would announce the same underlying at once.
- **No unified backtesting engine exists.** `app/backtest/calibration.py`
  is specifically the probability-curve fitter for AutoScalp (bucket→
  logistic fit with shrinkage), not a general-purpose backtester. Every
  strategy hypothesis in this codebase — the pre-existing ones
  (`ssl_hybrid_pro`, `stochrsi_supertrend`, `trend_swing`, `tri_compare`,
  ...) and today's five (`reversal_sweep_2026-09`, `vwap_ema_scalp_2026-09`,
  `false_breakout_reversal_2026-09`, `cpr_orb_2026-09`) — is a standalone,
  purpose-built script under `data/research/<name>_<date>/`, each with its
  own data loading, simulation loop, and a `VERDICT.md`/inline report. This
  is a real architectural gap: there is no shared, reusable backtest harness
  (candle loading, cost model wiring, train/test split, report format) —
  every new hypothesis re-derives its own plumbing, which is exactly why
  today's studies each took real engineering time before producing a
  result, and why a real bug (signal-overlap double-counting, fixed
  mid-session) was independently possible in more than one of them.

---

## 6. Findings & Recommendations

**Already fixed this session, verified still holding (not new findings, listed for completeness):**
- Kill-switch left ON 3 days (§1) — cleared, verified.
- ScalpRunner symboltoken gap (§2b) — fixed, verified via new tests.
- `scalp_pipeline` never passing `target_1` to the risk gate (§2b) — fixed.
- `open_trade()` missing qty/entry guard (§2b) — fixed, scoped to not break `combos`.
- Hedging `/scan-now` BackgroundTasks silently dropped (§2c) — fixed.
- A pasted "bug report"'s breakeven-logic claim — audited, found FALSE, correctly not applied.

**Genuinely new observations from this inspection pass:**

1. **The auth-middleware BackgroundTasks gotcha (§1) is a standing landmine, not a one-off.** It was found once (Hedging `/scan-now`) and fixed there specifically, but nothing prevents a future route from reintroducing it. Recommend: a one-line comment on `_auth_gate` itself (`app/main.py:103`) warning future authors, since the failure mode (task silently never runs, no exception) is unusually hard to notice without exactly the kind of nginx-access-log correlation this session had to do.
2. **No shared backtest harness (§5).** Given how many one-off `data/research/*/backtest.py` scripts now exist with materially similar shape (load real candles → resample → level detection → simulate SL/TP/timeout → report), a small shared library (candle loader with format auto-detection, a `simulate_trade()` walker, a standard report/verdict shape) would have caught today's signal-overlap bug once instead of needing the same fix applied to two separate scripts by hand.
3. **Cost-model coverage (§3) is the actual bottleneck behind most "marginal" verdicts**, not the strategies' underlying signal quality — NIFTY/BANKNIFTY options are UNCALIBRATED in `institutional_edge/costs.py`, so every NIFTY-specific backtest (including today's five) is forced to use a disclosed *estimated* cost rather than a real one. Calibrating a real NIFTY/BANKNIFTY options cost profile (even an approximate one, contract-note-derived) would sharpen every future verdict on these two symbols specifically, which is where most of this session's research volume has gone.
4. No new security/correctness bug was found beyond the above during this pass — the codebase's existing discipline (Param/ParamStatus provenance tracking, fail-closed auth, `run_tests.sh`'s DB-isolation guard, the triple live-execution gate) held up under direct verification.
