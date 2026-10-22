# Operational staging validation — 2026-10-02

**IMPLEMENTATION READY for manual evidence staging. HISTORICAL EVIDENCE NOT VERIFIED.**
Deployment remains **NOT READY — evidence missing**. Production Calendar is
**NOT INSTALLED** and unresolved historical AUTO/NEXT remains **FAIL-CLOSED**.

## What changed

- `evidence_engine.py`: root/backend CLI compatibility; `--staging`, `--ingest`,
  `--dry-run`, `--status`; per-file failures with continued batch processing;
  complete-provenance gate; raw retention and separate rejected-parse records.
- `staged_formats.py`: bounded in-memory ZIP/CSV/gzip/XLSX decoding; unambiguous
  labeled-column detection; literal trading-date validation; separate expiry,
  actual-expiry and contract-maturity fields. No extra dependency installed.
- `staging_status.py`: read-only 28-combination report, scoped candidate observations,
  hashes, provenance, missing required intervals and explicit review blockers.
  Staged publisher claims never become official qualifications or provider proofs.
- Optional Playwright route: per-origin robots exclusions, bounded requests,
  transparent user-agent and no retries, evasion, authentication or challenge solving.
  Browser control flow is mocked in tests; Playwright remains uninstalled.
- `tests/test_evidence_staging.py`: 48 new cases. Existing tests were preserved.

## Validation

| Check | Result |
|---|---:|
| Existing acquisition baseline | 66 passed |
| Initial test-first reproductions | 14 expected failures |
| Final focused acquisition tests | 114 passed |
| Complete relevant suite (including new tests) | 759 passed |
| Saved Phase-2 diagnostics | 4 passed |
| Distinct final passing tests | 763 |
| Final failures / errors / skips / warnings | 0 / 0 / 0 / 0 |
| Pre-existing application/test files hashed | 600; none changed |

Tests used the existing isolated runner: outbound sockets, subprocesses and
credential-file reads prohibited, SQLite and filesystem writes temporary-only,
warnings treated as errors. The unrelated full repository suite was not rerun.
JUnit reports, hashes and the live empty-ledger matrix are retained under
`operational_validation_20261002/` and indexed in `validation.json`.

Repository-root `--status` succeeds without creating an evidence root. Root
`--dry-run` currently returns `NO_STAGED_ARCHIVES` (exit 2), as neither staging
directory contains an archive. This is an explicit acquisition gap, not a test
failure. `SHA256SUMS` is refreshed after checks; its previous version is retained
as `SHA256SUMS_20261002_7_snapshot`. Original source bytes remain unchanged.

## Coverage and blockers

Official archives acquired: **0**. Staged archives acquired: **0**.
VERIFIED **0**; PARTIALLY_VERIFIED **0**; UNVERIFIED **28**;
CONFLICTING **0**; NOT_APPLICABLE **0**.

These owner-requested intervals remain entirely unverified for **both DHAN and
ANGELONE**. They are calendar-date scope, not a guessed list of trading sessions:

| Exchange | Instrument | Missing required interval, inclusive |
|---|---|---|
| NSE | NIFTY | 2026-07-01 – 2026-10-01 |
| NSE | BANKNIFTY | 2026-07-24 – 2026-10-01 |
| NSE | FINNIFTY, MIDCPNIFTY | 2026-09-04 – 2026-10-02 |
| BSE | SENSEX | 2026-09-03 – 2026-10-02 |
| BSE | BANKEX | 2026-09-04 – 2026-10-02 |
| MCX | NATURALGAS | 2026-07-13 – 2026-10-02 |
| MCX | CRUDEOIL | 2026-07-13 – 2026-10-01 |
| MCX | NATGASMINI, CRUDEOILMINI, GOLD, GOLDMINI, SILVER, SILVERMINI | Required historical scope still unestablished |

Every combination lacks qualifying official observations, independent source
review, complete listed-universe/publication/cutoff evidence, official trading
session evidence and separate historical provider-listing evidence. Exchange EOD
records cannot establish Dhan/AngelOne historical listing coverage by themselves.
An observed future expiry in a historical EOD row is preserved as an observation;
it is never installed as a resolved calendar or extrapolated to missing dates.

The report deliberately does not claim completeness from first/last dates, fill
weekdays, treat a parser success as partial verification, or infer MINI aliases
from product names. Unknown spreadsheet schemas require an explicit reviewed
mapping; unsupported workbook features are rejected. No actual official archive
has yet been available to validate exchange-specific workbook layouts end to end.

## Exact next action

Place an operator-downloaded original official file and its `.json` provenance
sidecar in repository-root `artifacts/staging/`, following `EVIDENCE_ENGINE.md`.
Run the root CLI with `--dry-run`, then `--ingest`, then `--status`. Missing official
source access must be resolved by lawful operator download, not a bypass or broker
token. Independently review retained bytes and assemble the existing provider-calendar
evidence package before any separate production activation review. All required
combinations must satisfy that policy; this acquisition tool cannot activate one.

Network requests **0**; live broker calls **0**; orders **0**; production migrations
**0**; production restarts **0**. No production calendar code, fallback policy,
legacy snapshot, existing research data or existing test was changed.
