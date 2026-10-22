## Latest operational staging validation — 2026-10-02

Manual ingestion now supports root-level `--ingest`, read-only `--dry-run` and
`--status`, bounded CSV/ZIP/gzip/XLSX parsing, schema detection and a 28-combination
coverage report. Rejected parses do not enter the ledger. Optional browser acquisition
honors per-origin robots exclusions and remains untested against live exchanges.

Final validation: **759 relevant tests + 4 Phase-2 diagnostics passed**; 48 new
regression cases; zero final failures/errors/skips/warnings. All 600 pre-existing
application/test files were preserved byte-for-byte. No network or broker calls,
orders, production migrations, service restarts or calendar activation occurred.

**Implementation ready; historical evidence not verified.** Official/staged archives
acquired: **0/0**. VERIFIED **0**, PARTIALLY_VERIFIED **0**, UNVERIFIED **28**,
CONFLICTING **0**, NOT_APPLICABLE **0**. Production Calendar remains **NOT INSTALLED**;
unresolved AUTO/NEXT remains **FAIL-CLOSED**. Deployment: **NOT READY — evidence missing**.

See [operational report](calendar_evidence_acquisition/OPERATIONAL_STAGING_REPORT.md),
[operator commands](calendar_evidence_acquisition/EVIDENCE_ENGINE.md) and
[28-combination matrix](calendar_evidence_acquisition/operational_validation_20261002/coverage_matrix.json).
Earlier reports below are retained as historical records.

---

# Track B — Phase 3 validation investigation

Investigation date: 2026-10-02. **No calendar behavior, production module, existing test or assertion was changed.** This package classifies the 36 saved full-suite failures and seven file timeouts; it does not claim a green full suite.

Companion: [complete per-incident classification](validation/phase3_failure_classification.json). Every incident has one A–H classification, its original exact failure, file/test name, current and pre-Phase-3 reproduction, Phase 3 involvement assessment, source references, recommended fix, and hashed log/XML references.

## Classification totals

Choose the concrete C–G cause instead of the broad B label. Thus **B=0 does not mean the failures began in Phase 3**: all 36 saved cases fail with pre-Phase-3 app source, and all seven timeout families also exceed bounded pre-Phase-3 replay limits. Baseline existence is evidence, not a second classification.

| Class | Saved test failures | File timeout incidents | Total |
| --- | ---: | ---: | ---: |
| A — Regression caused by Phase 3 | 0 | 0 | 0 |
| B — Pre-existing repository failure (functional; no more specific C-G cause) | 0 | 0 | 0 |
| C — Test isolation violation | 1 | 0 | 1 |
| D — Filesystem/database contamination dependency or attempted access; blocked before contamination | 28 | 0 | 28 |
| E — Environment-dependent failure | 2 | 6 | 8 |
| F — Timeout/resource issue | 3 | 1 | 4 |
| G — Date/time-dependent fixture | 2 | 0 | 2 |
| H — Other, with evidence | 0 | 0 | 0 |

D means a test attempts or depends on non-temporary file/database access. **The guards prevented actual production contamination.** The exact low-level sandbox policy causing EPERM was not changed or bypassed.

## Method and reproducibility

- Inspected the current implementation, original Phase 3 report, 200 completed JUnit files, 207 file execution records, and saved traces. The original full run reported **2,192 passed / 36 failed / 0 errors / 8 skipped**, with **seven additional files unfinished** at 90 seconds. Those unfinished cases are excluded from the totals.
- Replayed all 36 exact failing node IDs individually, with unchanged tests and the original 10-second per-test budget plus a 22-second process bound. Current code: **35 failed, 1 passed**. Saved pre-Phase-3 app source: **36 failed**. F14 is resource-sensitive and passed in one current isolated run; that is not a repository fix.
- Replayed the seven entire timed-out files against current and pre-Phase-3 source: **seven process timeouts in each mode**, using a **35-second investigation limit**. This reproduces the blocking families, not the original 90-second threshold. Targeted uncaptured traces identify concrete blocking tests below; the original quiet logs did not retain exact per-node attribution.
- Pre-Phase-3 source came from `/tmp/phase3_before.json`. A read-only Python import loader executes the saved `app/` source while retaining current module file locations/resource paths. It does not reset, copy over, checkout or edit the working tree. Test files involved here are unchanged. Baseline snapshot SHA-256: `d186eb4946a7e3eecda75e890802e4858b0da068d2e803525437eaf5698078be`.
- Independent controls changed only temporary test boundaries: 18 L2-path cases, 10 lock-path cases, two fixture-clock cases and three measured-budget cases. **32/33 controls passed**. F13 progressed beyond its time limit but then failed on a non-temporary market-history DB access. Control passes are not substituted for the original failed results.
- All seven targeted timeout traces were collected separately. Six async cases still timed out; the ablation determinism case passed in **42.31 seconds** with a 50-second per-test budget. Whole-file completion is still unverified.
- Calendar/Phase 2 validation ran again after investigation: **623 passed / 0 failed / 0 errors / 0 skipped**, and **4 passed / 0 failed / 0 errors / 0 skipped**. Warnings were errors; no warnings were waived. No coverage percentage was collected.

## Root causes and recommended repairs

| Evidence group | Finding | Repair recommendation; not applied to production/tests here |
| --- | --- | --- |
| L2: F01, F15–F21, F24–F33 | `depth.L2_DB_PATH` imports `l2capture.store._DEFAULT_PATH`; it ignores the temporary main DB and targets `backend/data/l2_capture.db`. Shadow catches the guard exception and returns None before persistence. The reverse-engineering missing-market test also calls this unmocked reader. All 18 pass with a temporary absent L2 path. | Isolate the secondary DB or mock the depth boundary explicitly in these unit fixtures. Keep absent-data semantics and original assertions. |
| Lock: F02–F11 | `hedging.runner._LOCK_PATH` targets `backend/data/.hedging_runner_scan.lock`. Nine tests fail at the guarded write; F08 sees an error-shaped persisted result without `armed`. All 10 pass with only the lock path redirected to temporary storage. | Use `tmp_path` for the lock fixture; retain actual locking and scan assertions. Do not remove the production lock. |
| CLI: F34 | The watchdog self-test launches an unguarded subprocess; audit hooks would not automatically survive into that child. Its DB self-test may inspect production history. | Test the pure function separately; run CLI coverage with a seeded temporary DB and inherited safety boundaries. Do not globally allow subprocesses or skip assertions. |
| Clock: F35–F36 | MCX fixture expiry `23SEP2026` is expired at the test date 2026-10-02. AUTO correctly rejects it/selects October. Both pass with the client clock frozen at a synthetic 2026-09-01 timestamp. | Freeze fixture time, retain chronological ordering/contract assertions and separately cover post-expiry. Do not change current provider filtering. No production calendar is derived from these fixtures. |
| Compute: F12–F14, T06 | Model training, full CSV sorting and repeated replay calculations exceed short budgets. F12 passes at 15.69 seconds with a 45-second budget; F14 passes in 14.01 seconds; T06 passes at 42.31 seconds with 50 seconds. F13 reaches another non-temporary DB blocker at 16.70 seconds. | Keep functional/determinism assertions; distinguish deterministic unit data from pinned research-data integration jobs, isolate all DB paths, and measure realistic budgets. Do not present a timeout as a failed financial assertion. |
| Async: F22–F23, T01–T05, T07 | Event-loop/thread/AnyIO waits reproduce without Phase 3. An application-free, audit-hook-free probe reports `PermissionError: [Errno 1] Operation not permitted` in `asyncio.selector_events._write_to_self` at `csock.send`. A scheduled timer wakes an otherwise delayed callback. | Permit local event-loop self-pipe/socketpair IPC in the test environment while retaining outbound broker/network guards. Keep thread-offload assertions intact; replacing `to_thread` with inline calls would invalidate F22’s purpose. |

## Every saved failure

“Before” means the saved pre-Phase-3 app source. All rows below fail before Phase 3; none has a calendar resolver in the identified failing path. Transitive imports of `app.main` or option-chain modules are not causal attribution. Detailed per-row source references and fixes are in the JSON companion.

| ID | Test file and exact test name | Class | Exact saved failure reason | Current / before |
| --- | --- | --- | --- | --- |
| F01 | `tests/test_ai_shadow.py::test_ai_called_and_persisted_when_required_and_configured` | D | `IndexError: list index out of range` | FAIL / FAIL |
| F02 | `tests/test_hedging_runner.py::test_disarmed_scan_evaluates_no_new_entries` | D | `RuntimeError: Phase 3 tests prohibit writes outside temporary storage` | FAIL / FAIL |
| F03 | `tests/test_hedging_runner.py::test_armed_scan_opens_a_position_and_notifies` | D | `RuntimeError: Phase 3 tests prohibit writes outside temporary storage` | FAIL / FAIL |
| F04 | `tests/test_hedging_runner.py::test_portfolio_margin_ceiling_blocks_new_entry` | D | `RuntimeError: Phase 3 tests prohibit writes outside temporary storage` | FAIL / FAIL |
| F05 | `tests/test_hedging_runner.py::test_armed_scan_respects_max_positions_per_symbol` | D | `RuntimeError: Phase 3 tests prohibit writes outside temporary storage` | FAIL / FAIL |
| F06 | `tests/test_hedging_runner.py::test_no_verified_lot_size_is_no_trade_not_fabricated` | D | `RuntimeError: Phase 3 tests prohibit writes outside temporary storage` | FAIL / FAIL |
| F07 | `tests/test_hedging_runner.py::test_bad_symbol_evaluation_is_error_not_crash` | D | `RuntimeError: Phase 3 tests prohibit writes outside temporary storage` | FAIL / FAIL |
| F08 | `tests/test_hedging_runner.py::test_scan_and_record_persists_result_for_polling` | D | `KeyError: 'armed'` | FAIL / FAIL |
| F09 | `tests/test_hedging_runner.py::test_entry_gate_blocks_new_positions_outside_session` | D | `RuntimeError: Phase 3 tests prohibit writes outside temporary storage` | FAIL / FAIL |
| F10 | `tests/test_hedging_runner.py::test_kill_switch_daily_loss_limit_disarms_and_notifies` | D | `RuntimeError: Phase 3 tests prohibit writes outside temporary storage` | FAIL / FAIL |
| F11 | `tests/test_hedging_runner.py::test_kill_switch_consecutive_losses_disarms` | D | `RuntimeError: Phase 3 tests prohibit writes outside temporary storage` | FAIL / FAIL |
| F12 | `tests/test_order_pressure.py::test_models_are_deterministic` | F | `TimeoutError: Phase 3 isolated test exceeded 10 seconds` | FAIL / FAIL |
| F13 | `tests/test_order_pressure.py::test_coverage_report_is_honest` | F | `TimeoutError: Phase 3 isolated test exceeded 10 seconds` | FAIL / FAIL |
| F14 | `tests/test_orderflow_v1.py::test_entry_ready_geometry_and_r_consistency_on_real_data` | F | `TimeoutError: Phase 3 isolated test exceeded 10 seconds` | PASS (budget-sensitive) / FAIL |
| F15 | `tests/test_phase_h_five_symbol_pipeline.py::test_full_pipeline_with_mocked_ai_success_for_every_symbol[NIFTY]` | D | `IndexError: list index out of range` | FAIL / FAIL |
| F16 | `tests/test_phase_h_five_symbol_pipeline.py::test_full_pipeline_with_mocked_ai_success_for_every_symbol[BANKNIFTY]` | D | `IndexError: list index out of range` | FAIL / FAIL |
| F17 | `tests/test_phase_h_five_symbol_pipeline.py::test_full_pipeline_with_mocked_ai_success_for_every_symbol[SENSEX]` | D | `IndexError: list index out of range` | FAIL / FAIL |
| F18 | `tests/test_phase_h_five_symbol_pipeline.py::test_full_pipeline_with_mocked_ai_success_for_every_symbol[NATURALGAS]` | D | `IndexError: list index out of range` | FAIL / FAIL |
| F19 | `tests/test_phase_h_five_symbol_pipeline.py::test_full_pipeline_with_mocked_ai_success_for_every_symbol[CRUDEOIL]` | D | `IndexError: list index out of range` | FAIL / FAIL |
| F20 | `tests/test_phase_h_five_symbol_pipeline.py::test_ai_failure_never_blocks_the_deterministic_pipeline` | D | `assert None is not None` | FAIL / FAIL |
| F21 | `tests/test_reverse_engineering_status_report.py::test_status_report_missing_market_db_does_not_raise` | D | `RuntimeError: Phase 1 tests require temporary databases` | FAIL / FAIL |
| F22 | `tests/test_sdk_candle_metadata.py::test_api_run_offloads_pipeline_off_the_event_loop` | E | `TimeoutError: Phase 3 isolated test exceeded 10 seconds` | FAIL / FAIL |
| F23 | `tests/test_sdk_candle_metadata.py::test_api_run_unexpected_exception_is_logged_and_sanitised` | E | `TimeoutError: Phase 3 isolated test exceeded 10 seconds` | FAIL / FAIL |
| F24 | `tests/test_shadow_notify.py::test_pass_sends_telegram_for_both_sides[BUY_CE-CE-expected_states0]` | D | `TypeError: 'NoneType' object is not subscriptable` | FAIL / FAIL |
| F25 | `tests/test_shadow_notify.py::test_pass_sends_telegram_for_both_sides[BUY_PE-PE-expected_states1]` | D | `TypeError: 'NoneType' object is not subscriptable` | FAIL / FAIL |
| F26 | `tests/test_shadow_notify.py::test_fail_suppresses_to_no_trade_and_does_not_send` | D | `TypeError: 'NoneType' object is not subscriptable` | FAIL / FAIL |
| F27 | `tests/test_shadow_notify.py::test_uncertain_does_not_send_when_it_stays_weak` | D | `TypeError: 'NoneType' object is not subscriptable` | FAIL / FAIL |
| F28 | `tests/test_shadow_notify.py::test_high_risk_weakens_and_only_sends_if_still_strong` | D | `TypeError: 'NoneType' object is not subscriptable` | FAIL / FAIL |
| F29 | `tests/test_shadow_notify.py::test_high_risk_weak_state_sent_when_include_weak_enabled` | D | `TypeError: 'NoneType' object is not subscriptable` | FAIL / FAIL |
| F30 | `tests/test_shadow_notify.py::test_duplicate_signal_sent_only_once` | D | `TypeError: 'NoneType' object is not subscriptable` | FAIL / FAIL |
| F31 | `tests/test_shadow_notify.py::test_telegram_failure_does_not_block_shadow_decision` | D | `assert None is not None` | FAIL / FAIL |
| F32 | `tests/test_shadow_notify.py::test_telegram_send_fn_raising_never_propagates` | D | `assert None is not None` | FAIL / FAIL |
| F33 | `tests/test_shadow_notify.py::test_no_duplicate_across_a_simulated_restart` | D | `assert 0 == 1  +  where 0 = len([])` | FAIL / FAIL |
| F34 | `tests/test_stale_feed_watchdog.py::test_selftest_entrypoint_passes` | C | `RuntimeError: Phase 3 tests prohibit subprocesses` | FAIL / FAIL |
| F35 | `tests/test_standalone_angelone.py::test_resolver_handles_mcx_options_on_futures` | G | `AssertionError: assert ('CONTRACT_INVALID' == 'OK'      - OK   + CONTRACT_INVALID)` | FAIL / FAIL |
| F36 | `tests/test_standalone_angelone.py::test_auto_expiry_is_chronological_not_lexical` | G | `AssertionError: assert ('23OCT2026' == '23SEP2026'      - 23SEP2026   + 23OCT2026)` | FAIL / FAIL |

## Every saved file timeout

These are **file-level incidents**, not seven fully enumerated failing test cases. Named tests are concrete blockers found in targeted investigation. Other tests may also be blocked; later cases in an interrupted file are not certified passing. The JSON records the last-started/probed test, reproduction limits and trace artifacts.

| ID | Timed-out file / targeted blocking test | Class | Original reason; current / before evidence | Recommendation |
| --- | --- | --- | --- | --- |
| T01 | `tests/test_autoscalp.py::test_runner_seeds_index_agg_from_broker_on_start` | E | Original 90s file timeout; both 35s file replays timeout. Targeted trace confirms async/portal wait; no-app probe confirms self-pipe EPERM. | Restore local thread/event-loop IPC in the test environment while retaining broker-network guards. Rerun the entire file unchanged, including downstream cases; do not replace thread offload with synchronous execution just for green tests. |
| T02 | `tests/test_autoscalp_final_signal_gate.py::test_gate_disabled_sends_telegram_as_before` | E | Original 90s file timeout; both 35s file replays timeout. Targeted trace confirms async/portal wait; no-app probe confirms self-pipe EPERM. | Restore local thread/event-loop IPC in the test environment while retaining broker-network guards. Rerun the entire file unchanged, including downstream cases; do not replace thread offload with synchronous execution just for green tests. |
| T03 | `tests/test_dhan_connector.py::test_worker_refresh_instruments_merges_auto_and_static` | E | Original 90s file timeout; both 35s file replays timeout. Targeted trace confirms async/portal wait; no-app probe confirms self-pipe EPERM. | Restore local thread/event-loop IPC in the test environment while retaining broker-network guards. Rerun the entire file unchanged, including downstream cases; do not replace thread offload with synchronous execution just for green tests. |
| T04 | `tests/test_dhan_postback.py::test_correct_secret_is_logged_and_needs_no_basic_auth` | E | Original 90s file timeout; both 35s file replays timeout. Targeted trace confirms async/portal wait; no-app probe confirms self-pipe EPERM. | Restore local thread/event-loop IPC in the test environment while retaining broker-network guards. Rerun the entire file unchanged, including downstream cases; do not replace thread offload with synchronous execution just for green tests. |
| T05 | `tests/test_main_auth.py::test_health_is_open_without_any_credential` | E | Original 90s file timeout; both 35s file replays timeout. Targeted trace confirms async/portal wait; no-app probe confirms self-pipe EPERM. | Restore local thread/event-loop IPC in the test environment while retaining broker-network guards. Rerun the entire file unchanged, including downstream cases; do not replace thread offload with synchronous execution just for green tests. |
| T06 | `tests/test_p61_filters_ablation.py::test_ablation_deterministic` | F | Original 90s file timeout; both 35s file replays timeout. Targeted test passes with 50s budget; full file still unverified. | Retain the deterministic equality and bucket assertions; profile/reuse immutable replay preparation where safe, distinguish unit and integration workloads, and set measured budgets. |
| T07 | `tests/test_smart_scalper_scheduler.py::test_evaluate_called_with_dry_run_false_when_armed` | E | Original 90s file timeout; both 35s file replays timeout. Targeted trace confirms async/portal wait; no-app probe confirms self-pipe EPERM. | Restore local thread/event-loop IPC in the test environment while retaining broker-network guards. Rerun the entire file unchanged, including downstream cases; do not replace thread offload with synchronous execution just for green tests. |

## Validation artifacts and safe reproduction

- New durable runner: `validation/isolated_pytest.py`; default behavior does not apply experimental fixture controls. It inherits the Phase 3 restrictions, records per-node progress/tracebacks and optionally supports the read-only baseline import overlay.
- Current calendar results: `/tmp/validation-final-calendar/result.xml`; diagnostics: `/tmp/validation-final-diagnostics/result.xml`.
- Per-case replays, before/after logs, controls and timeout traces: `/tmp/validation-investigation/`. The JSON companion contains SHA-256 references. `/tmp` files are transient; preserve them and the baseline snapshot if independent replay is required later.
- `VALIDATION_BASELINE=/tmp/phase3_before.json` selects the reviewed old source; omit it for current source. `VALIDATION_TEST_TIMEOUT` adjusts the diagnostic budget, not test assertions. Temporary experimental controls are outside the normal runner and were not installed in repository fixtures.
- Existing skipped research tests were not modified, rerun as passing, or hidden. The full 207-file sweep was **not repeated** in this investigation; the saved incidents were targeted instead.

Example from `backend/` (substitute the exact node from the table):

```bash
env -i PATH=/usr/bin:/bin VALIDATION_RUN_DIR=/tmp/phase3-review-current \
  venv/bin/python -B validation/isolated_pytest.py -q -W error \
  tests/test_ai_shadow.py::test_ai_called_and_persisted_when_required_and_configured \
  --tb=short --junitxml=/tmp/phase3-review-current/result.xml
```

Use an external process deadline for known hanging files; the per-test alarm alone cannot guarantee termination during thread cleanup. A larger budget must be documented as an experiment, not silently counted as a repair.

## Preservation and deployment

Only new checklist, classification/report and validation-runner artifacts were added. Existing application code, calendar behavior, tests, Phase 1/2/3 reports and raw/research datasets remain unchanged. All 722 snapshotted pre-existing app/test/report files have unchanged content hashes. Raw/research datasets were not rewritten; test guards prevented repository-data writes.

Outbound socket access, credential-file reads, subprocesses and non-temporary SQLite/write paths remain blocked in test runs. The small standalone asyncio probe imports no application code, loads no credentials and attempts no external connection. No broker/order API, service manager or production migration command was invoked. Diagnostic paper-trade records, where required by existing mocked tests, were confined to temporary databases. No synthetic production calendar was installed.

**Deployment status: NOT READY — evidence missing.** No Phase 3 regression was demonstrated. Track A still requires independently reviewable historical provider evidence. Track B requires the fixture/environment repairs described here and a fresh complete suite before any stronger readiness claim.


## Evidence-only continuation — 2026-10-02.4

This pass changes acquisition artifacts and adds evidence-package regression coverage only. It does not alter calendar behavior, previous suite expectations or the earlier full-suite failure classifications. The unrelated full suite was not rerun. A successful current Dhan master download does not establish historical calendar coverage; stored NIFTY expiry audit attributes labels to Upstox. All 28 provider calendars remain UNVERIFIED, production calendar installation remains false, AUTO/NEXT stays fail-closed. Exact focused-suite results and checksum status are recorded in calendar_evidence_acquisition/validation.json. No live broker API calls/orders, service restarts or production migrations occurred.


### Alternative-source pass validation results

- Focused evidence package: **22 passed** (16 preserved + 6 new).
- Existing calendar/historical coverage plus evidence: **645 passed** (623 + 22).
- Saved Phase 2 diagnostics: **4 passed**; **649 distinct passing cases** including diagnostics.
- Failures/errors/skips/warnings: **0**. Warnings were treated as errors.
- No unrelated full-suite rerun. No pre-existing application/test content changed.
- New coverage: actual source projection, same-symbol exchange separation, third-provider expiry derivation, hash/URL provenance, transport failure classification and no historical promotion from current observations.
- `git diff --check`: passed. SHA256 ledger verified; previous source bytes preserved.
- Deployment: **NOT READY — evidence missing**. No live broker API calls/orders, production migration/restart or calendar behavior change.


## Static archive tooling — 2026-10-02.5

New code is restricted to calendar_evidence_acquisition/static_archives.py and its dedicated tests. No existing application/test behavior changed. Initial test-first run had the expected module-not-found collection error before implementation. The first real secondary-data parse rejected undocumented numeric dates; the source was preserved, strict default retained, and explicit quarantine added with regression coverage. No epoch was guessed. Final targeted and relevant-suite results are recorded in validation.json; earlier unrelated full-suite failure classifications are unchanged.


### Static archive continuation validation — 2026-10-02.5

- Focused new archive/cache suite: **24 passed**.
- Full relevant calendar/evidence suite: **669 passed** (previous 645 + 24 new).
- Saved Phase 2 diagnostics: **4 passed**; **673 distinct passing cases** total.
- Failures/errors/skips/warnings: **0**; warnings treated as errors.
- `git diff --check`: passed. SHA256 verification covers raw sources, cache entries, parsed inventories, code, tests and reports.
- Prior application and test file hashes unchanged. Earlier raw evidence preserved. Unrelated full suite not run.
- Official archive files acquired: **0**. Secondary datasets acquired: **2**. New network requests: **7** (2 transport failures, 3 discovery responses, 2 dataset bundles). Prior failed attempts imported: **8**. Existing local source inventories reused: **3**. Broker/API token calls and duplicate requests: **0**.
- Calendar statuses: **0 VERIFIED, 0 PARTIALLY_VERIFIED, 28 UNVERIFIED, 0 CONFLICTING, 0 NOT_APPLICABLE**. Candidate row coverage is not independently verified calendar coverage.
- Production calendar installed: **NO**. AUTO/NEXT unchanged and fail-closed. No orders, production migration or service restart.


## Official EOD validation — 2026-10-02.6

- New EOD tests: **22**. Combined focused EOD/static-archive tests: **46 passed**.
- Complete relevant calendar/evidence suite: **691 passed**. Saved Phase 2 diagnostics: **4 passed**. **695 distinct passing cases**.
- Final failures/errors/skips/warnings: **0**. Warnings treated as errors; no tests weakened or skipped.
- Test-first evidence: module-not-found before implementation; two corrupt ZIP/GZIP tests reproduced a new-pipeline exception-handling gap, then passed after retaining unavailable parse results.
- `git diff --check`: passed. SHA256 ledger verified after generation. Prior raw artifacts and pre-existing application/tests unchanged.
- No unrelated full-suite run, broker calls/orders, production migration/restart or production calendar activation.
- **Production calendar NOT INSTALLED. AUTO/NEXT FAIL-CLOSED.**


## Browser/manual ingestion framework — 2026-10-02.7

Implemented `calendar_evidence_acquisition/evidence_engine.py` with an optional isolated Playwright route, manual staging CLI, filename/metadata checks, in-memory archive parsing, pandas filtering, content-addressed original archives and a locked/atomic provenance ledger. Underlying and instrument groups remain separate within a file-level expiry union. The existing official EOD parser now exposes an UNVERIFIED staged-byte entry point; no fake discovery HTML is manufactured for manual files.

Ledger ingestion does not automatically mark sources/calendars VERIFIED or activate production. SHA256 authenticates retained bytes' integrity, not their origin, completeness, publication time or provider listing coverage. Metadata cannot self-assign VERIFIED. Conflicting versions and raw/parse failures are preserved. Existing provider-calendar gates remain unchanged.

Playwright is not installed; only mocked browser behavior was tested. No packages, browser binaries or credentials were installed/read. No real browser, exchange or broker request occurred. The browser route uses normal sessions, exact observed links, bounded timeouts and manual fallback; it does not solve challenges or promise access through exchange WAFs. Manual staging is ready for genuine files with traceable source metadata; no workspace ledger or synthetic production calendar was created.

Validation: **20 new engine tests passed; 711 complete relevant tests passed; 4 Phase 2 diagnostics passed; 715 distinct cases**. Final failures/errors/skips/warnings: **0**. Tests run with network, subprocesses and credential-file reads blocked, and temporary-only writes/databases. Initial test-first missing-module and inventory-tamper failures were resolved without weakening tests. `git diff --check` passed; SHA256 ledger verified. All pre-existing application/test files and previous raw evidence preserved.

Usage and metadata instructions: `calendar_evidence_acquisition/EVIDENCE_ENGINE.md`. Optional Playwright dependency is isolated in `requirements-browser.txt`; existing pandas dependency remains unchanged. Runtime ledger status is UNVERIFIED/CONFLICTING until separate review. All 28 provider/calendar combinations remain UNVERIFIED. **Production calendar NOT INSTALLED; AUTO/NEXT FAIL-CLOSED.** Orders, migrations and service restarts: **0**.
