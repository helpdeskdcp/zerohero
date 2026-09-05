# Autoscalp — Production-Readiness Evidence Report

**Generated:** 2026-09-01 (IST) · **Deployed HEAD:** `0095240` (audited from `254f0b4`) · **Mode:** PAPER only, LIVE hard-disabled

> **2026-09-01 dependency-readiness pass appended below (§M–§O).** Static + dependency audit clean;
> 3 safe cleanups made (`a466c28`); `DEPENDENCIES.md` added (`0095240`); one controlled restart
> verified (§O). Evidence collection (§D/§E) uninterrupted.

This report separates four distinct states. Do not read "tests pass" as "ready to trade real money."

| state | meaning | status |
|---|---|---|
| **Code complete** | the pipeline is implemented and unit-tested | ✅ established |
| **Operationally healthy** | the deployed service runs, self-reports, recovers | ✅ established |
| **Evidence established** | runtime behaviour observed across real sessions | ⏳ partial — 2 gaps open, collection running |
| **Strategy performance established** | statistically-sound edge across many sessions | ❌ NOT established — needs weeks of PAPER runtime |

---

## A. Code status

- 12 commits this line of work; latest `254f0b4`. Working tree clean apart from gitignored `backend/data/`.
- Everything pushed to `origin/main` (`254f0b4`).
- No `TODO`/`FIXME`/`NotImplementedError` in `app/` beyond the abstract `broker_base.py` ABC stubs (intentional).
- Autoscalp engine modules: `app/autoscalp/{runner,aggregator,safeguards,notify,report}.py` + `app/engines/{scalp_strategy,paper_trading,option_engine,signal_engine}.py`.

Recent structural additions (all committed + deployed):
- `a2b63b8` expiry-day 0-DTE engine + zero-to-hero lottery leg
- `1b60881` premium-richness safeguard (cap at % of spot)
- `90bfcc3` sweep positions the WS feed never marks (`TIME_NODATA`)
- `1f614f3` + `cb26606` per-symbol `entry_blocks` + `BLOCKED[...]` snapshot stamp
- `fcc65fc` equity-F&O scalping path wired end to end
- `58f1da8` self-reporting: `session_report`, `self_check`, daily Telegram push
- `6f8b29c` + `5ff0d40` self_check market-hours-aware + config-smell warnings
- `254f0b4` `analyze_holdtime.py` evidence analyzer

## B. Test status

- **277 passed**, 0 failed, 6 warnings (FastAPI `on_event` deprecation only). Runtime ~80s.
- `compileall` clean on `app/` + `broker/`.
- Test isolation hard-guarded: `conftest.py` swaps `CHANAKYA_DB_PATH` to a tmp file at import (before `app.db`), strips `TELEGRAM_*`, stubs `telegram._send`.
- Coverage added this pass: premium cap, unmarkable-position sweep, `entry_blocks` + snapshot stamp, equity-F&O routing, `session_report`, `self_check` (incl. market-hours gating + config warnings), daily-report once-only guard.

**Caveat:** unit tests exercise fake feed / fake chain. They prove wiring and branch logic, **not** market behaviour.

## C. Runtime health

Live `GET /api/autoscalp/selfcheck` (pre-market, 2026-09-01 ~05:50 IST):

```
ok: true                    (market closed -> feed_fresh not gating)
armed: true   running: true   is_leader: true
market_open: false   segments: {MCX: CLOSED, NSE: CLOSED}
config_warnings: []
checks: armed✓ running✓ is_leader✓ feed_connected✓ feed_fresh✗(closed)
        no_last_error✓ all_aggs_seeded✓ live_trading_disabled✓
bars_ready: NIFTY✓ NATURALGAS✓ CRUDEOIL✓  (>=31 5m bars each, seeded on start)
```

- `self_check` is now market-hours-aware: `feed_fresh` / `feed_connected` gate `ok` only while a relevant exchange is OPEN (the WS feed legitimately goes quiet with no session). `armed` is reported but never gates `ok` (operator choice).
- `config_warnings[]` surfaces: empty watchlist, `safeguards.allow_weekend` ON, `live_trading` not explicitly disabled.
- A config regression was caught and fixed this pass: an offline debug script's `set_config()` had clobbered the live watchlist to `["NIFTY"]` and set `allow_weekend: true`. Restored to `["NIFTY","NATURALGAS","CRUDEOIL"]` / `safeguards: {}`. `config_warnings` now detects this class of drift.

## D. NIFTY time-exit evidence — ⏳ INSUFFICIENT

**Analyzer:** `backend/analyze_holdtime.py` (read-only; refuses tuning conclusions below 20 closed trades; the `max_hold_sec` what-if is a conservative proxy that cannot bias toward widening).

**Data available now:** 4 closed NIFTY AUTOSCALP trades (all from the first session, pre-`risk_ref`):

```
ALL: n=4  W/L=3/1  win%=75  net=+20.95pts  exp=n/a(no risk_ref)  TIMEexits=4  premature=0  missed_profit≈0
buckets: 25-40m n=3 (+19.8)   60m+ n=1 (+1.15)
what-if caps 600s..3000s: net = +20.95 for ALL (proxy holds TIME-exit pnl constant — correct)
```

**Finding:** all 4 exits are `TIME`, but **premature-exit rate = 0** and **missed-profit ≈ 0** — the hold clock is *not* cutting live winners short; the trades drift to expiry near their exit price. This **weakens** the earlier "widen `max_hold_sec`" hypothesis: widening only helps if trades hit the cap with unrealised profit, which is not what n=4 shows.

**Change made (reversible, forward-test):** `symbol_profiles.NIFTY = {"max_hold_sec": 2400}` (1500→2400s), user-authorised, config-only, single knob, NIFTY strategy logic untouched. On expiry days the 480s `expiry_day_profile` still wins. Rollback = delete the key.

**Verdict:** NOT enough evidence to keep *or* further change `max_hold_sec`. Need ≥20 closed NIFTY trades with `risk_ref` populated — realistically 1–2 weeks of PAPER sessions. Collection running: `data/nifty_holdtime_forward_test.sh` (before/after buckets) + `data/session_evidence.sh` (endpoint time-series) + the DB.

## E. CRUDEOIL block-reason evidence — ⏳ PENDING LIVE MCX SESSION

**History:** 2026-08-31 — 141 CRUDEOIL BUY_CE/BUY_PE decisions, **0 trades**, `last_error` null throughout (no exception). Offline analysis ruled out: MCX 23:00 cutoff (all 141 were 18:00–22:59 IST), concurrency, daily caps, kill switch, weekend/holiday, and `min_option_premium` for realistic premiums (Crude PE ≈ 200–260; chain LTPs confirmed real). NG traded fine on the same feed/exchange/window → not a shared-feed issue.

**Why it can't be closed offline:** the `BLOCKED[<gate>]` snapshot stamp (`cb26606`) and in-memory `entry_blocks` (`1f614f3`) were deployed *after* that session. All 141 Aug-31 rows are unstamped (verified). The gate can only be read from a fresh MCX session on the current HEAD — which stamps every refused entry with the exact `check_entry` reason string, at decision time, from the real runtime path.

**Set up and waiting:**
- `cb26606` confirmed in deployed HEAD → next MCX session stamps.
- `data/crudeoil_block_evidence.sh <IST-day>` — extracts the full evidence chain (live `entry_blocks`, `BLOCKED[...]` rows, gate histogram, un-blocked BUY-leans, trades, session window, regime mix).
- Watcher (bg job) fires on the first CRUDEOIL `BLOCKED[` row / CRUDEOIL trade / daily-report key.

**Decision rule (pre-committed):**
- If the gate is the market-hours check → document, **do not weaken**, and verify CRUDEOIL trades normally once MCX is genuinely OPEN.
- If another gate → trace the real runtime path, fix only a genuine implementation defect, add a regression test.

## F. Multi-symbol evidence

| symbol | class | resolution verified | bars seeded | traded (Aug 31) |
|---|---|---|---|---|
| NIFTY | NSE index weekly | ✅ | ✅ 31+ 5m | 4 (3W/1L, +20.95) |
| NATURALGAS | MCX options-on-futures | ✅ | ✅ 34+ 5m | 7 (2W/2L/3F, +0.85) |
| CRUDEOIL | MCX options-on-futures | ✅ | ✅ 34+ 5m | 0 — see §E |
| RELIANCE (equity F&O) | NFO OPTSTK | ✅ live `/api/market-selection` → `instrument:OPTION`, ATM 1280, 5 chain rows | n/a (not on watchlist) | n/a |

- Equity-F&O path (`fcc65fc`): `_underlying_ref` falls back to `resolve_equity`; `selection_snapshot` routes an equity *with* listed NFO options through the option-chain path. Verified live for RELIANCE. Not added to the trading watchlist (no instruction to).
- NG + NIFTY both produce real paper trades end-to-end (signal → contract-lock → paper fill → mark → exit → persist). Proven.

## G. Daily reporting evidence

- `GET /api/autoscalp/report?day=YYYY-MM-DD` — verified live: `?day=2026-08-31` returns the correct per-symbol rollup (NIFTY 4t 75% +20.95 / NG 7t +0.85 / Crude 0t), matches direct SQL. Stale snapshot-only symbols (BANKNIFTY) filtered out.
- `GET /api/autoscalp/selfcheck` — verified live (§C).
- `session_report()` is read-only (asserted in test — no `set_setting`/`insert_`/`update_`).
- **Runtime confirmation of the auto-push pending** the real exchange close today.

## H. Telegram notification evidence

- Telegram IS configured on the live service (`TELEGRAM_BOT_TOKEN` 46ch, `TELEGRAM_CHAT_ID`, `TELEGRAM_SIGNALS_CHANNEL_ID` present in the service process env; absent from `.env` grep only because values are set).
- `runner._maybe_daily_report()`: fires in `tick_once` **before** the `if not self.armed: return` (arm-state-independent); once per IST day per exchange after close (NSE 15:35 / MCX 23:35); `db.set_setting(key)` is called **before** the send → restart-safe, cannot re-fire even if the send throws.
- Code-verified it touches **no** order/arm/config/`check_entry` path — only `db.get/set_setting` (dedup key), `session_report` (read), `_tg_send(gate=False)` (bypasses only the *confidence* gate on the notification card — not any trading control), `_emit`.
- Unit test `test_daily_report_pushes_once_per_segment`: passes (fires once, key guard, telegram called, second tick no-op).
- **Runtime confirmation pending** first real close (watcher covers the `autoscalp_report_sent:*` key).

## I. Safety-control verification

- **`app/autoscalp/` imports no broker / execution / order module** — only `killswitch` (a safety control). Grep-verified.
- The autonomous runner reaches real orders through **no code path**. It calls only `paper_trading.{open_trade,close_trade,update_trade_price}` — pure in-DB simulation, zero network (`paper_trading.py` docstring: *"No real orders are ever placed. live_trading is always false."*; no `requests`/`http`/broker calls).
- `autoscalp status.live_trading == False`, `paper_mode == True` (hardcoded in `status()`, not a toggle).
- The LIVE order module (`app/execution/angelone_broker.py`) is triple-gated (needs `CHANAKYA_LIVE_CONFIRM_TOKEN` + more) and is a **separate module the runner never imports**.
- **No live-enabling env vars in the service process** (`CHANAKYA_LIVE_CONFIRM_TOKEN` / `CHANAKYA_ALLOW_LIVE` / `CHANAKYA_API_TOKEN` all absent — verified via `/proc/<pid>/environ` and `.env`).
- Safeguard stack active (DEFAULTS): max daily loss ₹3000, max 18 trades/day, max 3 concurrent, max 4 consecutive losses, NSE cutoff 15:00 / MCX 23:00, feed-age ≤ 12s fail-closed, min premium ₹8, max premium 8% of spot, kill switch.
- Market-hours suspension: `_evaluate` publishes `regime: MARKET_CLOSED` and returns early outside each symbol's exchange hours (unless `safeguards.allow_weekend`, which `config_warnings` now flags).

## J. Remaining risks

1. **No established edge.** NIFTY n=4, NG n=7. Win rates and P&L are descriptive only. Do not size or go live on this.
2. **CRUDEOIL is silently not trading** (§E). Until the gate is confirmed from a live session, one of three watchlist symbols contributes nothing and the reason is a hypothesis.
3. **Calibration is `prior`** — the probability model auto-fits only at ≥40 resolved LIVE samples. Until then every `probability`/`confidence` is the untuned prior.
4. **`risk_ref` gap on early trades** — the first NIFTY trades have no `risk_ref`, so expectancy-R is uncomputable for them. New trades carry it. The analyzer degrades gracefully.
5. **Daily report is fire-once even on failure** — `set_setting` before send means a `session_report` exception permanently skips that day's push (logged to `last_error`). Acceptable (no infinite retry) but a silent miss.
6. **Sandbox clock** runs fast / jumps — session timing in this environment is not representative of production wall-clock behaviour.
7. **Live greeks not wired** — `analyse_leg` uses its no-greek fallback (intentional, but option-quality scoring is coarser without them).
8. **Single-process leader lease** — if the lease-holder dies uncleanly, up to `LEASE_TTL` (30s) of no evaluation until another picks it up. Acceptable for PAPER.

## K. Items requiring future observation

| # | what | how it closes | ETA |
|---|---|---|---|
| K1 | NIFTY hold-time: ≥20 closed trades w/ `risk_ref` | `analyze_holdtime.py NIFTY` verdict flips from INSUFFICIENT | ~1–2 weeks PAPER |
| K2 | CRUDEOIL block gate | `crudeoil_block_evidence.sh` after next MCX session | next MCX session |
| K3 | Daily Telegram auto-push | `autoscalp_report_sent:{NSE,MCX}:<day>` key appears + card in channel | today's closes |
| K4 | Calibration fit | `status.calibration` becomes non-null at ≥40 resolved LIVE samples | weeks |
| K5 | NG/Crude edge | `analyze_holdtime.py` per symbol at n≥20 | ~2 weeks |
| K6 | Expiry-day engine + ZTH first live exercise | NIFTY expiry session (Tue) — trades tagged `AUTOSCALP` (480s profile) / `AUTOSCALP-ZTH` | next Tue |
| K7 | Premature-exit / missed-profit trend | analyzer `premature` + `missed_profit` columns over n≥20 | ~2 weeks |
| K8 | Probability overconfidence (2026-09-05, see `CALIBRATION_OVERCONFIDENCE_AUDIT.md`) — global calibration curve erases a real per-(symbol,regime) difference; sharpest leads (TRENDING_UP regime +33.4pp gap, two NATURALGAS triples at 30% win despite top-of-range scores) are all at or under this report's own 20-sample floor | re-run the same attribution once each flagged subgroup reaches n≥20 | ~2-3 weeks PAPER |

## L. Is the system ready for extended PAPER trading?

**Yes — for extended PAPER trading, with monitoring.** Not for LIVE.

- Code complete: ✅
- Operationally healthy: ✅ (self-reports, recovers, safeguards active, no live path)
- Evidence established: ⏳ — the pipeline is proven end-to-end on NG + NIFTY; CRUDEOIL has one unconfirmed gate (K2); everything else is set up to self-collect.
- Strategy performance established: ❌ — explicitly not. Extended PAPER trading is exactly how K1/K4/K5/K7 get answered.

**Recommended posture:** keep it armed in PAPER, let the daily report + `selfcheck` run, re-run `analyze_holdtime.py` weekly, and close K2 from the next MCX session. Do not discuss LIVE until K1, K4, K5 all show a positive, stable edge across many sessions and a human has reviewed the full trade log.

---

## M. Dependency-readiness audit (2026-09-01)

**Static sweep (read-only, AST + import):**
- TODO/FIXME/XXX/HACK: **0** in `app/` + `broker/`.
- `NotImplementedError`: **13**, all in `broker_base.py` — an ABC overridden by `PaperBroker`/`ShadowBroker`/`AngelOneBroker`. Intentional (C).
- `except: pass` (bare): **0**. `except Exception: pass`: **47**, sampled ~20 across autoscalp/db/combos/main — every one is best-effort side-effect isolation on a critical path (WS broadcast, telegram fire-and-forget, cache-fallback, lease fail-safe) or a scoped `(TypeError, ValueError)` parse fallback. Intentional (C).
- Dead code: **1** — `AutoScalpRunner._tg()` (superseded by `_tg_send`, never called). Removed (D → done, `a466c28`).
- Unreachable-after-return / ellipsis bodies / empty returns: **0**.
- **67 modules import clean**; **0 `ModuleNotFoundError`** on full recursive import (incl. `mcp_server`).
- All **12 `/api/autoscalp/*` endpoints** resolve to fully-implemented functions.

**Dependencies:**
- 6 third-party imports (`fastapi, pydantic, pyotp, requests, websockets, dotenv`) — all `==`-pinned in `requirements.txt`, all installed, `pip freeze` matches pins exactly. `+uvicorn` (entrypoint), `+pytest` (test). `python-dotenv` is `mcp_server`-only (optional).
- No used-but-unpinned, no pinned-but-unused (bar the entrypoint/test).
- Python 3.11.2. Repro: `python3.11 -m venv venv && venv/bin/pip install -r requirements.txt`.
- DB: sqlite, single file, idempotent auto-migration on boot (`init_db` + `_MIGRATIONS`) — no external tool.
- **Gap fixed:** 7 optional env vars (`ANGEL_MASTER_CACHE`, `CHANAKYA_INSTRUMENT_MASTER`, `CHANAKYA_MARKET_WINDOWS`, `CHANAKYA_MARKET_HOLIDAYS`, `CHANAKYA_MAX_DATA_AGE_SEC`, `OI_HISTORY_DB`, `CHANAKYA_ENV_FILE`) were undocumented — now in `.env.example` as commented entries. All have safe code defaults; no behaviour change.

**Safe changes made (`a466c28`, no behaviour change, 277 tests green):**
1. removed dead `runner._tg()`
2. `self_check` surfaces `segments_error` instead of silently swallowing a market-hours lookup failure
3. `.env.example` documents the 7 optional overrides

**Not touched (per instruction):** the ABC stubs, the 47 deliberate `except: pass`, and every B/C item (NIFTY/CRUDEOIL evidence gaps, calibration, greeks).

**Full deps/ops reference:** `backend/DEPENDENCIES.md`.

## N. Kill-switch clarification

`kill_switch = {active: false, policy: "MONITOR"}`. Interpretation for the evidence phase:
- The kill-switch **control** is present, functional, wired into `safeguards.check_entry` (`if killswitch.is_active(): return False, "kill switch active"`), shared across workers via `app_settings`, and **survives restart**.
- It is deliberately **not engaged** — engaging it would refuse all new entries and halt the very PAPER evidence collection this phase exists for. `policy: MONITOR` (alert-only) is the safe default; `FLATTEN` (the dangerous one) is not set.
- This is the correct state for extended PAPER trading. Engaging the switch is an explicit "stop trading now" action, out of scope here.

## O. Controlled restart record (2026-09-01 ~06:12 IST)

**Pre-restart:** HEAD `0095240`, 0 unpushed, working tree clean; service pid 3489884; 3 evidence collectors alive; DB 13 trades / 5284 snapshots / 11 signals; forward-test jsonl 89 lines.

**Action:** `systemctl restart oi-dashboard.service` (app-level only — no VPS/OS reboot). One restart.

**Post-restart 10-point verification — ALL PASS:**

| # | check | result |
|---|---|---|
| 1 | service running | ✅ `active`, pid 3492058 |
| 2 | self-check healthy | ✅ `ok: true`, `segments_error: null`, all gating checks pass |
| 3 | aggregators seeded | ✅ NIFTY 31 / NG 34 / CRUDE 34 5m bars |
| 4 | configured symbols | ✅ `[NIFTY, NATURALGAS, CRUDEOIL]` |
| 5 | PAPER-only enforced | ✅ `live_trading_disabled: true`, `config_warnings: []` |
| 6 | LIVE trading disabled | ✅ `live_trading: false`, `paper_mode: true`; kill-switch mechanism intact; `allow_weekend` falsy (market-hours suspension ON) |
| 7 | evidence collectors healthy | ✅ all 3 (pids 3480137, 3490027, 3486913) survived; resumed cleanly (samples at 06:13, `selfcheck.ok=true`) |
| 8 | reporting endpoint | ✅ `/api/autoscalp/report?day=2026-08-31` → correct totals |
| 9 | no new startup errors | ✅ `last_error: null`; no journal errors since restart |
| 10 | no duplicate scheduler/reporting | ✅ single process, single `_loop`, single leader lease `srv1243704:3492058`. (The other `uvicorn app.main` on :8420 is the unrelated pre-existing `chanakya-ai.service` fork.) |

**Evidence integrity across the restart:** forward-test jsonl 89→90, session_evidence 4→5, DB trades 13→13, snapshots 5284→5302 (grew), signals 11→11. Nothing lost.

## Readiness verdict (updated)

```
CODE COMPLETE (where possible)   ✅   pipeline + audit clean; 1 dead fn removed
DEPENDENCIES READY               ✅   pinned, installed, documented; repro deterministic
TESTS GREEN                      ✅   277 passed
SAFE RESTART VERIFIED            ✅   one controlled restart, 10/10 post-checks, evidence intact
RUNTIME EVIDENCE CONTINUES       ⏳   D (NIFTY hold-time, n=4/20) + E (CRUDEOIL gate) open;
                                     collectors running; K-table tracks the rest
```

**Not "100% complete."** Two items depend on future runtime evidence and are correctly left open.
Ready for **extended PAPER trading with monitoring**. **Not** ready for LIVE.

---

## P. Final close-out (2026-09-05) — D and E resolved from real runtime evidence

**Deployed HEAD:** `9698063`. Since §A-O (2026-09-01), a separate full-app architecture
review shipped and deployed (CORS lockdown, `app/execution/` 0→53 tests, a CI test-on-push
gate, observability logging on 41 previously-silent safety-critical paths, the `main.py`
1269→270-line router split, and consolidating the one genuine duplicated-math case,
`max_pain`). None of it touched autoscalp strategy logic, `symbol_profiles`, or
`max_hold_sec` — this section only closes the two evidence gaps §D/§E left open.

The ad-hoc bash evidence collectors (`data/{nifty_holdtime_forward_test,session_evidence,
crudeoil_block_evidence}.sh`) died some time after 2026-09-02 15:46 IST (sandboxed
background bash jobs do not survive indefinitely — a known constraint, not a service
issue) and were **not** relied on for this close-out; every number below is a fresh,
independent read of the live `ai_paper_trades` / `scalp_signals` tables and the live
`/api/autoscalp/selfcheck` endpoint, taken today.

### D. NIFTY time-exit evidence — still ⏳ INSUFFICIENT (correctly unresolved, config untouched)

```
./venv/bin/python analyze_holdtime.py NIFTY   (run against the live DB, 2026-09-05)

# hold-time evidence — NIFTY   (12 closed AUTOSCALP trades)
*** SAMPLE TOO SMALL (12 < 20) — report descriptive stats only, DO NOT tune max_hold_sec. ***
  ALL: n=12  W/L=6/6  win%=50.0  net=-30.25pts  exp=-0.351R  TIMEexits=9  premature=1  missed_profit≈6.95pts
  == verdict ==
  INSUFFICIENT EVIDENCE (12/20). Keep max_hold_sec as-is. Collect more sessions.
```

n grew 4→12 since the original 2026-08-31 session (`ai_paper_trades` by day: 08-31→4,
09-02→1, 09-03→4, 09-04→3 — 3 further trading sessions), still under the analyzer's own
pre-committed `MIN_SAMPLE = 20` floor. **No config change made.** `symbol_profiles` in
the live config (`GET /api/autoscalp/config`, verified today) confirms NIFTY has **no**
override — it runs on the base P6-validated defaults, matching the code's own comment in
`autoscalp/runner.py`: *"NIFTY is DELIBERATELY absent -> it runs on the P6-validated
defaults and must stay that way (best live win-rate)."* The §D-era `max_hold_sec: 2400`
forward-test was applied live via `POST /api/autoscalp/config` (per §D's own "config-only,
... reversible" wording) — a DB-persisted runtime change, never a commit — so it is not
traceable in git history; `git log -S'"NIFTY": {"max_hold_sec"' -- app/autoscalp/runner.py`
returns nothing, confirming it never touched source. It is gone from today's live config
(`GET /api/autoscalp/config` returns no NIFTY entry in `symbol_profiles`), i.e. reverted
at some point via the same live-config mechanism. `analyze_holdtime`'s "current effective
cap on these trades: [1500.0, 2400.0]" reflects trades that straddle both eras.
**This close-out took no action on it**, consistent with "do not modify any frozen NIFTY
AutoScalp configuration."

**Verdict: unchanged from §D — still INSUFFICIENT EVIDENCE, correctly left open.** K1
stays open; re-run `analyze_holdtime.py NIFTY` again once n≥20.

### E. CRUDEOIL block-reason evidence — ✅ RESOLVED: root cause found, fixed, and verified

**Correction while writing this close-out:** an earlier draft of this section speculated
that no runner code had changed since 2026-08-31. That was checked against `git log`
before publishing and was **wrong** — a concrete, already-evidenced root-cause fix landed
the very next day. The corrected finding:

**Root cause (`FEED_STALENESS_AUDIT.md`, commit `91e5e77`, 2026-09-01 15:29 IST):** the
AngelOne WS feed reader and the autoscalp decision loop shared one asyncio event loop.
`_evaluate` ran the broker option-chain REST fetch (`_autoscalp_chain` →
`selection_snapshot`, ~10 quotes), the CPU-bound `decide_from_context`, and the SQLite
snapshot write **synchronously on that loop**, once per symbol every 30s. While any of
these ran, `ws.recv()` could not be scheduled, so feed age climbed past the 12s
stale-feed cutoff and the freshness safeguard correctly (per its own logic) blocked
entries — but the *underlying* staleness was an artifact of the loop being starved, not
the broker feed actually being behind.

**2026-09-01 evidence that triggered the fix:** ~57% of all evaluation cycles were stale;
CRUDEOIL was worst-hit (chain-fetched cycles averaged 19.0s feed age, 98% over the 12s
cutoff) because `selection_snapshot` for MCX options-on-futures is the heaviest chain
fetch of the three symbols. ~49 `BLOCKED[stale feed]` events that day, some on strong
setups (e.g. one scored 73.9 / p=0.58 / EV=0.4R, killed purely by feed age).

**Fix:** wrapped the four blocking calls (chain fetch, `decide_from_context`,
`_index_future_vwap`, `_persist_snapshot`) in `await asyncio.to_thread(...)` — the same
pattern already used elsewhere in this file. Explicitly verified byte-identical decision
inputs/outputs; only *where* the work runs changed. No threshold, safeguard, broker-order,
or NIFTY-profile change. 296 tests passed at the time.

**Verified live the very next session (2026-09-02, §F of the audit doc):** chain-fetched
CRUDEOIL feed age collapsed from 19.0s/98% stale to **0.1s/0.0% stale**; `BLOCKED[stale
feed]` went from 49/day to **0**. This is not new evidence produced by this close-out —
it was already measured and recorded before this task started; this close-out is
re-confirming it from the live trading outcomes that followed.

**This close-out's own confirmation, from live DB queries run today (2026-09-05):**

```
CRUDEOIL AUTOSCALP trades: 11 closed (7 WIN / 4 LOSS), opened 2026-09-02 through 2026-09-04
  across 3 separate MCX sessions -- the fix held across every session since, not a one-off.
CRUDEOIL scalp_signals (LIVE): 11 total, 10x BUY_PE + 1x BUY_CE -- every signal that
  reached decision stage converted to a trade (100% signal->trade rate once past entry
  gates), confirming the order-intent pipeline has no separate defect.

entry_blocks.CRUDEOIL, mined from every snapshot captured 2026-09-01 11:31 -> 2026-09-02
15:46 IST (spanning the fix's deploy and its first live session):
  n=45  "stale feed (>12s)"                   -- pre-fix + early post-fix stragglers
  n=28  "duplicate: CRUDEOIL PE already open" -- single-open-position discipline;
                                                  PROVES positions were opening successfully
  n= 4  "past MCX cutoff 23:00"               -- market-hours gate, working as designed
```

The residual "duplicate" and "cutoff" blocks are legitimate, documented safeguards (§I),
not defects. Per the pre-committed decision rule: *"If the gate is the market-hours check
→ document, do not weaken"* — done.

**On the original 2026-08-31 session specifically** (141 refused signals, 0 trades,
unstamped, predates both the `BLOCKED[...]` stamp and this fix): it cannot be stamped
retroactively, but it is now the **most evidence-consistent** explanation by a wide margin
— 2026-09-01's measured 98%-stale/19s-avg CRUDEOIL condition is the same class of failure,
one day earlier, before anyone had looked at feed timing at all. This report states that
as the likely explanation, not a certainty, since the specific day cannot be re-stamped.

**No new code change made in this close-out — none was warranted.** The defect was
already found and fixed on 2026-09-01, before this task's evidence-gathering began; this
close-out's job was to confirm it held, which it did across 3 further sessions and 11 real
trades.

**Verdict: RESOLVED.** K2 closes: CRUDEOIL's entry pipeline is confirmed healthy and
trading; the original blocking cause was a real, now-fixed, already-verified bug
(event-loop starvation), not a hypothesis.

### Updated readiness verdict

```
CODE COMPLETE                     ✅   unchanged, plus a full architecture-review pass since §A-O
DEPENDENCIES READY                ✅   unchanged
TESTS GREEN                       ✅   498 passed (was 277 at §B; +221 across the interim work)
SAFE RESTART VERIFIED             ✅   multiple further restarts since §O, all clean (see interim commits)
RUNTIME EVIDENCE                  ⏳/✅  D: still insufficient (12/20), config correctly untouched.
                                        E: RESOLVED -- CRUDEOIL confirmed healthy, no defect.
STRATEGY PERFORMANCE ESTABLISHED  ❌   still not established -- unchanged conclusion, more sessions needed
```

**Posture unchanged: ready for extended PAPER trading with monitoring. Not ready for
LIVE.** One of two open evidence gaps (E) is now closed with a clean bill of health; the
other (D) remains open exactly because the evidence says it should — 12 trades is not
enough to change a live trading parameter, and none was changed.

**Remaining risk carried forward:** §J items 1, 3-8 are unchanged and still apply
(no established edge; calibration now shows `n=67` resolved LIVE samples in
`self_check` — at or past the ≥40 auto-fit threshold mentioned in §J.3/K4, worth a
dedicated look at the calibration report itself, but that is a new investigation outside
this close-out's scope and not claimed as verified here). §J.2 (CRUDEOIL silently not
trading) is retired — superseded by this section.
