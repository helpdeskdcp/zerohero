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

# CALENDAR EXCHANGE EVIDENCE REPORT

Package 2026-10-02.6. **Production calendar: NOT INSTALLED. AUTO/NEXT: FAIL-CLOSED.**

Official EOD/contract datasets acquired: **0**. Qualifying source counts below exclude failed discovery responses, site code and secondary datasets. No observation has been authenticated by a successful official report download. All 28 combinations remain UNVERIFIED. Empty observed-expiry and hash fields mean no qualifying evidence, not that the exchange has no contracts.

## Endpoint discovery

- NSE: official reports landing candidate `/all-reports-derivatives` failed with HTTP/2 INTERNAL_ERROR, without an HTTP response. No current downloadable file link validated.
- BSE: retained official site JavaScript/config identify `GetBhavcopydata/w`, parameters `flag=1&segment=deri&date=YYYYMMDD`, and the derivative download directory. A single listing request for 2026-09-03 returned 403. No filename was guessed and no download pattern was enabled.
- MCX: bhavcopy landing candidate `/market-data/bhavcopy` returned 403. No current downloadable file link validated.

Three new discovery GETs, no duplicate/retry requests, no backfill fan-out. Each failure and available response bytes are retained. See `calendar_evidence_acquisition/parsed/official_endpoint_discovery.json` for exact URLs, timestamp, SHA256 and BSE cached-source excerpts. These are discovery evidence, not EOD expiry evidence.

## Per-combination evidence matrix

For every row: **official observed expiry dates = []**, **covered trading-date range = none**, **source count = 0**, **official-source count = 0**, **qualifying provenance = []**, **qualifying SHA256 evidence = []**. Provider listing status also remains UNVERIFIED. The machine-readable report includes all these fields individually for every combination.

| Provider | Exchange | Instrument | Status | Missing requested range | Remaining blocker |
|---|---|---|---|---|---|
| DHAN | NSE | NIFTY | UNVERIFIED | 2026-07-01..2026-10-01 | Landing transport failure; no validated official data |
| ANGELONE | NSE | NIFTY | UNVERIFIED | 2026-07-01..2026-10-01 | Landing transport failure; no validated official data |
| DHAN | NSE | BANKNIFTY | UNVERIFIED | 2026-07-24..2026-10-01 | Landing transport failure; no validated official data |
| ANGELONE | NSE | BANKNIFTY | UNVERIFIED | 2026-07-24..2026-10-01 | Landing transport failure; no validated official data |
| DHAN | NSE | FINNIFTY | UNVERIFIED | 2026-09-04..2026-10-02 | Landing transport failure; no validated official data |
| ANGELONE | NSE | FINNIFTY | UNVERIFIED | 2026-09-04..2026-10-02 | Landing transport failure; no validated official data |
| DHAN | NSE | MIDCPNIFTY | UNVERIFIED | 2026-09-04..2026-10-02 | Landing transport failure; no validated official data |
| ANGELONE | NSE | MIDCPNIFTY | UNVERIFIED | 2026-09-04..2026-10-02 | Landing transport failure; no validated official data |
| DHAN | BSE | SENSEX | UNVERIFIED | 2026-09-03..2026-10-02 | Listing HTTP 403; no validated official data |
| ANGELONE | BSE | SENSEX | UNVERIFIED | 2026-09-03..2026-10-02 | Listing HTTP 403; no validated official data |
| DHAN | BSE | BANKEX | UNVERIFIED | 2026-09-04..2026-10-02 | Listing HTTP 403; no validated official data |
| ANGELONE | BSE | BANKEX | UNVERIFIED | 2026-09-04..2026-10-02 | Listing HTTP 403; no validated official data |
| DHAN | MCX | NATURALGAS | UNVERIFIED | 2026-07-13..2026-10-02 | Landing HTTP 403; no validated official data |
| ANGELONE | MCX | NATURALGAS | UNVERIFIED | 2026-07-13..2026-10-02 | Landing HTTP 403; no validated official data |
| DHAN | MCX | NATGASMINI | UNVERIFIED | Not established | Landing HTTP 403; no validated official data; define historical window/type |
| ANGELONE | MCX | NATGASMINI | UNVERIFIED | Not established | Landing HTTP 403; no validated official data; define historical window/type |
| DHAN | MCX | CRUDEOIL | UNVERIFIED | 2026-07-13..2026-10-01 | Landing HTTP 403; no validated official data |
| ANGELONE | MCX | CRUDEOIL | UNVERIFIED | 2026-07-13..2026-10-01 | Landing HTTP 403; no validated official data |
| DHAN | MCX | CRUDEOILMINI | UNVERIFIED | Not established | Landing HTTP 403; no validated official data; define historical window/type |
| ANGELONE | MCX | CRUDEOILMINI | UNVERIFIED | Not established | Landing HTTP 403; no validated official data; define historical window/type |
| DHAN | MCX | GOLD | UNVERIFIED | Not established | Landing HTTP 403; no validated official data; define historical window/type |
| ANGELONE | MCX | GOLD | UNVERIFIED | Not established | Landing HTTP 403; no validated official data; define historical window/type |
| DHAN | MCX | GOLDMINI | UNVERIFIED | Not established | Landing HTTP 403; no validated official data; define historical window/type |
| ANGELONE | MCX | GOLDMINI | UNVERIFIED | Not established | Landing HTTP 403; no validated official data; define historical window/type |
| DHAN | MCX | SILVER | UNVERIFIED | Not established | Landing HTTP 403; no validated official data; define historical window/type |
| ANGELONE | MCX | SILVER | UNVERIFIED | Not established | Landing HTTP 403; no validated official data; define historical window/type |
| DHAN | MCX | SILVERMINI | UNVERIFIED | Not established | Landing HTTP 403; no validated official data; define historical window/type |
| ANGELONE | MCX | SILVERMINI | UNVERIFIED | Not established | Landing HTTP 403; no validated official data; define historical window/type |

Missing ranges are requested calendar-date intervals, not an assertion that every date was a trading session. Missing trading-date lists remain undetermined until official holiday/special-session evidence is available.

NIFTY has one retained secondary dataset within its requested interval: a repository fixture dated 2026-08-13 with 18 expiry candidates. It is not counted above, is not official evidence, and establishes neither Dhan nor AngelOne listings. Its exact candidate dates, source/container hashes and limitations are retained separately per NIFTY combination in `parsed/calendar_exchange_evidence_report.json`. All other combinations have zero in-scope retained secondary EOD datasets in that report.

## Pipeline behavior

The evidence-only pipeline in `calendar_evidence_acquisition/official_eod.py` reuses SourceCache. It accepts exact file links extracted from successful, hashed official HTML/JSON responses. It does not accept guessed filename templates or bare filenames. Official-host GET transport has no authentication, redirects, retries or broker endpoints. Discovery and raw file bytes remain immutable; failed downloads and parse failures remain traceable.

Every successful inventory preserves exchange, segment, report trading date, source URL/endpoint/report name, acquisition timestamp, HTTP status, original filename, size, SHA256, parser/field-map version, raw fields, underlying, instrument type, expiry and source contract/product identifier. Where an identifier is absent in an older layout, it remains null; no token is manufactured.

BSE expiry and contract maturity are separate mapped columns. NSE UDiFF `XpryDt` and `FininstrmActlXpryDt` are also preserved separately. MCX expiry comes only from the explicitly mapped Expiry Date field. Missing expiry is rejected, never replaced with maturity. NSE EOD format selection enforces the requested 2024-07-08 transition; this is a file-format boundary, not an expiry rule.

BSE/MCX schema examples in tests are synthetic format fixtures. No production field map for an unseen official file is installed. Unsupported CSV layouts and non-CSV report formats remain unavailable until inspected. A published EOD row is not automatically proof of a trade or of intraday publication time.

Duplicate rows are preserved and identified. Same-day disagreements for the same explicit exchange/segment/contract ID are CONFLICTING, with both source hashes/claims retained. Different expiry subsets are not automatically conflicts. No routine promotes an incomplete interval to VERIFIED; independent review and completeness/publication/cutoff evidence remain necessary. No provider listing is inferred from exchange evidence.

Next action: supply a successful official report response with exact download links (or legally retained exchange-origin files and provenance), inspect the actual schema, then use the existing gated pipeline. Do not repeat the blocked discovery endpoints or guess dated URLs.


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
