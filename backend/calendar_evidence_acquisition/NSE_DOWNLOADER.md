# Autonomous NSE evidence acquisition

This component acquires **exchange observations for review**, not verified provider
calendars. It cannot activate production AUTO/NEXT. It starts at the fixed official
report page `https://www.nseindia.com/all-reports-derivatives`; no historical download
URL template exists in the implementation.

## Entry points and environment

Run from `/root/zerohero`. The isolated environment and Chromium headless shell are
installed under this evidence directory; the application virtualenv is unchanged.

```sh
export PLAYWRIGHT_BROWSERS_PATH=/root/zerohero/backend/calendar_evidence_acquisition/.browsers
backend/calendar_evidence_acquisition/.venv/bin/python -B -m backend.calendar_evidence_acquisition.nse_downloader --date 2026-10-01 --dry-run
```

After review, omit `--dry-run` to acquire a NEW date. Use `--expected-sha256` only
when an independent expected hash is available. `--artifacts` selects the local
evidence root (default `./artifacts`). No broker configuration or `.env` is read.

To recreate this isolated environment, use Python's `venv` in `.venv`, install
`requirements-nse-browser.txt`, then run `python -m playwright install chromium
--only-shell` with the browser-path variable above. The installed versions for this
validation are Playwright 1.63.0 and pandas 3.0.5. Mypy was installed here only for
development validation; it is not a production dependency. No OS packages were installed.

## Discovery and security

The normal Playwright context identifies itself as `ZeroHero-public-evidence/1`.
It checks robots policy per official origin and seeds the official root before
opening the report page. It matches exactly `F&O - UDiFF Common Bhavcopy Final (zip)`
with whitespace/case normalization; similar report names are rejected.

Discovery uses, in order:

1. Actual DOM links in a semantic container with that exact report label.
2. Actual JSON responses emitted by the page, requiring label, date and full URL
   in the same report object. It never constructs or probes a JavaScript API route.
3. A unique Download button scoped to the exact report row after selection through
   an unambiguous date control. Requests are captured and aborted before the body
   is downloaded. Missing or ambiguous URLs remain unavailable.

Only explicitly labeled date inputs and recognized date placeholders are supported.
Unknown date pickers/report layouts fail closed; no positional selectors or invented
endpoint adapters are used. Filename parsing can check an OBSERVED URL's date; it
never constructs one. Archive CONTENT must subsequently establish the requested date.

Only HTTPS on the existing NSE allowlist (`www.nseindia.com`, `nsearchives.nseindia.com`,
`archives.nseindia.com`) is permitted, on the standard port, without credentials or
sensitive query parameters. GET/HEAD only. Redirects are blocked for review, including
same-exchange redirects; the discovered URL is never silently replaced. Unknown or
restrictive robots policies, crawl-rate directives, 403 and 429 stop acquisition.
No stealth configuration, challenge solving, imported sessions or third-party data.

Each browser discovery session is bounded to 90 seconds and download to 60 seconds,
with shorter navigation/request limits. A confirmed `ERR_HTTP2_PROTOCOL_ERROR`
permits one fresh Chromium session with HTTP/2 and QUIC disabled. This changes only
protocol negotiation: the same user-agent, TLS validation, robots checks and origin
restrictions remain in force. HTTP/1.1 failure is terminal for that acquisition;
403/429, redirects, policy and provenance failures never trigger this fallback.
Other transient failures retain the existing maximum three attempts (1s/2s backoff).

After discovery, a browser download protocol/reset/closed-connection failure may
use a direct standard-library HTTP/1.1 GET to the exact observed archive URL. The
report/date/URL must match the browser's records and robots must permit it. Only
the existing archive hosts are allowed, redirects are rejected, TLS certificates
remain verified, response size/time are bounded, and pending files cannot overwrite
anything. All original ZIP/CSV/date/hash/engine checks still follow the transfer.
Direct transport cannot discover provenance or turn an operator URL into browser
evidence. Its actual HTTP/TLS metadata is retained in the acquisition proof.
Structured logs record transport, stage and allowlisted error codes, never cookies,
headers or raw browser exception text. See `NSE_TRANSPORT_REPORT.md` for live results.

## Validation, storage and provenance

The ZIP must contain exactly one root-level NSE FO UDiFF CSV for the requested date.
Traversal/nested paths, ambiguous members, corrupt ZIP/CRC and oversized content are
rejected. All required fields are checked; every row must have matching `TradDt`,
`BizDt`, `Src=NSE` and `Sgmt=FO`. Derivative/option types, literal expiry fields and
finite numeric values are validated. Blank numeric values remain explicitly missing,
not zero. At least one supported index must be present; no historical availability
of all four indices is inferred. Index-only OHLC datasets cannot pass this schema.
Actual expiry and nominal expiry remain distinct in the existing parser.

After validation, original bytes and metadata are published using a fully fsynced
temporary file and an atomic no-clobber hard-link publication. This deliberately
avoids `os.replace`, which could overwrite an existing verified file. A per-root
advisory lock coordinates jobs; conflicting external writes are also rejected.
Invalid/mismatched downloaded bytes go to `artifacts/nse/quarantine/` with a reason.
No existing archive is overwritten or deleted.

Sidecars use the established engine fields plus traceable observed metadata:

```json
{
  "exchange": "NSE",
  "segment": "FO",
  "trading_date": "<content-validated YYYY-MM-DD>",
  "provenance_url": "<exact observed AND requested official URL>",
  "report_page_url": "https://www.nseindia.com/all-reports-derivatives",
  "report_name": "F&O - UDiFF Common Bhavcopy Final (zip)",
  "filename": "<observed download filename>",
  "sha256": "<SHA256 of original ZIP bytes>",
  "http_status": "<actual observed integer>",
  "content_type": "<observed response content type>",
  "discovered_at": "<actual observation timestamp>",
  "downloaded_at": "<actual download completion timestamp>",
  "discovery_sha256": "<retained rendered report DOM hash>",
  "acquisition_proof_sha256": "<retained acquisition record hash>"
}
```

This is a schema illustration, not usable evidence. The code writes `http_status`
as an integer, not a placeholder string. Rendered DOM is explicitly a browser
snapshot, not a claim about the original HTTP response-body bytes. Relevant JSON
responses and their URLs/hashes are also retained. No status/timestamp is invented.

Artifacts are stored in `staging/`, `nse/proofs/`, `nse/receipts/` and `nse/inventories/`.
The engine is called only after archive validation and a conflict-checking preview.
Its ledger retains BROWSER acquisition and the acquisition-proof hash; provider
listing/calendar status remains UNVERIFIED. A validated per-date receipt lets the
next explicit-date run return `ALREADY_VERIFIED` without network/download. **That
label applies only to archive/acquisition validation, never calendar completeness.**
Reuse checks archive, sidecar, proof, source bodies and engine raw/inventory hashes.

An interrupted operation may leave immutable proof/staged artifacts without a final
receipt. Those are preserved and fail closed for operator review; they are not
silently trusted or redownloaded. This is safe recovery, not automatic reconstruction
of an incomplete transaction.

## Existing October 1 archive

The pre-existing `BhavCopy_NSE_FO_20261001.csv.zip` remains byte-for-byte unchanged:
`d44a052b79a3961910f74783960ebbd5043696dbed39e7fb86ec84929fd60338`.
The new validator checked its 33,250 rows and all four target indices successfully.
No sidecar was created. A matching URL observed today cannot retroactively prove
which bytes the previous download returned. Discovery may proceed, but an existing
archive without retained acquisition proof returns `SOURCE_METADATA_REQUIRED` with
`EXISTING_ARCHIVE_ORIGIN_UNPROVEN` once a URL is found. It is never redownloaded merely
to implement or test this component.

## Scheduler

`python -m backend.calendar_evidence_acquisition.nse_job` is the cron-compatible
entry point. It supports `--dry-run` and `--artifacts`. Dates come from the most
recent observed Final report strictly before today in Asia/Kolkata. No weekday,
holiday or expiry calculation substitutes for an observed report date. Today's
report is conservatively excluded; unavailable listings mean no acquisition.

The application scheduler is coupled to trading workers and was not modified.
Templates in `systemd/zerohero-nse-evidence.{service,timer}` run the isolated job
daily at 07:00 IST with jitter. They were syntax-checked but **not installed,
enabled or started**. They load no application environment file. Installation is
a separate operator deployment action after reviewing paths/permissions and live
access. The oneshot may return exit 2 for expected fail-closed acquisition gaps.

## Live result versus mocked validation

On 2026-10-03, the installed browser performed the authorized October 1 dry-run.
No authoritative URL could be acquired. Its exact result was:

```json
{"calendar_status":"UNVERIFIED","detail":"NSE_CONNECTION_FAILED","production_calendar":"NOT INSTALLED","reason":"SOURCE_METADATA_REQUIRED","status":"UNVERIFIED"}
```

No archive was downloaded and no evidence was ingested during that live run.
Mocked tests establish implementation behavior, not NSE reachability or historical
calendar correctness. Do not repeat blocked requests indefinitely or activate any
production calendar from this report. All 28 calendar/provider combinations remain
UNVERIFIED. Production Calendar: NOT INSTALLED. Historical AUTO/NEXT: FAIL-CLOSED.
