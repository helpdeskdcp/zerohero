# NSE completion and backend repair — 2026-10-03

**Implementation validated; production BLOCKED.** No production ingestion, calendar installation, AUTO/NEXT activation, broker calls/orders, service restart or scheduler activation.

## Original 32 failures

Machine-readable detail: `validation/nse_completion_failures.json`. Classes: A NSE regression; B existing backend regression; C fixture/data mismatch; D environment/dependency; E outdated expectation; F unresolved defect. **A=0, B=0, C=31, D=1, E=0, F=0** for the original saved failures. Each saved failure was inspected; no assertion was removed/weakened, no xfail/skip added.

| Test | Class | Root cause | Fix/status |
| --- | --- | --- | --- |
| `tests/test_phase_h_five_symbol_pipeline.py::test_full_pipeline_with_mocked_ai_success_for_every_symbol[NIFTY]` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_phase_h_five_symbol_pipeline.py::test_full_pipeline_with_mocked_ai_success_for_every_symbol[BANKNIFTY]` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_phase_h_five_symbol_pipeline.py::test_full_pipeline_with_mocked_ai_success_for_every_symbol[SENSEX]` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_phase_h_five_symbol_pipeline.py::test_full_pipeline_with_mocked_ai_success_for_every_symbol[NATURALGAS]` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_phase_h_five_symbol_pipeline.py::test_full_pipeline_with_mocked_ai_success_for_every_symbol[CRUDEOIL]` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_phase_h_five_symbol_pipeline.py::test_ai_failure_never_blocks_the_deterministic_pipeline` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_reverse_engineering_status_report.py::test_status_report_missing_market_db_does_not_raise` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_stale_feed_watchdog.py::test_selftest_entrypoint_passes` | D | Subprocess escapes inherited Python audit guards and may read production market_history.db; runner correctly prohibits it. | Exercise same main(--self-test) in guarded process against deterministic temporary DB with real scan checks. Targeted and full-suite PASS. |
| `tests/test_shadow_notify.py::test_pass_sends_telegram_for_both_sides[BUY_CE-CE-expected_states0]` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_shadow_notify.py::test_pass_sends_telegram_for_both_sides[BUY_PE-PE-expected_states1]` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_shadow_notify.py::test_fail_suppresses_to_no_trade_and_does_not_send` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_shadow_notify.py::test_uncertain_does_not_send_when_it_stays_weak` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_shadow_notify.py::test_high_risk_weakens_and_only_sends_if_still_strong` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_shadow_notify.py::test_high_risk_weak_state_sent_when_include_weak_enabled` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_shadow_notify.py::test_duplicate_signal_sent_only_once` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_shadow_notify.py::test_telegram_failure_does_not_block_shadow_decision` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_shadow_notify.py::test_telegram_send_fn_raising_never_propagates` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_shadow_notify.py::test_no_duplicate_across_a_simulated_restart` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_ai_shadow.py::test_ai_called_and_persisted_when_required_and_configured` | C | Fixture isolates primary DB but not app.orderflow.depth.L2_DB_PATH; guarded production L2 read aborts shadow/report call. | Use missing or synthetic temporary L2 DB, retaining real reader behavior. Targeted and full-suite PASS. |
| `tests/test_hedging_runner.py::test_disarmed_scan_evaluates_no_new_entries` | C | Fixture leaves runner._LOCK_PATH at backend/data; scan fails or scan_and_record returns error instead of armed state. | Point scan lock at per-test tmp_path; preserve actual locking. Targeted and full-suite PASS. |
| `tests/test_hedging_runner.py::test_armed_scan_opens_a_position_and_notifies` | C | Fixture leaves runner._LOCK_PATH at backend/data; scan fails or scan_and_record returns error instead of armed state. | Point scan lock at per-test tmp_path; preserve actual locking. Targeted and full-suite PASS. |
| `tests/test_hedging_runner.py::test_portfolio_margin_ceiling_blocks_new_entry` | C | Fixture leaves runner._LOCK_PATH at backend/data; scan fails or scan_and_record returns error instead of armed state. | Point scan lock at per-test tmp_path; preserve actual locking. Targeted and full-suite PASS. |
| `tests/test_hedging_runner.py::test_armed_scan_respects_max_positions_per_symbol` | C | Fixture leaves runner._LOCK_PATH at backend/data; scan fails or scan_and_record returns error instead of armed state. | Point scan lock at per-test tmp_path; preserve actual locking. Targeted and full-suite PASS. |
| `tests/test_hedging_runner.py::test_no_verified_lot_size_is_no_trade_not_fabricated` | C | Fixture leaves runner._LOCK_PATH at backend/data; scan fails or scan_and_record returns error instead of armed state. | Point scan lock at per-test tmp_path; preserve actual locking. Targeted and full-suite PASS. |
| `tests/test_hedging_runner.py::test_bad_symbol_evaluation_is_error_not_crash` | C | Fixture leaves runner._LOCK_PATH at backend/data; scan fails or scan_and_record returns error instead of armed state. | Point scan lock at per-test tmp_path; preserve actual locking. Targeted and full-suite PASS. |
| `tests/test_hedging_runner.py::test_scan_and_record_persists_result_for_polling` | C | Fixture leaves runner._LOCK_PATH at backend/data; scan fails or scan_and_record returns error instead of armed state. | Point scan lock at per-test tmp_path; preserve actual locking. Targeted and full-suite PASS. |
| `tests/test_hedging_runner.py::test_entry_gate_blocks_new_positions_outside_session` | C | Fixture leaves runner._LOCK_PATH at backend/data; scan fails or scan_and_record returns error instead of armed state. | Point scan lock at per-test tmp_path; preserve actual locking. Targeted and full-suite PASS. |
| `tests/test_hedging_runner.py::test_kill_switch_daily_loss_limit_disarms_and_notifies` | C | Fixture leaves runner._LOCK_PATH at backend/data; scan fails or scan_and_record returns error instead of armed state. | Point scan lock at per-test tmp_path; preserve actual locking. Targeted and full-suite PASS. |
| `tests/test_hedging_runner.py::test_kill_switch_consecutive_losses_disarms` | C | Fixture leaves runner._LOCK_PATH at backend/data; scan fails or scan_and_record returns error instead of armed state. | Point scan lock at per-test tmp_path; preserve actual locking. Targeted and full-suite PASS. |
| `tests/test_order_pressure.py::test_coverage_report_is_honest` | C | Coverage test reads workstation market_history.db and research CSVs rather than an explicit fixture. | Use temporary database/CSV/cache paths for the missing-data coverage contract. Targeted and full-suite PASS. |
| `tests/test_standalone_angelone.py::test_resolver_handles_mcx_options_on_futures` | C | September 2026 expiry fixtures evaluated against current October clock; AUTO correctly excludes expired contracts. | Freeze resolver clock before fixture expiries; preserve expiry/chronology assertions. Targeted and full-suite PASS. |
| `tests/test_standalone_angelone.py::test_auto_expiry_is_chronological_not_lexical` | C | September 2026 expiry fixtures evaluated against current October clock; AUTO correctly excludes expired contracts. | Freeze resolver clock before fixture expiries; preserve expiry/chronology assertions. Targeted and full-suite PASS. |

Three additional postback failures surfaced after removing sandbox IPC timeouts. Their classification is C; details are in `validation/nse_completion_additional_failures.json`. All 19 postback tests now pass. Fixtures construct real disabled/unstarted data workers and a temporary tick DB. No broker or trading implementation changed.

Fixture repairs isolate L2 reads and scan lock files, give the coverage-report test explicit absent temporary inputs, freeze the AngelOne September contract-fixture clock, and exercise the actual watchdog CLI self-test in-process against a synthetic temporary DB. The watchdog now asserts no internal DB check was skipped. Existing endpoint/business assertions remain intact.

## Contract master and expiry preview

`validation/nse_contract_expiry_preview.json` contains the normalized records and exact provenance. The layer is implemented; complete exchange-wide master coverage is **PARTIAL**. NSE historical responses establish these exact contracts on **2026-10-01**, with both CE and PE represented:

| Symbol | Observed contracts | Explicit expiry | Official lot size | Current selector entries |
| --- | ---: | --- | ---: | ---: |
| NIFTY | 140 | 2026-10-06 | 65 | 18 |
| BANKNIFTY | 140 | 2026-10-27 | 30 | 6 |
| FINNIFTY | 140 | 2026-10-27 | 60 | 3 |
| MIDCPNIFTY | 140 | 2026-10-27 | 120 | 3 |

The 560 rows are an observed response subset, not a reconstructed Cartesian product or complete exchange master. Historical responses do not publish tokens, official contract identifiers or quote-level source timestamps: these remain null with `NOT_PUBLISHED_IN_RESPONSE`. Trade dates are retained separately and never promoted to quote timestamps. No missing contract is fabricated.

SENSEX requires official BSE evidence. MCX instruments require official MCX evidence. Neither is mapped to NSE merely to increase coverage. Existing 28 provider/calendar statuses are not upgraded by exchange observations.

The retained daily report metadata identifies **F&O-MII – Contract File (.gz) (NSE Exclusive contract)**, file key `NSE-FO-CONTRACT-CSV`, dated 01-Oct-2026. It supplies separate `filePath` and `fileActlName` fields. This workflow has not independently captured the complete download action/URL or retrieved its bytes; it does not concatenate these fields and pretend to have observed a download. A complete verified bulk master remains the exact acquisition blocker.

### Expiry preview — not installed

Below are literal dates from the four official current selectors. Source timestamp is **NULL / NOT_PUBLISHED_IN_RESPONSE** for every selector row; fetch/observation timestamps and hashes are in the JSON preview. Mapped means at least one separately validated historical contract has that expiry, not complete listing/cutoff/calendar validation.

| Symbol | Instrument | Expiry | Source | Source timestamp | Validation status |
| --- | --- | --- | --- | --- | --- |
| NIFTY | OPTIDX | 2026-10-06 | NSE_OFFICIAL | NULL | OBSERVED; mapping present; calendar UNVERIFIED |
| NIFTY | OPTIDX | 2026-10-13 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| NIFTY | OPTIDX | 2026-10-19 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| NIFTY | OPTIDX | 2026-10-27 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| NIFTY | OPTIDX | 2026-11-03 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| NIFTY | OPTIDX | 2026-11-23 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| NIFTY | OPTIDX | 2026-12-29 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| NIFTY | OPTIDX | 2027-03-30 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| NIFTY | OPTIDX | 2027-06-29 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| NIFTY | OPTIDX | 2027-09-28 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| NIFTY | OPTIDX | 2027-12-28 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| NIFTY | OPTIDX | 2028-06-27 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| NIFTY | OPTIDX | 2028-12-26 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| NIFTY | OPTIDX | 2029-06-26 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| NIFTY | OPTIDX | 2029-12-24 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| NIFTY | OPTIDX | 2030-06-25 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| NIFTY | OPTIDX | 2030-12-31 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| NIFTY | OPTIDX | 2031-06-24 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| BANKNIFTY | OPTIDX | 2026-10-27 | NSE_OFFICIAL | NULL | OBSERVED; mapping present; calendar UNVERIFIED |
| BANKNIFTY | OPTIDX | 2026-11-23 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| BANKNIFTY | OPTIDX | 2026-12-29 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| BANKNIFTY | OPTIDX | 2027-03-30 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| BANKNIFTY | OPTIDX | 2027-06-29 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| BANKNIFTY | OPTIDX | 2027-09-28 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| FINNIFTY | OPTIDX | 2026-10-27 | NSE_OFFICIAL | NULL | OBSERVED; mapping present; calendar UNVERIFIED |
| FINNIFTY | OPTIDX | 2026-11-23 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| FINNIFTY | OPTIDX | 2026-12-29 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| MIDCPNIFTY | OPTIDX | 2026-10-27 | NSE_OFFICIAL | NULL | OBSERVED; mapping present; calendar UNVERIFIED |
| MIDCPNIFTY | OPTIDX | 2026-11-23 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |
| MIDCPNIFTY | OPTIDX | 2026-12-29 | NSE_OFFICIAL | NULL | OBSERVED; mapping not established; calendar UNVERIFIED |

Reproduce this preview offline (temporary normalization store, no production writes):

```sh
backend/venv/bin/python -B -m backend.app.nse_official.master \
  --repository /root/zerohero \
  --inventory backend/calendar_evidence_acquisition/parsed/nse_completion_20261003.json
```

## Acquisition and provenance

Ten new public NSE datasets plus one refreshed robots response were acquired using certificate-verified HTTP/1.1 GET, no redirects, no cookies/credentials and no broker calls. One initial network-sandbox transport failure was rerun with explicit network permission. No NSE denial was bypassed. Existing NIFTY selector and CE response were reused without download.

Raw bytes and sidecars: `artifacts/staging/nse-completion-20261003/`. Registry: `calendar_evidence_acquisition/parsed/nse_completion_20261003.json`. Exact endpoint, request parameters, observed input evidence, discovery-parent hashes, HTTP status, observed safe response headers, acquisition timestamp and SHA256 are retained. Dates came from NSE response fields; URL query syntax came from retained official JavaScript, never filename substitution.

The adapter now appends immutable validation decisions for success, rejected data and transport failure. Raw responses are captured before normalization. Rejected decisions preserve reason/hash and empty normalized output. No HTTP status/hash is invented for a transport failure. Only Content-Type/Length, Date, ETag and Last-Modified headers are retained; cookies/authorization are excluded.

## Live chain and freshness

The **retained** HTTP 200 NIFTY chain (not a newly acquired fresh feed) contains 116 strikes / 232 CE+PE legs, nine missing identities and 29 zero LTPs. Its source timestamp is **2026-10-01 15:40:00 IST**, captured 2026-10-03 04:06:30.898588 UTC: **150990.898588 seconds** old at receipt. It is `PLACEHOLDER` with separate `freshness_status=STALE`; all normalized rows remain rejected. No claim of working live feed.

New `validation_status` exposes VALID_LIVE, VALID_RECENT, STALE, PLACEHOLDER, DATA_UNAVAILABLE and SOURCE_METADATA_REQUIRED while preserving existing transport/status contracts. Bid/ask and quantities are normalized only when explicitly published. Missing fields remain null with reasons. Default live threshold is 2 seconds; stale threshold 60 seconds; both are configurable. Future, missing and naive source timestamps fail validation. Receipt age and current processing age are separate.

## Non-mutating NSE/broker comparison

`compare_market_sources` provides MATCH, MINOR_DISCREPANCY, MAJOR_DISCREPANCY, STALE_SOURCE and MAPPING_MISMATCH with per-field NSE_VALUE, BROKER_VALUE, DIFFERENCE, timestamps and AGE_DIFFERENCE. Absolute minor tolerances are explicit and configurable; default zero. Exact contract/time mapping and aligned quantity units are required. Missing OHLC or unknown quantity units are a comparison blocker, not zero. Framework validated with mocks; no live broker comparison was performed. Original comparison API remains compatible.

## Production gate

`production_gate` requires hash-bound, independently reviewed complete reports for full suite, master, calendar, historical ingestion, freshness, provenance, duplicates and discrepancy checks. Unreviewed pass claims do not qualify. Even a fully reviewed preview cannot activate production: there is no installer, and the CLI `--production` route returns BLOCKED. Missing master/calendar/live evidence remains a blocker despite passing unit tests.

**PRODUCTION=BLOCKED; Production Calendar=NOT INSTALLED; AUTO/NEXT=FAIL-CLOSED; LIVE ORDERS=DISABLED for this layer.** No existing live execution configuration was changed.

## Validation

**Final full backend: 2,704 passed, 0 failed, 0 errors, 8 existing skips, 0 timeouts, 0 warning summaries, all 220 files executed.** Counts, per-file XML hashes and exact skip reasons are recorded in `validation/nse_completion_validation.json`. All 32 original failures and the three additional fixture failures pass in this run. Targeted NSE: **94 passed**. Relevant calendar/NSE/Phase-2 suite: **961 passed, 0 failures/errors/skips/warnings** (includes four Phase-2 diagnostics). Mypy: 11 NSE modules pass with follow-imports=skip, ignore-missing-imports and check-untyped-defs. Syntax checks: 13 files. SHA256: **246 entries passed**. Both pre-existing October 1 ZIPs remain byte-for-byte unchanged.

Full backend uses the repository safety runner for **every test file**, in separate processes with unique report paths, 60-second call and 180-second file deadlines. Local TestClient IPC runs outside sandbox restrictions; external network, secret-file access, subprocesses and non-temporary DB/file mutation remain blocked inside tests. This is full per-file validation, not a claim about single-process cross-module ordering.

The eight pre-existing replay skips require real captured sessions for CRUDEOIL/NIFTY/NATURALGAS and associated Stage-7 events. No synthetic session was substituted to claim real-data validation. No skip was added. Their exact names/reasons are retained with final validation results.

Next: acquire the observed official bulk master through its genuine download action; validate full contract mapping, listing/lifecycle/cutoff evidence and fresh complete live snapshots; obtain the absent research capture fixtures; seek independent evidence review before any separately authorized production integration.

## Files changed in this continuation

- NSE modules: `controls.py`, `master.py`; additions to `adapter.py`, `chain.py`, `provenance.py`, `transport.py`, `store.py`, `__main__.py`.
- Tests: new `test_nse_completion.py` (20 cases); fixture-only repairs in `conftest.py`, `test_order_pressure.py`, `test_standalone_angelone.py`, `test_stale_feed_watchdog.py`, `test_dhan_postback.py`.
- Validation: `run_backend_suite.py`, failure classifications, normalized preview, final validation summary and this report.
- Evidence: new `nse_completion_20261003.json` registry and 11 raw/sidecar pairs plus hashes in the new staging directory. All previous raw evidence, existing reports and archives preserved.

## Exact observed NSE data endpoints used

Retained observations and ten newly acquired data responses; no generated archive URLs:

- `https://www.nseindia.com/api/daily-reports?key=FO` — SHA256 `f9c303b83e5a2f51afc6e52eaecde3d39386889f64887b2d715da09babd34be2`
- `https://www.nseindia.com/api/historicalOR/foCPV?from=01-10-2026&to=01-10-2026&instrumentType=OPTIDX&symbol=NIFTY&year=2026&expiryDate=06-OCT-2026&optionType=CE` — SHA256 `95b34980827dc7d509580156c80ec58dce61dc879cc466581046b2c27da62add`
- `https://www.nseindia.com/api/historicalOR/meta/foCPV/expireDts?instrument=OPTIDX&symbol=NIFTY&year=2026` — SHA256 `5cc7614b67693ba3e6ee7a326da7d1e037008aa824eaa8d1d838abdd55810d06`
- `https://www.nseindia.com/api/option-chain-contract-info?symbol=NIFTY` — SHA256 `1229bb445d1fee03acaf80c6c6e6fd564b525841cbeb1aa8ea4bb3b7094b5b6b`
- `https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol=NIFTY&expiry=06-Oct-2026` — SHA256 `b7296785773fcd0c253e2bf7207ac26ac84b1ccd44cddc57f96ce8e0f67013e2`
- `https://www.nseindia.com/api/historicalOR/foCPV?from=01-10-2026&to=01-10-2026&instrumentType=OPTIDX&symbol=NIFTY&year=2026&expiryDate=06-OCT-2026&optionType=PE` — SHA256 `3ce74bf8546ae0c64c38ede68170f19da48f0333396e571e030edebe6e1f273b`
- `https://www.nseindia.com/api/option-chain-contract-info?symbol=BANKNIFTY` — SHA256 `c13e7ce8dff87c05145a6f599cd75c9e0e6b1c32b2b269d9b49772aef0f3f194`
- `https://www.nseindia.com/api/historicalOR/foCPV?from=01-10-2026&to=01-10-2026&instrumentType=OPTIDX&symbol=BANKNIFTY&year=2026&expiryDate=27-OCT-2026&optionType=CE` — SHA256 `8d90efffc2ca71d1b6e74e6efa16ce27b77e48bffa3323d48268242fa025e78d`
- `https://www.nseindia.com/api/historicalOR/foCPV?from=01-10-2026&to=01-10-2026&instrumentType=OPTIDX&symbol=BANKNIFTY&year=2026&expiryDate=27-OCT-2026&optionType=PE` — SHA256 `40806ba22e0b0d35e4c43f53336a1b0f6eade4e734d4794b20f527f90f105e05`
- `https://www.nseindia.com/api/option-chain-contract-info?symbol=FINNIFTY` — SHA256 `7686e5b20fa21e2048784471f76cb52312a80f099fbe61923e657bc9f0421308`
- `https://www.nseindia.com/api/historicalOR/foCPV?from=01-10-2026&to=01-10-2026&instrumentType=OPTIDX&symbol=FINNIFTY&year=2026&expiryDate=27-OCT-2026&optionType=CE` — SHA256 `481f526f6ce2852ed25208464773aaf8eab5c0bbb238470e89e69cf5c342095b`
- `https://www.nseindia.com/api/historicalOR/foCPV?from=01-10-2026&to=01-10-2026&instrumentType=OPTIDX&symbol=FINNIFTY&year=2026&expiryDate=27-OCT-2026&optionType=PE` — SHA256 `859a66724a34a5cb0834c4d5e6963cf360ccaaa6afac207d10fe2152b5d5fdac`
- `https://www.nseindia.com/api/option-chain-contract-info?symbol=MIDCPNIFTY` — SHA256 `4167308be6075b845985b7a75adbd93006110e27317911982740989baa0987f9`
- `https://www.nseindia.com/api/historicalOR/foCPV?from=01-10-2026&to=01-10-2026&instrumentType=OPTIDX&symbol=MIDCPNIFTY&year=2026&expiryDate=27-OCT-2026&optionType=CE` — SHA256 `19a5b4a7abccd296cb84c0bc5d546e390dd4f90e266a9884f3ae6f84faf6bc13`
- `https://www.nseindia.com/api/historicalOR/foCPV?from=01-10-2026&to=01-10-2026&instrumentType=OPTIDX&symbol=MIDCPNIFTY&year=2026&expiryDate=27-OCT-2026&optionType=PE` — SHA256 `301e4f8ee8d5790aaab770532d643bcd3349a128154a703f454cbf8d063fa0d5`
