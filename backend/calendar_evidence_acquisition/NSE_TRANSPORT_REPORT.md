# NSE transport resilience — 2026-10-03

Implementation validated offline; live discovery remains blocked. No production
calendar installed, no scheduler enabled, AUTO/NEXT remains fail-closed.

## Live observations

The acquisition dry-run used the dedicated Playwright 1.63.0 / Chromium
153.0.8010.12 environment with normal TLS verification and the existing public
evidence user-agent. Chromium's default transport failed at NSE's root page with
`ERR_HTTP2_PROTOCOL_ERROR`. The new HTTP/1.1 fallback then failed. A single final
instrumented HTTP/1.1 attempt isolated that failure to the 20-second `Page.goto`
timeout at `ROOT_PAGE`. No HTTP status or cookies were obtained for the root page.
No report label, report DOM, download request or download URL was observed.

The CLI returned:

```json
{"calendar_status":"UNVERIFIED","detail":"NSE_HTTP1_TRANSPORT_FAILED","production_calendar":"NOT INSTALLED","reason":"SOURCE_METADATA_REQUIRED","status":"UNVERIFIED"}
```

Separate address-family diagnostics used one HEAD to `/robots.txt` per reachable
host/family, with certificate verification, SNI and HTTP/1.1 ALPN. Both hosts worked
over IPv4 (TLS 1.3, HTTP/1.1 200). The archive host also worked over IPv6. The report
host returned no IPv6 address in this resolver lookup (`gaierror`, errno -5).
See `nse_transport_network_20261003.json` for exact observations and timestamps.
Robots availability is not report availability or proof of archive provenance.
IP blocking/WAF rejection is not established: no report-page 403/429 or challenge
was observed. No security controls or certificate checks were disabled.

Direct archive fallback was tested with mocks only. It was not attempted live,
because browser discovery did not establish an authoritative download URL. The
existing October 1 archive was not downloaded again.

## Operator provenance and unchanged archive

The operator supplied the exact URL:
`https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_20261001_F_0000.csv.zip`
and title `F&O - UDiFF Common Bhavcopy Final (zip)`.
The existing manual contract in `EVIDENCE_ENGINE.md` explicitly permits operator
assertions while retaining UNVERIFIED status and independent-review requirements.
The six-field sidecar was therefore added at
`../../artifacts/staging/BhavCopy_NSE_FO_20261001.csv.zip.json`. It contains only
exchange, segment, trading_date, provenance_url, report_name and SHA256. There is
no invented HTTP status, live observation timestamp or browser discovery hash.
No receipt/ledger was created and no ingestion was performed.

The child hash remains
`d44a052b79a3961910f74783960ebbd5043696dbed39e7fb86ec84929fd60338`.
The original multi-archive hash remains
`2c5aae28b3d6682ee8dc2c7162041acfbf5c6999233d653a2efd157450f40fb0`.
The independent browser provenance requirement remains unresolved. Neither this
sidecar nor successful local parsing makes a calendar VERIFIED.

The local child dry-run passed parsing (`parser_error: null`) and returned
`UNVERIFIED / INDEPENDENT_REVIEW_AND_CALENDAR_COMPLETENESS_REQUIRED`. The parent
multi-archive still returns `SOURCE_METADATA_REQUIRED`, so the batch CLI exits 2.
The extracted expiry fields are observations from the staged bytes, not independently
verified exchange/provider calendars. No HTTP status is attributed to these bytes.

## Validation

The focused transport/downloader suite passed 99 cases. The complete relevant suite
passed 858 cases (825 existing plus 33 new), and Phase-2 diagnostics passed another
4: 862 distinct passing cases, no failures/errors/skips/warnings. Initial test-first
run: 18 expected failures and 3 passes before implementation. Mypy passed for five
acquisition modules; six syntax checks passed without writing bytecode. Tests blocked
network/secret reads and used temporary storage. The live results above are separate
from these mocked tests. Neither successful mocks nor operator metadata establish
live acquisition success.

## Next boundary

Resolve lawful report-page access with the VPS/network operator or NSE before
another live attempt. The HTTP/1.1 fallback exists but has not produced a usable
NSE report in this environment. Do not enable the production timer on this result.
