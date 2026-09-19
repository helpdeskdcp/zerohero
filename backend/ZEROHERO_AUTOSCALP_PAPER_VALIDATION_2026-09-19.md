ZEROHERO AUTONOMOUS SCALPER
PAPER VALIDATION REPORT
=======================

Repository: /root/zerohero (main, commit 64a217c at time of writing)
Date: 2026-09-19
Scope: app/autoscalp/runner.py's live decision loop and everything it
actually calls — the real PAPER-trading autonomous scalper. Read-only
investigation, no code changed, no service restarted, no real broker
orders (confirmed throughout). Built from 4 parallel, independent
execution-path traces (file:line evidence) plus direct test runs and a
short live-process sample; findings below are evidence-based, not
inferred from filenames or docstrings.

Labels used throughout, per your instruction: VERIFIED / PARTIALLY
VERIFIED / FAILED / NOT TESTED / MISSING. No profitability, production-
readiness, or accuracy claims are made unless a specific reproducible
number is cited.

---

A. ARCHITECTURE
----------------
Traced actual imports/calls, not filenames.

| Stage | Real module | Verdict |
|---|---|---|
| WS market data | `app/connectors/angel_ws.py::AngelMarketFeed`, started via `app/runtime.py` at `main.py:305` startup | VERIFIED live |
| WS reconnect | exponential backoff 2s→30s cap, resubscribes | VERIFIED live |
| REST fallback (index feed) | **NOT FOUND** — stale ticks are skipped, not backfilled from REST, in the live decision loop itself | MISSING (by design — see §B) |
| REST fallback (option chain) | `market_hub.get_chain()` -> `runtime.py::_autoscalp_chain()`, histcap-first with a throttled REST fallback | VERIFIED live |
| Instrument/expiry resolution | `instruments.py::resolve_index_future` (futures/VWAP only, AUTO mode) IS live-called; `resolve_nse_option`/`resolve_mcx_future` are **not called** from the live path — option legs come from `market_hub.get_chain()` instead | VERIFIED (for what's actually used) |
| Option-chain / OI / Greeks (dedicated subsystem) | `app/optionchain/`, `app/greeks_engine/` | DISPLAY-ONLY — correct, tested, but not upstream of live decisions. Live OI/Greeks/IV come straight off the `market_hub` chain snapshot. |
| Indicators / S/R / regime | `scalp_strategy.py::decide_from_context()` → `sr_engine.compute_sr()`, `regime_mtf.detect_regime()`+`mtf_alignment()`, `state_classifier.classify()` | VERIFIED, full call graph traced |
| Signal generation (entry/SL/T1/T2/trailing) | `option_engine.py::analyse_leg/select_option/ev_gate`, trailing computed at signal time and **applied** during position life in `paper_trading.py` | VERIFIED end-to-end |
| Position state / persistence | `ai_paper_trades` (status='OPEN'), single source of truth, queried fresh every tick before any new entry | VERIFIED |
| Runtime scheduler | `asyncio.create_task(self._loop())`, 2s default poll, DB-backed leader lease gates multi-worker races | VERIFIED |

**Two engines exist, only one is live.** `app/execution/paper_broker.py` +
`OrderManager` is a materially more sophisticated paper-execution layer
(partial fills, slippage, rejections) but is only used by `app/scalper.py`'s
`ScalpRunner`, and only when its own `execution_enabled` flag is on
(default `False`). The autoscalp loop that's actually running uses the
simpler `app/engines/paper_trading.py` instead. This distinction matters
for every section below — "verified elsewhere" does not mean "verified in
the live path."

B. DATA QUALITY
----------------
**VERIFIED**: a real, per-tick, enforced gate exists —
`Safeguards.check_entry()` (`app/autoscalp/safeguards.py`) requires
`feed_age_sec <= max_feed_age_sec` (default 12s) and `feed_connected`
truthy, called from `runner.py::_evaluate()` before every entry decision.
On stale/disconnected data the tick returns early with a recorded reason —
no trade opens. Directly tested: `test_safeguards_stale_feed_and_disconnect_fail_closed`
— **passes**.

**PARTIALLY VERIFIED against your exact spec**: you asked for four
discrete classifications (FRESH / STALE / INVALID / MISSING) per signal.
What actually exists is a binary age-threshold + connectivity check, not a
4-state classifier with that literal vocabulary. The practical effect is
equivalent for the cases that matter (stale or disconnected data blocks
the trade either way), but there's no single function that labels a tick
FRESH/STALE/INVALID/MISSING as such, and "malformed quote" as a distinct
INVALID category was not found as a named check anywhere in the live path.

**MISSING**: REST fallback for the index/WS feed itself when stale (the
option-chain has one; the index tick feed does not) — the system's actual
behavior is "skip the tick and wait for the next one," which is fail-safe
but not a fallback in the sense you described.

C. SIGNAL ENGINE
-----------------
VERIFIED, full call graph traced (see §A). The final signal dict is built
in `scalp_strategy.py`/`option_engine.py` and persisted into
`ai_paper_trades`. Checking the exact field list you specified against
what's actually stored:

| Required field | Status |
|---|---|
| signal_id | VERIFIED |
| timestamp | VERIFIED (`opened_ts`/`closed_ts`) |
| symbol / underlying / expiry / strike / CE-PE | VERIFIED |
| direction / entry / SL / T1 / T2 | VERIFIED (stored — but see §F, T2 is stored and never checked) |
| risk / reward / RR | NOT CONFIRMED as distinct stored numeric columns — `risk_ref` exists but its exact semantics weren't traced this pass |
| confidence | VERIFIED (`confidence` column) |
| regime | VERIFIED (`market_regime` column) |
| data_quality | **MISSING** — no column exists |
| reason_codes | **PARTIALLY VERIFIED** — one free-text `reason` column exists, not a structured list of codes |

D. NO-TRADE ENGINE
-------------------
Directly tested (`test_safeguards_*`, `test_runner_safeguard_blocks_entry`
— 7/7 pass against the real live path, not the unused sibling engine).

| Condition | Verdict |
|---|---|
| Stale data | VERIFIED |
| Feed/broker disconnected | VERIFIED |
| Missing/insufficient OI | VERIFIED (`scalp_strategy.py` rejects below `min_coverage`) |
| Missing Greeks | **PARTIALLY VERIFIED** — computed and *logged* as a data-quality flag, explicitly commented in code as never blocking the trade |
| Abnormal spread | VERIFIED (`max_spread_pct`) |
| Insufficient liquidity | PARTIALLY VERIFIED — only via premium-band proxies, no direct volume/depth check found |
| Poor RR/EV | VERIFIED (explicit EV gate) |
| Unclear regime | **NOT FOUND** as a named gate — regime classification always returns some label |
| Conflicting signals | **NOT FOUND** as a named gate |
| Duplicate signal | VERIFIED (see §G) |
| Daily loss limit | VERIFIED, DB-derived (not a static config check) |
| Position limit | VERIFIED, DB-derived |
| Broker/data connection unhealthy | VERIFIED (same gate as stale-feed) |
| Contract resolution uncertain | **NOT FOUND** as an explicit named reject — a failed chain fetch is logged (`last_error`) but whether an empty chain forces NO_TRADE downstream wasn't confirmed this pass |

**NO TRADE is confirmed as a real, reachable outcome, not merely a config
default** — the tests above exercise it directly and it passes.

E. RISK ENGINE
---------------
VERIFIED: daily loss limit and max-concurrent-position limit are both
enforced inline in `check_entry()`, computed from real DB rows each check
(not a cached/static number). VERIFIED: the kill switch
(`app/execution/killswitch.py`) is DB-persisted (survives restart) and is
the *first* check in `check_entry()` — it gates the **paper path**, not
only the (disabled) live broker path. VERIFIED: an EV/RR gate exists and
rejects low-quality setups before they become signals.

F. PAPER EXECUTION
-------------------
**VERIFIED, paper-only, no real-order path reachable.** `open_trade()` in
`app/engines/paper_trading.py` is the single call site of `db.insert_trade`
in the whole app; grepped for any AngelOne order-placement call reachable
from it — none exists. This is the ONLY execution engine the live
autoscalp loop actually uses.

Exit coverage in that live path:
- Target (T1): VERIFIED
- Stop-loss: VERIFIED
- Trailing stop: VERIFIED (ratcheting, applied live, never loosens)
- Timeout/max-hold: VERIFIED (`exit_reason="TIME"`)
- Manual/forced exit: VERIFIED (default close path; `MANUAL`-strategy trades are explicitly never auto-closed, by design, since the app can't square a real broker position in that mode)
- **Target 2 (T2): MISSING** — stored on every row, never read or checked as an exit trigger
- **Rejected order: MISSING/not applicable** in this path — every call unconditionally opens a position (no rejection concept exists here; it does exist in the unused sibling engine)
- **Partial-fill simulation: MISSING** in this path (exists only in the unused sibling engine)
- **Slippage simulation: MISSING** in this path (exists only in the unused sibling engine, and separately as a pre-trade EV-gate input — neither touches the P&L this path records)

G. DUPLICATE-SIGNAL PROTECTION
-------------------------------
**VERIFIED for actual trades.** Every tick, the runner queries currently-
open positions fresh from the DB (`_open_positions()`) and
`Safeguards.check_entry()` rejects re-entering an already-open
`(underlying, side)`. This is DB-backed, so it survives a process restart
(not an in-memory set that resets). Multi-worker safety is a DB-backed
leader lease — only the leader ticks, so concurrent workers can't
double-fire. Directly tested (`test_safeguards_concurrency_duplicate_consec_and_dailyloss`
— passes).

**Gap, low practical severity**: the *separate* `final_signal_gate`'s own
Telegram-alert dedup state (`_ACTIVE_SIGNAL`/`_LAST_APPROVED`) is an
in-memory dict — a restart clears it. This affects only whether a
duplicate *Telegram alert* could go out around a restart; it has no
effect on paper-trade opening (a structurally separate, opt-in,
shadow-mode-by-default gate).

**MISSING at the execution-layer level**: `paper_trading.py::open_trade()`
itself has no dedup logic of its own — every call unconditionally inserts
a fresh row. All duplicate protection lives in the caller (`runner.py`,
verified above). This is architecturally fine as long as `runner.py`
remains the only caller, but it means `open_trade()` is not safe to call
from anywhere else without re-implementing the check.

H. FAILURE INJECTION
----------------------
Rather than write a new, possibly-redundant test suite, I inventoried and
ran the EXISTING tests that already exercise these scenarios against the
real live path, then confirmed pass/fail directly:

- Stale feed / disconnect → fail closed: **VERIFIED** (`test_safeguards_stale_feed_and_disconnect_fail_closed`, passes)
- Duplicate/concurrent entries + daily loss: **VERIFIED** (`test_safeguards_concurrency_duplicate_consec_and_dailyloss`, passes)
- Kill switch blocks: **VERIFIED** (`test_safeguards_killswitch_blocks`, passes)
- Abnormal premium/spread: **VERIFIED** (`test_safeguards_premium_and_spread`, `test_safeguards_rejects_premium_too_rich_vs_spot`, pass)
- Restart recovery without duplicate submission: **VERIFIED**, but only in the *unused sibling* execution engine (`test_live_flow.py::test_restart_recovers_without_duplicate`, `test_order_manager.py::test_recover_abandons_paper_intent_the_broker_lost_on_restart`) — the live `paper_trading.py` path's restart safety is covered indirectly via the duplicate-position DB check (§G), not by a dedicated restart-injection test of its own. **NOT TESTED** directly for the live path specifically.
- Broker timeout / retry / circuit breaker: **VERIFIED**, again only in the unused sibling engine (`test_execution_ratelimit.py`, 12/12 pass) — **NOT TESTED** for the live path, which doesn't call a broker at all for paper fills (no timeout surface exists there to test).
- Rejected order / partial fill: **VERIFIED** in the unused sibling engine only; **MISSING/not applicable** in the live path (§F).
- Malformed quote, missing OI, missing Greeks, wrong expiry/strike: OI and spread are gate-tested (above); a dedicated "malformed quote crashes nothing" or "wrong-expiry-resolved silently" injection test was **NOT TESTED** this pass.
- Database failure (mid-write crash/lock): **NOT TESTED** — no dedicated coverage found for a raw SQLite operational error during a live write.

Total run: **74/74 pass** across the existing execution/staleness/idempotency/
ratelimit/broker-adapter/order-manager/live-flow test files, plus **7/7 pass**
on the autoscalp-specific safeguards tests (81/81 combined, all green).

No new tests were written this pass — coverage against the actually-live
path was already real and passing where it existed; the gaps above are
genuine absence of coverage, not a failing test, and closing them is
implementation work beyond a validation report's scope.

I. BACKTEST
------------
Per your own instruction not to re-run existing work from scratch, this
inventories what this repo already has (all independently re-verified as
real, not memory-trusted):

| Artifact | Verified real? | Headline verdict |
|---|---|---|
| `ORDERFLOW_STAGE11_TIME_EXIT_EDGE.md` | Yes, 215 lines read directly | NATGAS: robust edge across the full hold/stop grid, TRAIN+OOS. CRUDEOIL: the earlier "confirmed" result was a methodology bug (double-counted BUY+SELL off one spike); corrected, CRUDEOIL's TRAIN is losing — **not confirmed**, not wired. |
| `SSL_HYBRID_PRO_FINAL_VERDICT.md` | Yes | **NO-GO** — no stable OOS edge on NIFTY/BANKNIFTY futures, any variant. |
| `app/structural_break/backtest_compare.py` | Yes, tested | A comparison utility (static-vs-adaptive), not a standalone strategy backtest with its own P&L verdict. |
| `app/institutional_edge/costs.py` | Yes, real hardcoded constants | NATGAS ₹113.50/lot round-trip, CRUDEOIL ₹184.50/lot (brokerage+exchange+CTT) — real AngelOne-realistic costs, confirmed for these two instruments only; file explicitly does not extend this to others. |

No new backtest was run this pass. **NOT TESTED**: whether the currently-
live signal logic (as opposed to the orderflow research variant) has ever
been backtested end-to-end with the cost model above applied — the cost
model exists in a separate research module (`institutional_edge`), not in
the live `paper_trading.py` P&L path (§J).

J. WALK-FORWARD
-----------------
**MISSING as a reusable capability.** Four independent `walk_forward`-style
functions exist across the repo (`tri_compare/compare.py`,
`optionchain/ann_backtest.py`, `trend_swing/backtest.py`, a threshold-sweep
script) — each is its own one-off implementation, none share a common
module. Every walk-forward validation done in this repo to date has been
bespoke per research thread, not a standing harness the live system runs
against automatically. TRAIN/OOS discipline itself has been followed
consistently in individual research threads (documented, e.g., Stage-11's
explicit 10-session TRAIN / 4-session OOS split) but there is no
standing, repo-wide walk-forward test that runs as part of this
validation.

K. AI VALIDATION
------------------
**No trained ML model exists anywhere in the live decision path.**
Grepped `scalp_strategy.py`/`runner.py` for model-loading calls
(`pickle`/`joblib`/`torch`/`xgboost`/`sklearn`): zero hits. Every
"confidence"/"regime" score in the live path is deterministic rule/
threshold logic, not a fitted model — so the question "can AI confidence
bypass the risk engine" doesn't apply; there is no AI in that sense to
bypass anything with.

One real, lightweight ML model does exist (`app/optionchain/ann_confirm.py`,
online logistic regression + isotonic calibration) but it is **dark** —
zero references from `qualify.py`, `structure.py`, `api.py`, or
`autoscalp/runner.py`. It's backtest-only, consistent with a prior
session's own "ANN NOT JUSTIFIED" verdict.

`app/hcs/` (the "High-Confidence Signal" engine) is also rule-based, not
ML, and confirmed **SHADOW** — zero imports from the live scalp loop.

A real input→output→outcome join log exists for the deterministic
`final_signal_gate` (`fsg_shadow_log` table, joined against
`trade_exit_outcomes`) — but that gate is itself shadow-mode/opt-in for
Telegram publishing, separate from paper-trade opening, and `app/hcs` has
no equivalent join log of its own.

L. RUNTIME STABILITY
-----------------------
**Short sample only — this is NOT a soak test, and is reported as such.**
Live process (`oi-dashboard.service`, uptime ~4.9 hours at sample time):

- CPU: 1.1% (idle-ish sample, market-hours load not captured)
- RSS: ~231.9 MB, flat across two samples ~16s apart (no signal of a fast leak; a multi-hour trend was NOT measured)
- Threads: 11, open FDs: 19
- Feed: connected, tick age 9.3s at sample time (within the 12s staleness cutoff)
- Exceptions in the last ~5 hours of real logs: 1 — a caught, logged `JSONDecodeError` from an AngelOne broker network hiccup, handled without crashing the process (evidence the fail-safe logging pattern works in the wild, not just in tests)
- `armed: True`, `is_leader: True`, `halt_reason: None`, `last_error: None` at sample time

**NOT TESTED**: signal latency, execution latency, tick rate under real
market load, memory growth over hours/days, exception rate during actual
market-open volatility, reconnect frequency over a full session. A
genuine runtime validation needs a multi-hour-to-multi-day observation
window during live market hours, which this pass did not attempt.

M. PERFORMANCE
-----------------
No performance claim is made. §I/§J already state that no fresh backtest
was run this pass and the live signal logic's realized P&L, if computed
from `ai_paper_trades.pnl`, is **GROSS, not NET** — no brokerage, STT,
CTT, or realistic slippage is subtracted anywhere in the live paper-
execution path (`app/engines/paper_trading.py`). The cost model that
exists (`institutional_edge/costs.py`) is real and instrument-specific but
lives in a separate research module, not in the live P&L path. **Any
win-rate/expectancy number read directly off live paper trades overstates
real-world performance by this unmodeled cost — do not report such a
number as strategy performance without manually subtracting costs.**

N. REMAINING RISKS / MISSING COMPONENTS
------------------------------------------
1. **Cost model absent from the live P&L path** — gross P&L is being
   recorded and would be reported as-is unless a separate net calculation
   is added. (MISSING)
2. **T2 exit trigger stored but never checked** in the live path. (MISSING)
3. **No `data_quality` or structured `reason_codes` columns** in the trade
   journal — a single free-text `reason` field only. (MISSING)
4. **Partial-fill and slippage simulation absent** from the live paper-
   execution path (present only in an unused sibling engine). (MISSING)
5. **Missing-Greeks is logged, not enforced** — a trade can open on
   incomplete Greeks data. (PARTIALLY VERIFIED gap)
6. **No REST fallback for the index tick feed** when WS is stale — fail-
   safe (skip tick) but not a fallback in the requested sense. (MISSING,
   by current design)
7. **No reusable walk-forward harness** — every validation to date has
   been a one-off script. (MISSING as infrastructure)
8. **No multi-hour runtime observation performed** — only a short sample.
   (NOT TESTED)
9. **No dedicated DB-write-failure injection test** for the live path.
   (NOT TESTED)

O. RECOMMENDED NEXT MILESTONE
--------------------------------
Given everything above, the single highest-value next step, in order:
1. Add a `cost` column (or computed field) to the live paper-execution
   path using the already-real `institutional_edge/costs.py` constants,
   and report GROSS/COST/NET explicitly wherever paper P&L is surfaced —
   this directly closes the gap most likely to produce a misleadingly
   optimistic self-assessment.
2. Run a real, multi-day runtime observation during actual market hours
   (not a short sample) before making any runtime-stability claim.
3. Decide whether T2/partial-fill/slippage simulation are worth porting
   from the unused sibling engine into the live path, or whether the
   simpler live path is an intentional design choice — currently
   undocumented either way.

Only after these are addressed — and only with reproducible evidence, not
projection — should live-order execution be considered as a separate,
later milestone. Nothing in this report should be read as a readiness
signal for that.

---

GIT / VERIFICATION
-------------------
This was a read-only investigation: no application code was changed, no
service was restarted, no real broker orders were sent or attempted.
`git status` at the end of this pass shows the same untouched state as at
the start (only a pre-existing, unrelated auto-updating log file
modified, not by this task). No commits were made. Full test suite
re-confirmed unaffected (1423/1423 passing, matching the count from the
prior remediation phase — no code was touched this pass to regress it).

Files changed: none (application code).
Tests added: none (existing coverage against the real live path was
already substantial and directly verified instead).
Tests passed: 1423/1423 (full suite, unchanged from prior phase) + 81/81
(targeted safeguards/execution-layer re-run during this pass).
Tests failed: 0.
Commits created: none.
Remaining work: see sections N and O above.
