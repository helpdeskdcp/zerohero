# MCX disabled — production calendar scope decision

**MCX DISABLED — OUT_OF_SCOPE — NOT PART OF PRODUCTION CALENDAR**

MCX is disabled at exchange level, including NATURALGAS, NATGASMINI, CRUDEOIL, CRUDEOILM/CRUDEOILMINI, GOLD, GOLDMINI, SILVER, SILVERMINI and registered aliases. No future MCX calendar evidence can override this scope guard. No instrument is waiting for evidence.

## Enforced boundaries

- `calendar_scope.py` is the shared product-scope policy. `production_scope.json` records the current disabled release state; the prior Natural Gas-only decision was copied byte-for-byte to a historical snapshot.
- Calendar review and provider/official resolution reject MCX before consulting supplied evidence, including explicit-date calendar requests. AUTO/NEXT cannot resolve or activate a calendar.
- MCX option-expiry reconstruction and calendar normalization from broker expiry lists return unavailable/disabled. No dates are generated. Broker integrations and configuration are unchanged.
- New cache acquisition, official HTTP fetch, EOD acquisition, Playwright acquisition and staging ingestion reject MCX before network or publication. An excluded MCX staged file is reported DISABLED without failing an active-exchange batch.
- Coverage reports show 16 disabled MCX provider/instrument rows with no blockers or required ranges. Retained MCX ledger entries are excluded from active coverage evaluation, so missing/corrupt legacy MCX files cannot block NSE/BSE readiness. This is not an integrity approval of those files.
- `production_gate(exchange="MCX")` returns NOT_ENABLED, AUTO/NEXT DISABLED, no missing gates and no pending requirements. It is not eligible for review or installation. NSE/BSE retain the existing eight evidence gates and manual activation restriction.
- Offline source/row readers remain available for historical forensic inspection. They cannot grant active calendar eligibility. All MCX PDFs, source responses, parsed schedules and hashes are retained; none is promoted to production validation.

## Validation and contract changes

Final complete relevant suite: **1,211 passed, 0 failed, 0 errors, 0 skipped, 0 warnings** (warnings treated as errors), including four Phase 2 diagnostics. New disabled-scope file: **46 tests passed**. Mypy passed for 11 files; changed Python files compile. Read-back of the actual artifacts coverage, scope configuration, MCX gate and all MCX AUTO/NEXT previews passed. No production calendar was installed.

The initial new tests reproduced 41 scope violations with one NSE/BSE control passing. Existing MCX-positive assertions were changed to explicit disabled expectations because of the owner decision; parameterized cases were retained, not skipped or deleted. NSE/BSE positive assertions remain. Sixteen captured-chain cases and one status assertion initially still expected the old scope and were corrected. A separate idempotency fixture crossed a wall-clock second: its capture timestamp is now frozen while all original assertions remain. Production capture behavior was not changed.

Checksum review: 381 existing entries passed, including all original MCX PDFs and October 1 archives. Review manifests were refreshed only for intentionally changed code/tests/current scope; `mcx_disabled_checksum_changes.json` retains old and new hashes. Raw source hashes were not changed. The new release checksum manifest covers every changed file except itself. `git diff --check` passed.

## Read-back

```json
{
  "PRODUCTION": "NOT_ENABLED",
  "AUTO/NEXT": "DISABLED",
  "LIVE_ORDERS": "DISABLED",
  "eligible_for_review": false,
  "missing_gates": [],
  "pending_requirements": [],
  "gate_required": false,
  "scope": "OUT_OF_SCOPE",
  "status": "DISABLED",
  "reason": "CALENDAR_EXCHANGE_DISABLED",
  "calendar_installed": false
}
```

No active MCX calendars, enabled AUTO/NEXT calendars, pending MCX production requirements or MCX generation remain. Scope exclusion is not successful evidence validation, and does not declare NSE/BSE production-ready. No acquisition, broker call, order, migration, service restart or scheduler activation occurred.

## Exact files changed in this continuation

- `backend/app/hist_options/calendar_scope.py`
- `backend/app/hist_options/provider_calendar.py`
- `backend/app/hist_options/expiry.py`
- `backend/app/nse_official/calendar.py`
- `backend/app/nse_official/own_exchange.py`
- `backend/app/nse_official/controls.py`
- `backend/calendar_evidence_acquisition/evidence_engine.py`
- `backend/calendar_evidence_acquisition/static_archives.py`
- `backend/calendar_evidence_acquisition/official_eod.py`
- `backend/calendar_evidence_acquisition/staging_status.py`
- `backend/calendar_evidence_acquisition/production_scope.json`
- `backend/calendar_evidence_acquisition/parsed/mcx_naturalgas_scope_before_disable_20261005.json`
- `backend/tests/test_mcx_disabled_scope.py`
- `backend/tests/test_provider_calendar.py`
- `backend/tests/test_official_expiry_calendar.py`
- `backend/tests/test_own_exchange_calendar.py`
- `backend/tests/test_hist_expiry.py`
- `backend/tests/test_evidence_engine.py`
- `backend/tests/test_evidence_staging.py`
- `backend/tests/test_official_eod_evidence.py`
- `backend/tests/test_static_calendar_archives.py`
- `backend/tests/test_mcx_naturalgas_production_scope.py`
- `backend/tests/test_historical_read_contract.py`
- `backend/tests/test_histcap.py`
- `backend/calendar_evidence_acquisition/SHA256SUMS`
- `backend/validation/MCX_NATURALGAS_SCOPE_SHA256SUMS`
- `backend/validation/NSE_COMPLETION_SHA256SUMS`
- `backend/validation/OFFICIAL_CALENDAR_SHA256SUMS`
- `backend/validation/OWN_EXCHANGE_CALENDAR_SHA256SUMS`
- `backend/validation/mcx_disabled_checksum_changes.json`
- `backend/MCX_DISABLED_REPORT.md`
- `backend/validation/mcx_disabled_validation.json`
- `backend/validation/MCX_DISABLED_SHA256SUMS`
