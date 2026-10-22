# Official NSE page investigation — 2026-10-03

Source: `https://www.nseindia.com/all-reports-derivatives#cr_deriv_equity_archives`.
The fragment is a client-side tab identifier, not part of the HTTP request. The
actual network request was GET `/all-reports-derivatives`. HTTP/1.1 succeeded with
verified HTTPS, no redirects, existing evidence user-agent, and allowed robots
policy. No browser, broker API, production ingestion or calendar activation ran.

## Independently observed findings

1. HTML contains `F&O - UDiFF Common Bhavcopy Final (zip)` and control
   `id="ard-udiffcb"`, initially `href="#"`. The historical selector is HTML;
   its download action is JavaScript. NSE's page states that the old formats were
   discontinued on July 8, 2024 and references circular 62424 dated June 12, 2024.
   This is a retained page notice, not independent acquisition of that circular.
2. The page-referenced `all-reports.js?v=01102026` explicitly requests
   `/api/daily-reports?key=` for a symbol list including `FO`, then renders
   `CurrentDay` and `PreviousDay` with `ReportCard`. One policy-checked GET of that
   exact published FO listing mechanism returned HTTP 200 JSON.
3. CurrentDay contains the actual report date `01-Oct-2026`, displayName
   `F&O-UDiFF Common Bhavcopy Final (zip)` and filename
   `BhavCopy_NSE_FO_0_0_0_20261001_F_0000.csv.zip`. PreviousDay supplies a separate
   September 30 observation. These dates come from the response, not a filename
   calendar rule. The JSON supplies `filePath` and `fileActlName` separately.
4. The page-referenced `nse-min.js?v=01102026` sets
   `downloadLink = item.filePath + item.fileActlName`, places that value in
   `data-link`, and calls `window.open(downloadLink, '_blank')` on click.
   The returned path contains a double slash after the host. We did not normalize
   it, concatenate the fields into asserted provenance, execute the renderer,
   fabricate a date-specific URL, or request an archive based on a reconstruction.
   No complete report URL was captured as a live link/request. Under the task's
   strict exact-observed-URL rule, acquisition remains SOURCE_METADATA_REQUIRED.
5. `Contract Expiry Details (Life Cycle)` is a fragment-only `href="#"` link in
   **commodity** compliance tab `h_report_deriv_commodity7`. It exposes no contract,
   expiry or lifecycle download URL. It does not establish an NSE index calendar.
6. The embedded equity historical contract section `h_report_deriv_equity1` contains the
   advertised Symbol, Date, Expiry, OHLC, LTP, Settle Price, Open Int, Change in OI
   and Underlying Value columns. Its static HTML includes duplicated 2018 sample
   rows and stale example controls. That markup alone is not verified trading
   data or calendar evidence; classification is INDEPENDENT_REVIEW_REQUIRED.
7. Separately, an actual navigation link on the primary page points to
   `https://www.nseindia.com/report-detail/fo_eq_security`. That page loaded with
   HTTP 200 and the title `Historical Contract-wise Price Volume Data`. Its exact
   linked script `contract-wise-pricevolume-fo.js?v=01102026` exposes the public
   expiry-selector and contract-data requests. One selector request for
   `OPTIDX`, `NIFTY`, `2026` returned 50 literal dates in `expiresDts`. This proves
   current selector observations only, not historical publication/listing dates,
   holiday policy, cutoff rules or a complete calendar. No weekly dates were filled.
8. A single historical-data query used the script's published parameters for
   `01-10-2026` through `01-10-2026`, OPTIDX/NIFTY/CE, and the literal selector
   value `06-OCT-2026`. The response is 39,866 bytes, HTTP 200 JSON, 70 records,
   SHA256 `95b34980827dc7d509580156c80ec58dce61dc879cc466581046b2c27da62add`.
   All rows have the requested symbol/instrument/side/date and explicit expiry.
   The raw response retains `FH_EXPIRY_DT`, OHLC, LTP, settlement, OI, change in OI,
   underlying value and other original fields. Classification: HISTORICAL_CONTRACT_DATA.
   The full URL and source chain are in the parsed inventory and artifact sidecar.
   This is not a Bhavcopy, a provider listing history or a complete calendar.

## Traceable staging and classifications

Ten original response bodies (robots, two pages, four page-referenced JavaScript
files, FO listing JSON, expiry-selector JSON and historical-contract JSON) are retained once under
`artifacts/staging/nse-discovery-20261003/`. Each has its own SHA256 and sidecar with
actual URL, status, content type, header length when present, measured byte count,
observation timestamp, policy result and `ingested=false`. Files have deterministic
hash-suffixed staging names; source basenames are retained separately. No invented
report date is assigned to a page or JavaScript file.

The page/API responses are REPORT_ARCHIVE context; policy/code sources are
INDEPENDENT_REVIEW_REQUIRED. `independently_observed=true` on their sidecars applies
only to those retrieved resources, never to an undownloaded Bhavcopy or provider
calendar. The historical JSON is separately classified HISTORICAL_CONTRACT_DATA.
`parsed/nse_page_discovery_20261003.json` links every source hash, path and URL and
retains the exact report-listing records, including split URL fields. The original
historical JSON remains unmodified in staging; it was not converted into synthetic
CSV or used to install an expiry calendar.

The existing October 1 child ZIP remains OPERATOR_PROVIDED_ARTIFACT, UNVERIFIED for
independent provenance. Its archive bytes and the parent ZIP were not modified or
downloaded again. Its existing manual sidecar now explicitly distinguishes supplied
provenance from an independently observed download, without inventing HTTP metadata.
The API metadata corroborates its claimed title/date/name but does not bind those
local bytes to a completed exchange download. No ledger or receipt was created.

## Network limits and next action

This run establishes working HTTP/1.1 access to the report page and public listing
API and one historical contract-data response, superseding the earlier assertion
that the page itself is unreachable. It does not establish Bhavcopy archive endpoint
availability. No 403, 429 or redirect was
overridden. Earlier independently recorded IPv4/IPv6/TLS and HTTP/2 diagnostics
remain in `nse_transport_network_20261003.json` and `NSE_TRANSPORT_REPORT.md`; they
were not repeatedly probed here. Browser discovery is not permitted by this task,
so no browser was used to manufacture or capture an otherwise unobserved URL.

For Bhavcopy, the next required evidence is the exact completed download action/link exposed
by NSE for the report, followed by an allowed response and archive validation.
An operator may retain the original URL/action metadata from the normal NSE UI.
For the historical JSON, broader coverage, source-meaning review and independent
provider listing/publication/cutoff evidence remain missing. One official response
must not be extrapolated into the full 28-combination calendar matrix.
No expiry, source URL or provider listing will be guessed to fill this gap.
All 28 provider/instrument combinations remain unresolved. Production Calendar
NOT INSTALLED; AUTO/NEXT FAIL-CLOSED.

## Validation and operational boundary

Focused source/downloader/transport tests: 104 passed. Complete relevant suite:
863 passed, plus 4 Phase-2 diagnostics (867 distinct cases, zero failures, errors,
skips or warnings). Five new regression cases protect actual source identities,
missing complete URLs, placeholder links and the distinction between official
contract data and verified provider calendars. Seven syntax/compile checks and
mypy for five acquisition modules passed. Tests blocked network, secret reads and
production database writes. `git diff --check` passed.

The staged child ZIP passed signature/member/CRC/SHA256 and existing parser checks.
Its local dry-run remains UNVERIFIED with
INDEPENDENT_REVIEW_AND_CALENDAR_COMPLETENESS_REQUIRED. The unmodified parent ZIP
still lacks source metadata; batch dry-run exits 2. No ledger/receipt was created.
Use the stage-local `SHA256SUMS` to recheck all ten raw responses and their sidecars.
The source inventory records exact request URLs, parent-document hashes and bounds
for reproducibility; every future acquisition must recheck robots/access policy.

This investigation used direct HTTP/1.1 only. Do not run the existing browser-first
acquisition CLI as part of this procedure: the updated task permits browser fallback
only after an exact report URL is independently observed and direct transport fails.
Application/runtime code and the uninstalled scheduler were left unchanged.
