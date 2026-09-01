# Prioritised Remediation Plan

**Created:** 2026-09-02 · **Mode:** verification-first. **Nothing here is implemented.**
Each item is a proposal; **no work starts without your explicit per-item approval.**

## Hard constraints (apply to every item)
- No live-money trading. `paper_mode=true`, LIVE hard-off.
- No production trading-logic / threshold / broker-order / kill-switch change.
- No new AI influence. No Vega / Options-Greeks implementation.
- No strategy optimisation on insufficient sample size.
- No manufactured or cherry-picked evidence.
- Every fix carries a **measurable PASS/FAIL** acceptance criterion.
- Read-only diagnostics and PAPER validation come before any production change.

## Change-control template (used for every item below and every future change)
**Problem → Evidence → Proposed fix → Acceptance criteria (PASS/FAIL) → PAPER validation → Runtime verification**

---

# P0

## P0-1 · Runtime verification of the 3 recent fixes
**Problem.** VWAP-from-index-future, feed-staleness fix, and GEX v1a were deployed
2026-09-02 00:26 IST. Unit tests pass (304/1 date-flake) but there is **no market-hours
runtime confirmation** of any of them.

**Evidence.** 0 open-market rows since the deploy (session had not started). Baselines and
exact SQL are in `SESSION_VERIFY_PLAN.md` Part 1.

**Proposed fix.** None — this is verification, not a code change. Run the 3 checks on
2026-09-02 open-market rows (scheduled, post-MCX-close).

**Acceptance criteria (PASS/FAIL).**
- **VWAP:** ≥ 50 % of open-market NIFTY rows have `vwap != NULL` **and**
  `vwap_status='available'` **and** the reason names the front-month index future; NG/CRUDE
  VWAP coverage unchanged (≥ 95 % `available`, Sep-1 baseline 100 %). Else FAIL.
- **Feed:** chain-fetched `avg(feed_age_sec) ≤ 8 s` (Sep-1: 11.8 s) **and** CRUDEOIL/chain
  `≤ 10 s` (Sep-1: 15.8 s) **and** `BLOCKED[stale feed]` for the day `≤ 15` (Sep-1: 49).
  Partial improvement = INCONCLUSIVE, no improvement = FAIL.
- **GEX:** ≥ 90 % of open-market rows per symbol have `gex_flip` populated **and**
  `sr_diag.gex.status='ok'` in market hours. Flags (MCX σ, flip/pin ATR-distance) recorded,
  **not** corrected.

**PAPER validation.** N/A (this validates prior PAPER-tested changes).
**Runtime verification.** The scheduled 2026-09-02 post-close job; result appended to
`FEED_STALENESS_AUDIT.md` §D / `VWAP_AUDIT.md` / `GEX_SR_SPEC.md`.

---

## P0-2 · Aggregator re-seed / restart blind-period
**Problem.** `_seed_aggs` is one-shot per process: `runner.py:436` adds the symbol to
`self._seeded` **before** the broker candle fetch and never retries. If the fetch fails at
restart, the aggregator rebuilds only from live WS ticks, needing 20 closed 5-minute bars
(`runner.py:537 if len(bars["5m"]) < 20: return`) ≈ 100 min, during which the engine writes
no snapshots, makes no decisions, opens no trades, and cannot normally time-stop an open
position.

**Evidence.** Sep-1 market-hours restart ≈ 09:28 IST → NIFTY open-market snapshots resumed
only ≈ 11:40 IST = **132.3 min blind** (measured snapshot-write gap 03:58:03 → 06:10:18 UTC).
MCX resumed after 72.4 min. Broker `fetch_candles` was returning `DATA_UNAVAILABLE` /
`CONFIG_REQUIRED` through that window.

**Proposed fix (design only).**
- (a) **Retry the seed:** mark `_seeded` only after `seed_from_ohlc` returns > 0 rows;
  re-attempt on later ticks with backoff (≈ every 5 min, cap 6 tries) until seeded or live
  bars ≥ 20.
- (b) **Expose the blind state:** `self_check` gains
  `aggregator_warmup: {sym: {bars_5m, seeded, est_ready_in_min}}`; emit a `WARMING` regime
  row instead of silent nothing.
- (c) **Runbook:** restart only when all segments CLOSED (already the practice); if a
  market-hours restart is unavoidable, expect ≤ ~135 min NIFTY blindness and log it.

**Acceptance criteria (PASS/FAIL).**
- Broker candle REST forced to fail (test double): a fresh runner reaches ≥ 20 5-minute
  bars and resumes `_evaluate` within **≤ 105 min** of the first tick — no worse than today.
- Broker REST succeeds on the 2nd attempt (1st fails): runner seeded within **≤ 10 min**
  (today: never — stays on the slow live-tick rebuild).
- `self_check.aggregator_warmup.est_ready_in_min` decreases monotonically to a
  `seeded/ready` state; unit test asserts accuracy to ± 1 bar.

**PAPER validation.** A PAPER session with a deliberate mid-session restart; confirm the
warm-up telemetry matches the real resume time and no phantom trade fires during warm-up.
**Runtime verification.** Next unavoidable restart — measure the NIFTY resume gap:
< 15 min if broker REST is up, ≤ 135 min if down, telemetry visible throughout.

---

## P0-3 · AngelOne WebSocket single point of failure — **design the requirement only**
**Problem.** One WS feed (`AngelMarketFeed._run`), one broker. On a WS drop or AngelOne
outage the engine has no market data: `feed_age_sec` climbs, all entries are blocked by the
freshness gate (correct fail-safe), and open positions can only be closed via the blunt
`_sweep_unmarkable` overdue-sweep.

**Evidence.** `app/connectors/angel_ws.py` — single `_run` loop, `websockets.connect`,
reconnect-with-backoff but no alternate source. `_pump_feed` reads only `self.feed`. No
secondary provider anywhere in `app/autoscalp/`.

**Proposed fix — REQUIREMENT ONLY. Do NOT implement a second broker/feed without explicit
approval.**
- Define a `MarketFeed` protocol (`get_ltp`, `status`, `subscribe`) and a `CompositeFeed`
  that holds an ordered provider list, serves the freshest, and demotes/promotes on health.
- Candidate secondary sources (choose later, with approval): AngelOne REST quote polling
  (same broker, different transport — cheap, partial mitigation) or a genuinely independent
  feed (exchange official / second broker — full mitigation, more work).
- **Interim mitigation (needs approval, no new dependency):** on a WS gap > T s, fall back
  to a low-rate AngelOne REST quote poll for the *subscribed* tokens **solely to keep
  `get_ltp` fresh enough to time-stop open positions** — never to open new ones (the entry
  freshness gate is unchanged).

**Acceptance criteria (PASS/FAIL) — for the interim mitigation if approved.**
- Simulated 5-min WS blackout: every open PAPER position receives a mark within
  `T + poll_interval` and time-stops correctly — no position overshoots `max_hold_sec` by
  more than one poll interval.
- 0 `_open_paper` calls while `feed_age_sec > 12` during the blackout.
- REST poll rate ≤ 1 request / 3 s / segment (measured).

**PAPER validation.** A PAPER session with an injected WS-blackout window; position
management degrades gracefully, entries stay blocked.
**Runtime verification.** Observe the next natural WS reconnect in PAPER; the fallback
engages and disengages cleanly and `last_error` records the transition.

---

# P1

## P1-4 · Broker REST DATA_UNAVAILABLE / CONFIG_REQUIRED instability
**Problem.** `fetch_candles`, the option-chain fetch, and quotes all depend on the AngelOne
REST/SDK, which returns `DATA_UNAVAILABLE` / `CONFIG_REQUIRED` intermittently. These are
handled (early return, `self.last_error` set) but **not durably logged**, so their real
frequency, timing and per-endpoint pattern are invisible after the fact.

**Evidence.** Repeated `seed <sym>: …` / `chain: …` in `runner.last_error` across the
session; the offline GEX check hit `fetch_candles("NFO","NIFTY",…)` → `CONFIG_REQUIRED`
live; `live_market_snapshots.reason` has **zero** UNAVAILABLE/CONFIG rows because those
failures happen *before* `_persist_snapshot`.

**Proposed fix (design only).**
- A read-only `broker_health` ring buffer (deque ≈ 500): every REST call site records
  `{ts, endpoint, symbol, status, latency_ms}`; exposed at
  `GET /api/autoscalp/broker-health` and summarised in `self_check` as
  `broker_rest: {calls_1h, fail_1h, fail_pct, p95_latency_ms, last_fail_ts}`.
- **No retry-policy change yet** — first *measure* the failure rate and pattern.

**Acceptance criteria (PASS/FAIL).**
- `broker_health` captures ≥ 95 % of REST call outcomes (unit test: call sites vs recorded
  entries).
- After one full PAPER session, `fail_pct` and the per-endpoint breakdown are populated and
  non-trivial.

**PAPER validation.** One session of passive collection → `BROKER_REST_HEALTH.md` with the
measured numbers.
**Runtime verification.** `broker_health` fail events correlate with `runner.last_error`
transitions and aggregator warm-up episodes.

---

## P1-5 · Engine stall detection + alerting
**Problem.** If the runner stops writing snapshots (loop-task crash, lease lost, exception
storm) nothing actively alerts. `self_check` is pull-only; the Telegram daily report is
once/day. `_loop` swallows exceptions into `self.last_error` and continues.

**Evidence.** `report.py:self_check` exposes `last_tick_ts`, `feed_fresh`,
`config_warnings` — but there is no scheduled evaluator or pusher, and no `last_snapshot_ts`.

**Proposed fix (design only).**
- A lightweight watchdog (in-process `asyncio` task or an external systemd timer hitting
  `/api/autoscalp/selfcheck`): when `armed` **and** a segment is `OPEN` **and**
  (`now - last_tick_ts > 60 s` **or** `now - last_snapshot_ts > 3 × decide_every_sec`
  **or** a non-null `last_error` unchanged for > 5 min) → push **one** deduped Telegram
  alert, cleared on recovery.
- Add per-symbol `last_snapshot_ts` to `status()` / `self_check`.

**Acceptance criteria (PASS/FAIL).**
- Inject a stalled `_evaluate` (raises every cycle) in a test: exactly **one** alert within
  ≤ 6 min and exactly **one** recovery notice when it resumes — no floods.
- All segments CLOSED → **0** alerts overnight.

**PAPER validation.** A full PAPER day: 0 false alerts across the open/close transitions.
**Runtime verification.** The first real transient (WS reconnect, broker blip) — the
watchdog stays quiet if it self-recovers under threshold, or alerts + clears correctly.

---

## P1-6 · Config-drift protection (offline/debug scripts ↔ live DB)
**Problem.** An offline script that builds an `AutoScalpRunner` and calls `set_config(...)`
writes to `app_settings` in the **live** `chanakya.db` unless `CHANAKYA_DB_PATH` is
overridden. This already happened once (watchlist clobbered to `["NIFTY"]`, weekend trading
enabled). `config_warnings[]` now *detects* some drift but does not *prevent* it.

**Evidence.** Session history — the incident and the `POST /api/autoscalp/config` fix;
`config_warnings` added to `self_check` afterward. No guard in `db.py`, no conftest
protection for scripts.

**Proposed fix (design only).**
- (a) **Write guard:** `db` refuses writes to a protected key set (`autoscalp_config`,
  `autoscalp_armed`, kill-switch) unless the process is the uvicorn service (systemd sets a
  sentinel env) **or** the caller sets an explicit opt-in env.
- (b) **Config audit:** every `set_config` appends `{ts, old, new, caller(owner,pid,argv0)}`
  to a `config_audit` table; `self_check` surfaces "config changed <ago> by <caller>".
- (c) **Restore-to-known-good:** a checked-in `autoscalp_config.default.json` and an
  admin-gated `POST /api/autoscalp/config/reset`.

**Acceptance criteria (PASS/FAIL).**
- A debug script calling `set_config` without the opt-in env is refused; the live
  `autoscalp_config` row is unchanged (unit + integration test).
- Every `set_config` produces a `config_audit` row with a non-empty caller (test).

**PAPER validation.** Run the existing offline scripts (`analyze_holdtime.py`, …) against
the live DB path; confirm none can mutate protected settings.
**Runtime verification.** `self_check.config_warnings` stays `[]` for a full session; any
real change appears in `config_audit` with the correct caller.

---

# P2

## P2-7 · Probability calibration / discrimination analysis
**Problem.** `probability` is compressed to 0.35–0.64 across all symbols — weak separation;
the EV gate and confidence tiers rest on it.

**Evidence.** `SNAPSHOT_DATA_AUDIT.md` §2 — `probability` min 0.3524 / max 0.6399 over 1507
open-market rows.

**Proposed fix — analysis only, no recalibration.**
- A read-only script: reliability curve (predicted p vs realised win-rate, bucketed),
  Brier score, AUC — **only once** the closed-trade sample supports it (≥ ~50 resolved per
  curve; `calibration._MIN_ROWS = 40`).
- State whether the compression is (a) genuine (edge really is marginal → p near 0.5) or
  (b) a calibration artefact (curve too flat).

**Acceptance criteria (PASS/FAIL).** `CALIBRATION_ANALYSIS.md` exists with the reliability
curve, Brier and AUC on ≥ 50 resolved trades per curve, and states (a) or (b) with numbers.
**No threshold or curve change is proposed until this document exists.**

**PAPER validation / Runtime verification.** N/A (analysis).

---

## P2-8 · Strategy-edge evidence accumulation
**Problem.** No statistical basis for any profitability claim.

**Evidence (clean counts).** Closed AUTOSCALP trades: **NATURALGAS 21** (9W / 7L / 5F),
**NIFTY 4** (3W / 1L), **CRUDEOIL 0**. `risk_ref` present: NG 15 / 21, NIFTY 0 / 4,
CRUDE 0 / 0. (A clean 1:1 trade→snapshot join for the regime split is part of this task —
the current fan-out join is unreliable.)

**Proposed fix — no fix; an evidence gate.** Track, do not act.

### Strategy-edge evidence gap tracker
| symbol | closed trades w/ `risk_ref` | distinct sessions | regimes (≥ TREND + RANGE) | status |
|---|---|---|---|---|
| NIFTY | **0 / 20** (4 total) | 2 / 5 | TREND only | **not met** |
| NATURALGAS | **15 / 20** | ~2 / 5 | TREND (+ some RANGE) | **not met** |
| CRUDEOIL | **0 / 20** | 0 / 5 | — | **not met** |

**Acceptance criteria (PASS/FAIL) — the gate.** No profitability statement, no strategy
optimisation, no `max_hold_sec` / EV / threshold change until **all three rows** show
≥ 20 closed trades with `risk_ref`, ≥ 5 distinct sessions, ≥ 2 regimes, **and** a per-symbol
report with net-R, win-rate 95 % CI, max drawdown, and exit-reason mix.

**PAPER validation.** Ongoing PAPER accumulation across sessions/regimes. Depends on a
**durable** evidence collector (the `nohup` collectors keep dying — see P1-5 / tooling).
**Runtime verification.** Weekly re-run of the tracker; **counts only, never a projected
edge.**

---

# Sequencing (proposed, pending approval)

1. **P0-1** completes on its own tonight (scheduled). No approval needed — read-only.
2. **P0-2** next: highest-value, self-contained, fully PAPER/test-validatable, no
   trading-logic surface. Recommend approving this first.
3. **P1-4** + **P1-5** together: both are read-only telemetry additions that also unblock
   P2-8's durable-collector dependency.
4. **P1-6**: independent, small, protective.
5. **P0-3**: requirement doc first; interim REST-fallback only after P1-4 shows the REST
   failure profile.
6. **P2-7**, **P2-8**: analysis + accumulation, no code that touches decisions.

Nothing above is started. Awaiting your explicit per-item approval.
