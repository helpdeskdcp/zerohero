# NSE official layer validation — 2026-10-03

Implementation is opt-in. Production calendar is **NOT INSTALLED** and AUTO/NEXT
remain **FAIL-CLOSED**. No existing application, test, broker or calendar module
was edited for this task. Existing dirty work and the October 1 archives remain
unchanged. No service, scheduler, database schema or trading configuration changed.

## Implemented and verified

- Added `app/nse_official/{__init__,__main__,adapter,core,chain,provenance,store,quality,transport}.py`.
- Added `tests/test_nse_official.py` and `tests/test_nse_official_chain.py`: 74 cases.
- Added `NSE_OFFICIAL_DATA_CONTRACT.md` with operations, fields and limitations.
- Added `calendar_evidence_acquisition/parsed/nse_market_data_20261003.json`,
  referencing the unchanged earlier inventory plus four new observed sources.
- Added five raw public sources, five sidecars and checksums under
  `../artifacts/staging/nse-market-data-20261003/`. The fifth raw source is the
  refreshed robots policy, whose bytes match the prior policy.

The retained historical HTTP 200 response yields 70 validated NIFTY OPTIDX CE
records for 2026-10-01, with explicit NSE expiry 2026-10-06. Temporary-store
ingestion returned `INGESTED`, then `ALREADY_INGESTED` on repetition, with dataset
key `2198cbe888fefe5e64de7845b5d97c8fb8dfe9a6abb621638d048c668e7affed`.

Historical raw SHA256:
`95b34980827dc7d509580156c80ec58dce61dc879cc466581046b2c27da62add`.

The public option-chain page, published script, current selector and requested
chain all returned HTTP 200. Robots also returned HTTP 200. These five GETs used
certificate-verified HTTP/1.1, an identifying evidence-agent header, no redirects,
cookies, broker credentials, proxies or browser automation. Initial sandbox
transport failures were followed by explicitly approved external-network calls.
No NSE denial was bypassed. Current selector: 18 observed expiry dates.

The actual chain has 116 strike rows, nine placeholder legs lacking identity,
and timestamp `01-Oct-2026 15:40:00 IST`, independently supported by the official
rendering script. Its adapter result is:

```json
{
  "status": "DATA_UNAVAILABLE",
  "reason": "INCOMPLETE_CHAIN_CONTRACT",
  "source_timestamp": "2026-10-01T10:10:00+00:00",
  "fetch_timestamp": "2026-10-03T04:06:30.898588+00:00",
  "data_age_seconds": 150990.898588,
  "freshness_status": "STALE",
  "row_count": 0,
  "sha256": "b7296785773fcd0c253e2bf7207ac26ac84b1ccd44cddc57f96ce8e0f67013e2"
}
```

This proves acquisition and rejection of invalid/stale data, not a working live
feed. Positive chain normalization is covered by explicitly mocked, complete
fixtures. No normalized subset of the incomplete actual chain was published.

## Tests and checks

Final relevant run: **941 passed, 0 failed, 0 errors, 0 skipped, 0 warnings**
(`-W error`). Includes 74 new cases and all four saved Phase 2 diagnostics.
XML: `/tmp/nse-official-final-green/result.xml`.

Complete backend attempt: 218 test files were invoked separately through the
existing isolated runner, with a 60-second test-call deadline and 120-second file
deadline. Completed results: **2,487 passed, 32 failed, 0 errors, 8 skipped**;
**7 file timeouts** leave some cases unverified. No tests were intentionally
excluded. The eight existing skips report missing captured research sessions.
This is not a green full suite.

This full run started before the final new-module additions: it included 56 new
NSE cases. The final relevant run above supersedes that module's validation and
also covers the subsequently added chain tests. Counts across the two runs must
not be added together. Two distinct `test_scoring.py` paths initially shared a
report filename; both were rerun unchanged into distinct report directories
(6 and 5 passes). The full counts above include both corrected reports.

Full-run reports: `/tmp/nse-official-full/`; corrected scoring reports:
`/tmp/nse-official-full-distinct/`. Safety guards block network, credential-file
reads, non-temporary database access, subprocesses and non-temporary writes.

| File | Failures | Observed result |
| --- | ---: | --- |
| `test_ai_shadow.py` | 1 | Expected AI call absent; IndexError |
| `test_hedging_runner.py` | 10 | Nine prohibited non-temporary writes; one missing `armed` field |
| `test_order_pressure.py` | 1 | Non-temporary database access blocked |
| `test_phase_h_five_symbol_pipeline.py` | 6 | Missing mocked AI calls/decision |
| `test_reverse_engineering_status_report.py` | 1 | Non-temporary database access blocked |
| `test_shadow_notify.py` | 10 | Missing decisions/notifications |
| `test_stale_feed_watchdog.py` | 1 | Subprocess blocked by isolation runner |
| `test_standalone_angelone.py` | 2 | Contract/expiry fixture expectations disagree with current result |

File timeouts: `test_autoscalp.py`, `test_autoscalp_final_signal_gate.py`,
`test_dhan_connector.py`, `test_dhan_postback.py`, `test_main_auth.py`,
`test_sdk_candle_metadata.py`, `test_smart_scalper_scheduler.py`.
No unrelated failure was fixed or assertion weakened. These files do not import
the new NSE module. Several failure families match the prior validation report;
this task does not claim a new causal investigation of every unrelated failure.

Mypy passed for all nine new modules using `--follow-imports=skip
--ignore-missing-imports --check-untyped-defs`; this is not strict typing coverage
of the whole repository. Syntax checks passed for 11 new Python files without
writing bytecode. `git diff --check` passed.

All 224 checksum entries passed: 192 existing acquisition entries, 21 earlier
staging entries and 11 new evidence/inventory entries. October 1 child archive
remains `d44a052b79a3961910f74783960ebbd5043696dbed39e7fb86ec84929fd60338`;
parent archive remains
`2c5aae28b3d6682ee8dc2c7162041acfbf5c6999233d653a2efd157450f40fb0`.

## Remaining boundaries

The observed contract master is a 70-contract historical subset, not a complete
exchange security master. No independent historical listing/publication/cutoff
coverage was established. The existing 28 provider/calendar combinations are not
upgraded. The exact Bhavcopy download URL is still not independently observed by
this workflow. No production calendar installation or AUTO/NEXT activation is
justified. Existing consumers were not switched from broker feeds to this new
opt-in layer. Broker discrepancy/fallback behavior is unit-tested, not live-tested.

Next: obtain complete/fresh official chain and independently reviewable contract
master/calendar coverage, then explicitly integrate compatible consumers. Resolve
full-suite isolation/fixture failures separately. No broker calls/orders,
production migrations, production ingestion or service restarts occurred.
