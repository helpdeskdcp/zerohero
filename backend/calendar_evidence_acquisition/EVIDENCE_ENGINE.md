# Browser and manual evidence ingestion

NSE autonomous discovery is now available as an additional isolated component:
[NSE downloader and scheduler guide](NSE_DOWNLOADER.md). Its dedicated virtualenv
and browser are installed; the application virtualenv and production services are
unchanged. The live NSE dry-run remained fail-closed with `NSE_CONNECTION_FAILED`.
The manual workflow and earlier validation history below remain applicable.

Run from the repository root using `backend/venv/bin/python`, or from `backend/`
using its existing virtual environment. This CLI writes local
evidence artifacts only. It does not modify application databases, provider
selection, trading logic, services, calendars or authentication.

## Manual staging (no network or Playwright required)

Repository-root commands:

```sh
mkdir -p artifacts/staging
backend/venv/bin/python -B -m backend.calendar_evidence_acquisition.evidence_engine --staging ./artifacts/staging --dry-run
backend/venv/bin/python -B -m backend.calendar_evidence_acquisition.evidence_engine --staging ./artifacts/staging --ingest
backend/venv/bin/python -B -m backend.calendar_evidence_acquisition.evidence_engine --status
```

`--dry-run` checks metadata, original-byte SHA256, schema, rows and existing ledger
integrity without creating directories, raw copies, inventories, locks or a ledger.
`--status` is also read-only. It prints all 28 provider/instrument combinations,
candidate and qualifying source counts separately, observed dates, hashes, URLs,
missing required intervals and blockers. It never treats operator assertions as
independent review. No files staged is `NO_STAGED_ARCHIVES` (exit 2).

With `--staging /path/staging`, artifacts default to `/path`; `--artifacts` can
explicitly choose another evidence root. The older subcommands remain supported:

```sh
mkdir -p artifacts/staging
venv/bin/python -B -m calendar_evidence_acquisition.evidence_engine --help
```

Place the original downloaded `.csv`, `.zip`, `.csv.gz` or `.xlsx` in `artifacts/staging/`.
Beside it place `<full-original-filename>.json` containing its source metadata.
Do not rename a secondary dataset to suggest an official origin. An official URL
in a sidecar is an operator claim, not proof that those bytes came from the URL.

For example, a file with the **filename format**
`BhavCopy_NSE_FO_0_0_0_20260903_F_0000.csv.zip` would need a sidecar named
`BhavCopy_NSE_FO_0_0_0_20260903_F_0000.csv.zip.json`:

```json
{
  "exchange": "NSE",
  "segment": "FO",
  "trading_date": "2026-09-03",
  "provenance_url": "<exact official download URL actually used>",
  "report_name": "<name shown by the official report page>"
}
```

This is a metadata example, not an assertion that an official report was acquired.
Optionally supply `sha256` from an independently retained checksum; a mismatch
rejects ingestion. Rows must match the declared date/exchange/segment. Filename
patterns validate consistency only. If omitted, exchange/date can be obtained from
a recognized filename; an unknown filename can obtain one full-year trading date
from an unambiguous date column. Unknown exchange requires explicit metadata.
BSE two-digit filenames are checked against full-year metadata or literal row dates;
the century is never guessed. Source URL, report name and segment remain required.

NSE parsing reuses the existing legacy/UDiFF transition at 2024-07-08. For BSE/MCX,
unambiguous labeled columns are detected (such as `Expiry Date`, `EXPDATE`,
`EXPIRY_DT` or `XpryDt`). Unsupported or ambiguous columns fail closed. Supply
`field_map` with the **actual inspected source column names** when necessary:

```json
{
  "version": "<reviewed mapping version>",
  "trading_date": "<trading-date column>",
  "underlying": "<underlying column>",
  "instrument": "<instrument-type column>",
  "expiry": "<expiry-date column>",
  "maturity": "<contract-maturity-date column, or null if absent>",
  "identifier": "<contract-ID column, or null if absent>"
}
```

Use JSON `null`, not the string `"null"`, for absent optional columns. Expiry is
mandatory; maturity never fills a missing expiry. Mapping examples are not installed
production schemas. Raw symbol spellings and original fields remain in inventories.

ZIP must contain exactly one CSV or XLSX table; nothing is extracted to disk.
XLSX uses a bounded standard-library reader, so no `openpyxl` installation is
required. It supports one worksheet, shared/inline strings and literal cells.
Date serials require a recognized date style and the workbook's 1900/1904 epoch.
Numeric CSV dates are not guessed. Formula cells, external links, macros, DTDs,
encrypted/ambiguous archives and multiple worksheets are rejected. Unsupported
layouts need a reviewed mapping or a separately downloaded official CSV, not edits
to the original evidence bytes. No claim is made that every exchange workbook
layout is supported before actual official files can be inspected.

Ingest all staged files, or pass selected paths:

```sh
venv/bin/python -B -m calendar_evidence_acquisition.evidence_engine ingest
venv/bin/python -B -m calendar_evidence_acquisition.evidence_engine --artifacts /path/to/artifacts ingest /path/to/artifacts/staging/file.csv
```

A per-file metadata/filename error returns exit code 2. Batch processing continues
with remaining files and reports each failure. Parse failures retain raw bytes
and a rejection record with `PARSER_REJECTED`, no fabricated expiry and UNVERIFIED
status; rejected or empty-target parses do not update the ledger. Successful
ingestion returns 0, which means **ledgered for review**, not calendar verification.
No staged files are removed or overwritten.

## Optional isolated browser route

Playwright is absent in the current environment. Installation is a separate operator
step; it was not performed during remediation:

```sh
venv/bin/pip install -r calendar_evidence_acquisition/requirements-browser.txt
venv/bin/python -m playwright install chromium
```

Then use a real official report page; URLs are not generated from date templates:

```sh
venv/bin/python -B -m calendar_evidence_acquisition.evidence_engine browser \
  --exchange NSE --date 2026-09-03 --report-page '<official report page>'
```

`--download-url` may select an exact link **already present on the page**, never an
invented URL. BSE/MCX may also need `--field-map path/to/mapping.json`.

The normal Chromium context identifies itself as `ZeroHero-public-evidence/1`.
It checks each required origin's public robots policy, then visits the exchange
root before the report page/download. Disallowed paths, unavailable policies and
crawl-delay/request-rate directives require manual acquisition. Policies are cached
per origin within the session; unreviewed subresource origins are blocked.
Session cookies remain in that isolated context and are not exported. Only same-
exchange allowlisted HTTPS GET/HEAD requests are allowed; no broker/authentication
endpoints or stored user profiles are used. Acquisition uses `expect_download`,
in-memory CSV/decompression and original archive bytes. Browser download staging
may temporarily hold the original archive; redundant unzipped files are not written.

The session has navigation, download-start and overall 90-second deadlines plus
bounded cleanup. It does not solve CAPTCHAs, hide automation or guarantee WAF access.
Missing dependency/browser, blocked pages, missing/ambiguous links, timeouts and
failed downloads produce `MANUAL_REQUIRED` (exit code 2). No automatic retry occurs;
attempt results are retained under `browser_attempts/`. Missing HTTP download status
remains null rather than a fabricated 200. Interactive date forms without a visible
matching link require manual download; the tool does not guess selectors/endpoints.

## Ledger contract and recovery

`artifacts/evidence_ledger.json` uses `exchange-evidence-ledger/v1` with an `entries`
array. Entries retain the requested fields: trading date, exchange, raw SHA256,
publisher claim, ingestion timestamp, extracted `DD-MMM-YYYY` expiries, source URL and
status. ISO dates, raw/inventory paths, inventory hash, route, parser reason and
provider-listing status are additional fields. The ingestion timestamp is not the
exchange's original publication time or the operator's earlier download time.

Original archives are content-addressed under `raw/`; inventory JSON retains raw
columns, expiry/maturity distinctions and source identity. Pandas filters the already
validated records; it does not guess dates or fill missing values. Identical input
with the same context is idempotent. Different archives are retained separately;
same-day explicit contract-ID conflicts retain both claims and append CONFLICTING.

Ledger updates use POSIX `flock`, file/directory fsync and atomic replacement. An
interrupted write leaves the previous ledger intact; orphaned retained artifacts
are harmless and are not deleted. Retrying ingestion checks existing hashes before
reuse. Incomplete browser attempts do not trigger another network call. An unknown
ledger schema, corrupt retained file or conflicting immutable output requires review.

## Verification boundary

**SHA256 proves integrity, not authenticity or calendar completeness.** Metadata
cannot self-assign VERIFIED. Manual and browser ingestion return UNVERIFIED (or
CONFLICTING), even when parsing and atomic ledger replacement succeed. Publisher
attribution is explicitly operator-asserted or browser-observed, not independently
reviewed. The ledger is not a production calendar schema and is not auto-loaded.

The existing `CalendarEvidence.validated()` gate additionally requires a reviewed
source/manifest pair, complete historical provider-listed expiry universe, correct
identity, publication/listing/cutoff times, effective intervals and review reference.
Exchange EOD data cannot by itself prove Dhan/AngelOne historical listings. No new
activation path bypasses that gate. Production calendar remains NOT INSTALLED and
AUTO/NEXT remains FAIL-CLOSED until its evidence policy is satisfied.


## Validation of this implementation

20 engine tests passed; complete relevant suite 711 passed; Phase 2 diagnostics 4 passed (715 distinct). Zero final failures/errors/skips/warnings. Browser selection, missing-dependency/timeout fallback and ingestion gates were mocked; live Chromium was not tested because Playwright is absent. No new official archive was acquired, no workspace ledger was created, and all 28 provider calendars remain unverified.
