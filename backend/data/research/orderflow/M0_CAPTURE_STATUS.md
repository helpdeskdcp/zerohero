# ORDERFLOW v2 — M0: SnapQuote capture status

_started 2026-09-09 ~12:10 IST_

## M0 goal (from ORDERFLOW_ENGINE_V2_SPEC.md Part 10)

`snapquote_ticks` gains >= 8000 rows/session for the P0 instrument (NIFTY
future), >= 5 consecutive sessions, quality gates (Part 1.3) green.

## Root cause the capture was dead (0 rows despite L2_CAPTURE_ENABLED=1)

`l2capture/worker._session()` opened the WebSocket with
`websockets.connect(additional_headers=...)` guarded by `try/except TypeError`
falling back to `extra_headers`. `websockets` does **not** validate that kwarg
until the connection actually opens (inside `async with`), well past the
try/except — so on the installed **websockets 13.1** (`additional_headers` is
14.0+) every session raised `TypeError` immediately, the worker never reached
`store.start_run()`, and the DB stayed empty.

Fix (commit `cd9cf7f`): choose the header kwarg from `websockets.__version__`
before calling `connect()`. Deployed via `systemctl restart oi-dashboard`.

## Verified working (post-restart, live session)

| instrument | rate | fields |
|---|---|---|
| **NIFTY future** (P0) | **~2.4 packets/s** (293 in a 2-min window) → ~50k/session projected | ltp, ltq, cumulative volume, tot_buy_qty, tot_sell_qty, oi, bid/ask, bid_qty/ask_qty, full 5-level depth_json — all populated |
| CRUDEOIL future | ~0.8/s | depth_json populated; top-level `bid` NULL on MCX → reader must fall back to `depth_json[0]` |
| NATURALGAS future | **stuck at 1 stale packet** — MCX natgas contract/token/liquidity issue; investigate before relying on it for regime diversity (NIFTY + CRUDEOIL still cover it) |

Autoscalp / scalp feed unaffected by the restart (armed, running, tick age 0.1 s).
`live_trading` untouched. Capture runs inside the always-on service (code in the
checkout) and continues automatically each session.

## M0 remaining

- Accrue **>= 5 consecutive trading sessions** of NIFTY-future capture.
- Then run the Part-1.3 quality gates (packets/session, coverage %, clean-book %,
  volume monotonicity, independent-session count).
- Follow-up: fix or drop NATURALGAS capture; confirm the CRUDEOIL L1 fallback.

Nothing modelled until M1 (>= 60 quality sessions).

---

## Update 2026-09-09 ~13:25 IST — M0 hardening #1 + #2 done

### #1 NATURALGAS capture fixed (commit 206a5f8)
- **root cause:** `instruments.resolve_mcx_future` lexically sorted expiry
  strings (`'20NOV2026' < '25SEP2026'`) AND matched OPTFUT rows -> it returned a
  dead far-dated *option* token (NATURALGAS -> 583870 instead of the 568245 /
  25SEP2026 front-month FUTURE; CRUDEOIL had the same wrong-token problem, token
  580384 vs the correct 565899).
- **fix:** FUTCOM-only, chronological expiry parse, drop expired, nearest =
  front month; `NEXT` / `LATEST` supported.
- **packet validation** (`worker._validate_packet`): reject missing/garbage
  `exch_ts`, stale (>150 s lag vs wall clock), future-dated (>15 s skew),
  `ltp<=0`, crossed book. Rejects are counted per-reason, logged, surfaced in
  `status()` and persisted to `capture_runs.note`. Good packets still written.
- **logging** (`l2capture` logger): worker start, token resolution (token@expiry
  per symbol + MISSING list), connect, subscribe, TEXT/control + error frames
  (were silently dropped), heartbeat/recv failure -> reconnect, "no binary data
  90 s during market hours" -> reconnect, markets-closed idle, lease stand-by,
  credential-unavailable retry.
- **pre-fix wrong-token rows wiped** from `l2_capture.db` -> M0 clock starts
  clean at the 2026-09-09 13:15 IST redeploy.
- **verified live:** NIFTY 68407@29SEP2026 ~1.6 Hz, CRUDEOIL 565899@21SEP2026
  ~1.0 Hz, NATURALGAS 568245@25SEP2026 ~0.6 Hz; L1 100 %, L2 100 %, clean-book
  100 %, 0 rejects, 0 stale.

### #2 daily capture health-check (commit 7ad1f25)
- `app/l2capture/health.py` -> `python -m app.l2capture.health [--date]`.
  Per session date, per symbol: rows, packet rate vs a capture-health baseline,
  expected-vs-actual, gaps (median/p90/max, >5/30/60 s counts, gap-time frac),
  stale-written, dup snap_keys, L1 %, L2 (5-level) %, crossed-book %, clean-book
  % (ladder monotonicity), volume continuity (monotonic %, breaks, max drop),
  OI jumps + null %, per-field null %, session coverage over the nominal window,
  and a 0-100 **Data-Quality Score** (multiplicative penalties per V2 spec Part
  4). Writes `l2_health_<date>.{md,json}`, appends `l2_health_log.tsv`, optional
  Telegram one-liner. Verdict PASS/WARN/FAIL + `p0_pass` (NIFTY >= 8000 rows &
  PASS).
- **cron:** `scripts/l2_capture_health_cron.sh`, crontab `55 23 * * 1-5` (after
  NSE 15:30 + MCX ~23:30 IST). Runs automatically after every trading session.
- tests: `tests/test_l2capture_health.py` (5); full suite 703 pass.

### M0 remaining (unchanged)
Accrue >= 5 consecutive full NIFTY-future sessions with the health check green,
then the **complete M0 data-quality report is shown before M1**. Nothing
modelled until M1 (>= 60 quality sessions). V1 baseline stays frozen; V2 not in
the RK score; no parameter tuning on the first sessions.
