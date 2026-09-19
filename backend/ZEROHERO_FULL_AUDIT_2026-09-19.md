ZEROHERO CODEBASE AUDIT
=======================

Repository: /root/zerohero (backend + frontend, single git repo)
Commit: a89a01b (main, in sync with origin)
Date: 2026-09-19

Scope note: a prior architecture audit (`backend/PHASE0_AUDIT.md`, 2026-09-12)
and a prior frontend audit (`frontend/FRONTEND_AUDIT.md`, 2026-09-01) already
exist and are current on architecture/frontend. This audit builds on both
rather than re-deriving them, refreshes their numbers, and adds the
sections they didn't cover: security tooling, dependency CVEs, static
analysis, severity classification. Findings below are evidence-based
(command output quoted or file:line cited); nothing is asserted without a
check actually run this session.

ARCHITECTURE
------------
Confirmed still accurate (spot-checked, not fully re-walked): single FastAPI
app (`app/main.py`), 24 router-bearing modules (up from 20 documented
2026-09-12), 544 backend `.py` files (excl. venv), 150 test files (up from
87 on 2026-09-12 — the codebase grew substantially via this session's and
prior sessions' research work: SSL Hybrid PRO, StochRSI+Supertrend,
orderflow Stage 8-11, option-chain history_analytics).

Checked fresh this session: a 2-node circular-import scan across every
`app/**/*.py` via `ast` found **zero direct A↔B import cycles**. Not a full
transitive-cycle detector, but the common failure mode (two modules
importing each other directly) is absent.

Full detail (entrypoint, routers, background workers, DB schema, signal
engine map, execution layer, Telegram fan-out, AI/local-model map) is in
`backend/PHASE0_AUDIT.md` §1-6 — still structurally accurate; only the file
counts above have changed.

SECURITY
--------
Verified directly this session (file:line, quoted):

1. **CORS** (`app/main.py:54-64`) — pinned to a real origin by default
   (`https://chanakya.datacarepoint.com`), not wildcard; overridable via
   `CHANAKYA_CORS_ORIGINS`. `allow_methods=["*"]`/`allow_headers=["*"]` is
   fine given the origin is pinned. **VERIFIED sound.**

2. **Auth** (`app/main.py:66-99`) — opt-in bearer token OR HTTP Basic,
   gates every path except `/api/health` (including `/`, `/static/*`, `/ws`
   upgrade). Password comparison uses `hmac.compare_digest` (timing-safe).
   **Default credentials are `admin`/`admin@1234`** (`ADMIN_USERNAME`/
   `ADMIN_PASSWORD` env-var defaults, `app/main.py:70-71`) and **`.env` does
   NOT override either variable** (`grep -c` on `.env` = 0 matches). This
   means the live production instance is currently running on the
   well-known default credentials for its only auth layer.
   **Severity escalated to HIGH** (from the dependency-fork subagent's
   initial MEDIUM call): confirmed via `curl` to the GitHub API that
   `github.com/helpdeskdcp/zerohero` is a **public** repository
   (`"private": false`), and the security-sweep subagent independently
   found the exact same default (`admin@1234`) embedded as a shell fallback
   in ~8 operational scripts under `backend/scripts/`/`backend/data/*.sh`
   in that same public repo. The mechanism itself is sound (timing-safe
   compare, tight route allowlist), but the credential is not a secret —
   it's published. Anyone who finds the repo has full API access to the
   live app right now. **This is already known to the user** — a real
   login system (admin/registration/guest) is an explicitly planned,
   deferred piece of work per prior session record; not re-raising it as a
   fresh ask, but the public-repo fact is new information this audit
   surfaced that changes the urgency, so it's reported at its evidence-based
   severity rather than left at the prior MEDIUM assumption.

3. **SQL injection** — `bandit` flagged 38 medium-severity
   `hardcoded_sql_expressions` findings (f-strings near `execute()`).
   Spot-checked two representative patterns:
   - `app/autoscalp/report.py:34-52` — the interpolated `{ist}` is a fixed
     code literal, not user input; the actual variable (`day`) goes through
     a `?` placeholder. **Not exploitable.**
   - `app/db.py:518-526` (`update_trade`) and `:571-577`
     (`update_smart_scalper_signal`) — column *names* are built from
     `fields.keys()` via f-string (can't be parameterized), values go
     through `?`. Traced every caller (`grep`, 13 call sites across
     `scalper.py`, `positions_routes.py`, `paper_trading.py`,
     `smart_index_scalper/paper_engine.py`) — **every single one passes a
     hardcoded literal dict** (e.g. `{"stop_loss": new_sl}`), never a
     caller-controlled key set. **Not currently exploitable.**
   **Verdict: the 38 bandit hits are false positives in current usage**, but
   the `update_trade`/`update_smart_scalper_signal` pattern (column names
   from dict keys) is a **fragile design** — it becomes a real injection
   vector the day someone adds a caller that spreads request-controlled
   keys into that dict. **Severity: LOW (code-quality/defense-in-depth,
   not a live vulnerability).** Recommend a follow-up: assert `fields.keys()
   ⊆ {known column allowlist}` inside those two functions rather than
   trusting every caller forever — cheap, backward-compatible, closes the
   class of bug permanently. Not applied in this audit (audit is read-only
   per your brief's own Phase 0 rule; flagging for your decision).

4. **Secrets in source** — `.env` is gitignored and confirmed **not
   tracked** (`git ls-files` = no match). No secrets found in the files
   checked directly this session (`app/main.py`, `app/db.py`,
   `app/orderflow/*`); the dedicated grep sweep (item 7 below) confirms
   this across the full tree.

5. **No live order-placement path** — reconfirmed via `app/mcp_server.py`
   (145 lines, 6 tools, all read-only: candles/LTP/positions/signal/scalp/
   reversal; explicit docstring "NO order-placement tool exists here";
   `app/execution/killswitch.py` present). Matches every prior session's
   finding — paper-only, unchanged. Independently reconfirmed the LIVE
   broker-order triple-gate this session too: `app/execution/__init__.py`
   requires `execution_mode=="LIVE" AND CHANAKYA_ALLOW_LIVE=="1" AND` a
   non-empty `CHANAKYA_LIVE_CONFIRM_TOKEN`; `.env` sets none of these
   (`grep -c CHANAKYA_ALLOW_LIVE .env` = 0). **VERIFIED off.**

6. **DB safety** — `PRAGMA journal_mode=WAL` set (`app/db.py:395`, good for
   concurrent readers), connection lifecycle via `@contextmanager`, 14
   tables / 12 explicit indexes. Test isolation mechanism
   (`scripts/run_tests.sh`) asserts the live DB's inode is unchanged after
   every test run — a real, verified guardrail against the exact failure
   mode ("tests corrupt the live DB") this class of bug usually causes.

7. **Security grep sweep (completed)** — dedicated subagent covered
   secrets/`eval`/`exec`/`shell=True`/`pickle.loads`/`yaml.load`/path-
   traversal/TLS-verify-disabled/DEBUG-mode across the full tree
   (excluding venv). Results:
   - `eval`/`os.system`/`shell=True`/`pickle.loads`/unsafe `yaml.load`: none
     found anywhere in `app/` or `scripts/`.
   - One `exec(compile(...))` in two offline research scripts
     (`scripts/regime_adaptive_option_backtest*.py`) — operates on a
     hardcoded local `Path`, not reachable via any API route. **INFO.**
   - Path traversal: none found — no route accepts a client-supplied
     filename/path and opens/serves it.
   - `verify=False` (TLS bypass): none found.
   - `DEBUG=True` / debug-mode: none found.
   - Independently confirmed the same default-credential finding (item 2)
     is ALSO embedded as a shell fallback (`${CHANAKYA_ADMIN_PASSWORD:-admin@1234}`)
     in ~8 operational scripts under `backend/scripts/`/`backend/data/*.sh`.

BUGS / RUNTIME
---------------
**Confirmed real bug, found via the full test-suite run (1 failure out of
1411 tests)**: `tests/test_nse_mcx_pipeline_audit.py::
test_dynamic_expiry_and_atm_resolver_uses_master` failed with:
```
AssertionError: OK and '01OCT2026' == '24SEP2026'
```

```text
Observed symptom
  resolve_nse_option("NIFTY", "AUTO", "ATM", "CE", spot=22000) returned
  the FARTHER expiry (01OCT2026) instead of the nearer one (24SEP2026)
        ↓
Immediate cause
  app/instruments.py:188 -- `valid = sorted({r["expiry"] for r in rows if
  r["expiry"]})` sorts expiry strings LEXICOGRAPHICALLY (plain string
  sort), not chronologically. "01OCT2026" < "24SEP2026" as strings (the
  leading '0' beats '2'), even though Sep 24 is chronologically before
  Oct 1. `valid[0]` (used for AUTO/CURRENT) then picks the wrong contract
  whenever two expiries span a month boundary where the new month's
  day-of-month digit is smaller than the old month's.
        ↓
Root cause
  This exact bug class was already found and fixed -- TWICE -- in this
  same file's two sibling functions. `resolve_mcx_future` (line ~222)
  carries an explicit docstring warning: "Must NOT lexically sort expiry
  strings ('20NOV2026' < '25SEP2026')... both bugs picked a dead far/
  option token" -- and both `resolve_mcx_future` and `resolve_index_future`
  correctly parse each expiry to a `date` via a local `_d()`/`_expiry_date()`
  helper and sort on THAT. `resolve_nse_option` (the third, oldest sibling)
  was never given the same fix -- it still does the original plain
  `sorted({...})` on raw strings, despite defining its own correct `key()`
  date-parser two lines below (used only for the >= now filter, not for
  ordering).
        ↓
Why existing tests didn't catch it until now
  The test itself commits to only RELATIVE dates specifically "so AUTO
  does not roll past a hard-coded past date" -- i.e. it was written to be
  robust to time passing, which is good practice, but it means the test's
  actual PASS/FAIL outcome depends on today's date: it only fails when
  today + 6 days and today + 13 days happen to land in different months
  with an inverted day-of-month comparison. Today (2026-09-19) triggered
  that condition (6 days out = Sep, 13 days out = Oct, and the resulting
  day-of-month comparison inverts lexically). On most days of the month
  this exact test would silently pass despite the underlying code being
  wrong -- the bug was real all along, just usually untriggered by this
  particular test's month-independent date math.
        ↓
Blast radius (checked, not assumed)
  grep across the ENTIRE repo (`grep -rln resolve_nse_option . --include
  "*.py"`, excluding venv) found exactly 2 hits: the function's own
  definition and its own test file. Zero production callers. The live
  option-chain path (`app/optionchain/resolve.py`, confirmed by reading
  its imports) uses entirely separate source modules
  (angelone_chain/upstox_open/nse_v3), not this function. **This is
  currently dead code as far as the live/paper trading path is concerned**
  -- confirmed by direct search, not inferred.
        ↓
Permanent fix (proposed, NOT applied)
  Mirror the exact pattern already proven correct in the two sibling
  functions two lines below: replace
  `valid = sorted({r["expiry"] for r in rows if r["expiry"]})` with a sort
  keyed on the already-defined `key(x)` parser, e.g.
  `valid = sorted({r["expiry"] for r in rows if r["expiry"]}, key=key)` --
  a one-line change, zero behavior change for same-month expiry pairs,
  correct behavior for cross-month pairs. The existing failing test
  becomes the regression test once fixed.
```

**STATUS: PATCHED — NOT APPLIED.** This is real, reproducible, root-caused,
and the fix is a proven one-line pattern already used twice elsewhere in
the same file — but per this session's standing practice of not touching
option/expiry-resolution code without explicit authorization (even
currently-unused code, since it's exactly the kind of function someone
wires into a live path later without re-auditing it), **I did not apply
it.** Recommend: apply the one-line fix, `pytest
tests/test_nse_mcx_pipeline_audit.py -q` to confirm, since it costs
nothing today (dead code) and prevents a real bug from reactivating
silently if `resolve_nse_option` is ever wired in.

**Severity: MEDIUM** (real, root-caused, confirmed-exploitable-if-called
bug — downgraded from what would otherwise be HIGH/CRITICAL only because
blast radius is verified zero today).

Beyond this, the rest of the run:
`ruff check app/ --select E9,F` (syntax errors + undefined names, the two
categories that indicate an actual runtime crash risk): **zero E9, zero
F821** — no syntax errors, no undefined-name bugs found across all 544
files. The 94 findings that DID surface are 100% cosmetic: 52×`F401`
(unused import) and 42×`F841` (unused local variable), spread across
~30 files, concentrated in `app/hcs/`, `app/structural_break/`,
`app/research_engines/`, `app/expiry_zero_to_hero/`. None of these can
crash the app (Python doesn't error on unused names). **Severity: INFO** —
safe auto-fixable cleanup (`ruff check --fix` handles 52 of the 94), no
behavior change, but out of scope to apply without your go-ahead per the
"smallest safe change, nothing unasked" rule.

Zero `TODO`/`FIXME`/`XXX` markers found in `app/` outside `research_engines/`
— either genuinely clean or (more likely given the scale) the convention
here is memory files / doc comments instead of inline markers.

API / DATABASE
---------------
No route/schema contract audit re-run this session — `FRONTEND_AUDIT.md`
(2026-09-01) already did a full field-by-field diff of all 23 (now more,
unverified current count) consumed endpoints against their render code and
found zero mismatches after its own fixes; not stale enough to be
suspicious of regression given how narrowly scoped the intervening commits
were (orderflow-only). Flag: if you've added new frontend-consuming routes
since 2026-09-01, that diff should be re-run — I did not do it this
session, marking **NOT TESTED** rather than assuming it still holds.

TESTING
-------
Full suite run completed (`scripts/run_tests.sh`, DB-isolated, 317.52s):
**1410 passed, 1 failed** (up from the stale 2026-09-12 figure of 934 — the
codebase has grown substantially). The 1 failure is the
`resolve_nse_option` AUTO-expiry bug documented above in BUGS/RUNTIME — not
a flaky/environmental failure, a real regression test correctly catching a
real bug. Live DB inode unchanged after the run (isolation guardrail held).
Orderflow-specific suite (this session's own change surface): 39/39
passing, confirmed multiple times today.

Coverage gaps (from the file listing, not measured with a coverage tool):
`app/optionchain/history_analytics.py` — 98/98 related tests pass but the
feature itself is uncommitted (see MEDIUM PRIORITY). No dedicated test file
exists for `app/orderflow/service.py` or `app/orderflow/notify.py` (the
live-wiring changed today) — covered indirectly via
`test_orderflow_smart_money.py`/`test_orderflow_backtest.py` at the
`smart_money_setups()`/`backtest()` layer, but `service.smart_money()`'s
own caching key and `notify.signal_card()`'s entry_mode label branch have
no direct unit test. **Recommend**: a small test file for
`app/orderflow/notify.py::signal_card` (immediate vs breakout label) and
`service.py::smart_money`'s cache-key change, given they were touched by
today's live-wiring change and are the last line before a real Telegram
send.

DEPENDENCIES
------------
`requirements.txt` (12 pinned packages) audited with `pip-audit` (installed
fresh this session, dev-only, does not touch runtime):

**Found 22 known vulnerabilities across 4 direct/transitive packages:**

| package | installed | advisory | fix version |
|---|---|---|---|
| requests | 2.32.3 | PYSEC-2026-1872 | 2.32.4 |
| requests | 2.32.3 | PYSEC-2026-2275 | 2.33.0 |
| python-dotenv | 1.0.1 | PYSEC-2026-2270 | 1.2.2 |
| pytest | 8.3.3 | PYSEC-2026-1845 | 9.0.3 |
| starlette | 0.38.6 | 7 distinct advisories | 1.3.1 (latest fixed line) |

**Severity: MEDIUM** — `requests` is used for the Telegram bot API and
broker HTTP calls (network-facing, worth patching); `starlette` is
FastAPI's ASGI layer (also network-facing, has the most advisories).
`pytest`/`python-dotenv` are lower-exposure (test-only / startup-only).
Per your own rule 10 ("do not upgrade blindly"): every one of these is a
minor/patch-level bump per `pip list --outdated` (starlette is the outlier
— 0.38.6 → 1.3.1 is a major version jump bundled with FastAPI's own
version; upgrading starlette alone without bumping FastAPI in lockstep
risks an incompatibility, since FastAPI 0.115.0 pins a starlette range).
**Recommendation, not applied**: bump `requests` and `python-dotenv`
first (low-risk, isolated); treat the `fastapi`+`starlette` pair as one
combined upgrade decision requiring the existing test suite as the gate,
given FastAPI 0.115.0 → latest is also 19 minor versions behind.

Static analysis (`ruff`, `bandit` — both installed fresh, dev-only):
results folded into BUGS/RUNTIME and SECURITY sections above.

No `package.json` in `frontend/` — it's vanilla HTML/CSS/JS with no build
step and no npm dependency tree, so there is no JS dependency surface to
audit (confirmed via `find frontend -iname package.json` = no match).

PERFORMANCE
-----------
**Measured**: nothing profiled this session (no load test run — would need
explicit authorization to hit the live paper-trading process).

**Potential optimization, not measured**: `app/combos.py:120,159` —
`rows = [db.get_trade(t) for t in c["legs"]]` issues one DB round-trip per
leg inside a loop (classic N+1 shape). Given combo legs are typically 2-4
(options strangle/combo), and SQLite is local, this is very unlikely to be
a real bottleneck — flagging as a code-quality note, not a performance
finding, since it wasn't measured.

AI/ML
-----
`app/mcp_server.py` — 6 read-only MCP tools exposed to an external Claude
client (candles/LTP/positions/signal/scalp/reversal), no order-placement
tool, no free-text prompt accepted into the trading decision path (so no
prompt-injection surface into signal generation specifically — the MCP
tools are outputs, not inputs, to the trading logic). No XGBoost/model-
training code found in `app/` (matches prior session's own note that this
was deliberately not built yet).

DEPLOYMENT
----------
Single systemd unit (`oi-dashboard.service`, confirmed `active` this
session via `systemctl is-active`), runs in-place from
`/root/zerohero/backend` (no separate build/deploy step — a `git commit`
to a live file IS the deploy, confirmed today via the orderflow live-wiring
change). Cron-driven signal watchers run read-only/notify-only, none place
orders (per `PHASE0_AUDIT.md` §6, re-confirmed this session for
`orderflow_signal_watch.sh` specifically since it was edited today).

OBSERVABILITY
-------------
`/api/health` exists and is the one auth-exempt route (used for
liveness). 17 of 544 files use structured `logging.getLogger` — a modest
footprint; most of the codebase relies on the return-value/DB-log pattern
(e.g. `fsg_shadow_log`, `ai_signals_log`) rather than app-level logging for
trading decisions specifically, which is arguably the right call for this
domain (decisions need to be queryable, not just grep-able in a log file).

CRITICAL FINDINGS
-----------------
None found. No live order-execution path, no SQL/command injection
confirmed exploitable, no secrets leaked, no syntax/undefined-name errors,
no TLS bypass, no data-loss risk found.

HIGH PRIORITY
-------------
1. **Default HTTP Basic credentials (`admin`/`admin@1234`) are live in
   production, unrotated, with no `.env` override — and the exact default
   is published in this app's own PUBLIC GitHub repository**
   (`github.com/helpdeskdcp/zerohero`, confirmed `"private": false` via the
   GitHub API), both in `app/main.py`'s fallback default and duplicated
   across ~8 operational scripts. Anyone who finds the repo has working
   credentials to the live API. Escalated from the initial MEDIUM
   assessment once the public-repo fact was checked — see Security §2.
   **This is a known, already-scoped, deferred decision** (real login
   system planned) — reported at its evidence-based severity, not as a new
   ask.

MEDIUM PRIORITY
----------------
1. **Latent logic bug in `resolve_nse_option` (`app/instruments.py:188`)**
   — lexicographic instead of chronological expiry sort, root-caused above
   in BUGS/RUNTIME. Zero blast radius today (no live caller), would be
   HIGH the moment it's wired up. A ~2-line fix with an existing failing
   regression test ready to confirm it.
2. 22 known CVEs across `requests`, `python-dotenv`, `pytest`, `starlette`
   — see Dependencies. None are exploit-confirmed against this app's
   specific usage (advisory-level, not a demonstrated working exploit
   against this codebase).
3. `app/optionchain/history_analytics.py` feature (vol-surface/straddle-
   pnl/oi-profile, 3 new routes) — fully tested (98/98) but uncommitted;
   raised to the user in the prior turn, still pending a yes/no.

LOW PRIORITY
------------
1. `update_trade`/`update_smart_scalper_signal` column-name-from-dict-keys
   pattern — not exploitable today (all 13 call sites use hardcoded literal
   keys, verified), fragile if a future caller changes that.
2. 94 ruff findings (unused imports/locals) — cosmetic, 52 auto-fixable.
3. `combos.py` N+1 `get_trade()` loop — unmeasured, small legs count (2-4).
4. No dedicated unit test for `orderflow/service.py::smart_money`'s cache
   key or `orderflow/notify.py::signal_card`'s entry_mode label branch,
   both touched by today's live NATGAS-signal change.
5. 6 FastAPI `on_event` deprecation warnings (lifespan handlers are the
   replacement) — no functional impact today, will need addressing on a
   future FastAPI major-version bump.

RECOMMENDED FIX ORDER
----------------------
1. (Your call, highest evidence-based priority) Rotate `CHANAKYA_ADMIN_PASSWORD`
   and set it in `.env` (not committed) — the credential is public, this is
   the one finding with real current exposure, independent of the broader
   login-system project.
2. (Your call) Fix the `resolve_nse_option` sort bug — 2-line change,
   existing test proves it (`tests/test_nse_mcx_pipeline_audit.py::test_dynamic_expiry_and_atm_resolver_uses_master`
   currently RED, would go GREEN). Cheap, isolated, zero risk since nothing
   calls this function yet.
3. (Your call) Bump `requests`/`python-dotenv` (low-risk patch versions).
4. (Your call) Add a column-name allowlist assertion to `update_trade`/
   `update_smart_scalper_signal` — 10-line, backward-compatible, closes a
   currently-theoretical injection class permanently.
5. (Your call, low urgency) `ruff check --fix` for the 52 auto-fixable
   unused-import findings — zero behavior change.
6. (Deferred, already known) Real login system — not re-raising as new.
7. FastAPI/starlette major-version upgrade — bundle together, gate on full
   test suite, not urgent (no CRITICAL finding depends on it).

VERIFICATION RESULTS
---------------------
- `ruff check app/ --select E9,F`: **RUN, 0 syntax errors, 0 undefined
  names, 94 cosmetic findings** — evidence quoted above.
- `bandit -r app/ -ll`: **RUN, 38 medium findings, both spot-checked
  patterns traced to all call sites and confirmed not exploitable** —
  evidence quoted above.
- `pip-audit -r requirements.txt`: **RUN, 22 known CVEs found** — table
  above, sourced directly from tool output.
- 2-node circular-import scan: **RUN, 0 cycles found**.
- Full backend test suite (`scripts/run_tests.sh`, 317.52s): **RUN, 1410
  passed, 1 failed** — the 1 failure is a real bug (see BUGS/RUNTIME),
  root-caused, live-DB inode confirmed unchanged after the run.
- Security grep sweep (secrets/dangerous-exec/path-traversal patterns):
  **RUN, completed** — see Security §7. No eval/os.system/shell=True/
  pickle.loads/TLS-bypass/DEBUG-mode found; default-credential finding
  independently reconfirmed.
- GitHub repo visibility: **RUN, confirmed public** via GitHub API.
- Live-order-execution triple-gate: **RUN, confirmed off** (`.env` sets
  none of `CHANAKYA_ALLOW_LIVE`/`CHANAKYA_LIVE_CONFIRM_TOKEN`).

REMAINING RISKS
----------------
- This audit did not re-run the frontend/backend contract diff (deferred
  to the existing 2026-09-01 FRONTEND_AUDIT.md, itself already 18 days old
  relative to today) — mark **NOT TESTED** for any route added since.
- No load/performance testing was run — the PERFORMANCE section is
  observational only, not measured under load.
- Per your own template: this is **NOT** a "production ready" or "100%
  safe" claim — it is VERIFIED for what was directly checked, LIKELY for
  reasoned-but-unmeasured items (e.g. combos.py N+1), and UNKNOWN/NOT
  TESTED for anything explicitly marked as such above.
