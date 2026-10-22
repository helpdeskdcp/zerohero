# MCX specification acquisition — 2026-10-04

**Current update:** the three local operator-provided PDFs have now been inspected: 36 explicit NATURALGAS futures schedule entries, zero independently observed trading records, and zero validated production expiries. The initial HTTP-denial record below is preserved as acquisition history; see the local inspection section for current evidence.

Three newly supplied exact official URLs were requested once each using certificate-validated HTTPS GET, with no redirects, retries, proxies or identity spoofing. Each returned HTTP 403 and HTML. No PDF was retrieved; no expiry row was ingested. This is an access failure from this environment, not proof that MCX lacks the documents.

## Source attempts

- URL: https://www.mcxindia.com/docs/default-source/products/contract-specification/natural-gas/natural-gas-january-2026-contract-onwards2ff2141b-f6e4-4b6a-9f0e-59f260cecf9b.pdf
  - Retrieval: `2026-10-04T08:56:43.479540+00:00`; HTTP 403; text/html; 587 bytes.
  - SHA256 of **denial response**, not PDF: `1b9ecee9dca6700d40acad6302aa4832405c7304c18b6589f7084e9727115034`.
  - Retained raw response: `artifacts/staging/mcx-naturalgas-specifications-20261004/2d950268ecf3fda267bc.body`.
- URL: https://www.mcxindia.com/docs/default-source/about-us/natural-gas-january-2027-contract-onwards.pdf
  - Retrieval: `2026-10-04T08:56:43.421563+00:00`; HTTP 403; text/html; 488 bytes.
  - SHA256 of **denial response**, not PDF: `d3c13d00570ba1579b612708a2433868d7965e02db50140a21d67118b094914d`.
  - Retained raw response: `artifacts/staging/mcx-naturalgas-specifications-20261004/5bc1a2b2c0b1105039f5.body`.
- URL: https://www.mcxindia.com/docs/default-source/products/contract-specification/natural-gas/natural-gas-january-2025-contract-onwardse3c71b50-1e45-4513-ac73-76cf7a894681.pdf
  - Retrieval: `2026-10-04T08:56:43.501183+00:00`; HTTP 403; text/html; 587 bytes.
  - SHA256 of **denial response**, not PDF: `90806cd928f2ea99379cc6b5b8233abfb9120cf8e139c0736264d17fe05b420d`.
  - Retained raw response: `artifacts/staging/mcx-naturalgas-specifications-20261004/7512762cb90ca5fe8b98.body`.

Document titles, page numbers, circular references, contract months, launch dates and expiry dates remain null. The year in a URL is not document evidence. The operator describes these as NATURALGAS futures specifications; that scope has not yet been independently inspected. No `OBSERVED_OFFICIAL` / `FIRST_PARTY_MCXX_CONTRACT_SPECIFICATION` classification was assigned to denial HTML.

## Instrument results

| Instrument | Official expiry observations | Validated production expiries | Ready |
|---|---:|---:|---|
| NATURALGAS | 0 | 0 | No |
| NATGASMINI | 0 | 0 | No |
| CRUDEOIL | 0 | 0 | No |
| CRUDEOILM | 0 | 0 | No |

NATGASMINI, CRUDEOIL and CRUDEOILM require separate own-instrument evidence. Retained official MCX documents, manifests and source registers contain no additional specification links for these instruments. Previously denied contract-availability, bhavcopy and product-page routes were not retried. There is no accessible official discovery payload available here; no guessed sibling PDF paths or third-party sources were used.

## Next acquisition boundary

Obtain the original three PDFs through permitted official MCX access, retaining their exact download URLs and retrieval timestamps, and stage them without overwriting existing files. Operator-provided bytes must retain that acquisition classification until independently reviewed. Then extract literal launch-calendar rows with page/circular references and compare overlapping document versions for conflicts. For mini and crude contracts, obtain their distinct officially published URLs/documents. No weekday derivation or inferred missing dates is permitted.

## Safety and validation

No application or existing test files changed. Existing October 1 archives remain untouched. No production evidence was installed, no provider listings were asserted, and no broker calls, orders, migrations or restarts occurred. Production remains **NOT INSTALLED**; AUTO/NEXT remains **FAIL-CLOSED**.

Validation results are recorded in `validation/mcx_specification_validation.json`. Raw denial-response metadata/checksums are under `artifacts/staging/mcx-naturalgas-specifications-20261004/`; the machine-readable review is `calendar_evidence_acquisition/parsed/mcx_specification_candidates_20261004.json`.

## Local original-PDF inspection — supersedes the acquisition gap

All three original staged files are readable, unencrypted PDFs. `pdfinfo` and page-preserving `pdftotext -layout` succeeded locally. No HTTP request was made. Original PDF bytes were not modified. Source URLs remain exactly those supplied by the operator. Operator retrieval timestamps were not supplied and remain null; local inspection timestamps are stored separately. PDF creation metadata is retained but is not treated as publication, retrieval or effective-date evidence.

The literal printed launch calendars support **36 explicit published futures schedule entries**. Classification: `OFFICIAL_PUBLISHED_CONTRACT_SCHEDULE`; source type: `MCX_OFFICIAL_CONTRACT_SPECIFICATION`; acquisition: `OPERATOR_PROVIDED_ARTIFACT`. This is not a claim of 36 observed historical trades, actual broker listings or production-validated expiries. Independent source authenticity/amendment review remains outstanding.

### Documents and page references

- **2025**: `natural-gas-january-2025-contract-onwardse3c71b50-1e45-4513-ac73-76cf7a894681.pdf` (3 pages).
  - URL: https://www.mcxindia.com/docs/default-source/products/contract-specification/natural-gas/natural-gas-january-2025-contract-onwardse3c71b50-1e45-4513-ac73-76cf7a894681.pdf
  - SHA256: `4101b025fe36cb595b7843236b50e9495fd60175b0348c39e565f7d9b08f6441`.
  - Title: Contract Specifications of Natural Gas Futures (page 1). Calendar: page 3.
  - Printed reference: **MCX/TRD/497/2024**, dated **2024-07-26**, page 3. The separate circular itself was not acquired.
  - Local inspection: `2026-10-04T09:34:45.711789+00:00`; original HTTP/download timestamp: unknown.
- **2026**: `natural-gas-january-2026-contract-onwards2ff2141b-f6e4-4b6a-9f0e-59f260cecf9b.pdf` (2 pages).
  - URL: https://www.mcxindia.com/docs/default-source/products/contract-specification/natural-gas/natural-gas-january-2026-contract-onwards2ff2141b-f6e4-4b6a-9f0e-59f260cecf9b.pdf
  - SHA256: `2f15e2ecc7f610686307d667b94c0788659b8a58acc539ec8bef96e7711cb74a`.
  - Title: Contract Specifications of Natural Gas Futures (page 1). Calendar: page 2.
  - Printed reference: **MCX/TRD/320/2025**, dated **2025-06-27**, page 2. The separate circular itself was not acquired.
  - Local inspection: `2026-10-04T09:34:45.711789+00:00`; original HTTP/download timestamp: unknown.
- **2027**: `natural-gas-january-2027-contract-onwards.pdf` (2 pages).
  - URL: https://www.mcxindia.com/docs/default-source/about-us/natural-gas-january-2027-contract-onwards.pdf
  - SHA256: `4a31495a37e671df9910a8bfbeed3b06a2da752730628dfa8a7d9d6b61b55e6e`.
  - Title: Contract Specifications of Natural Gas Futures (page 1). Calendar: page 2.
  - Printed reference: **MCX/TRD/371/2026**, dated **2026-06-29**, page 2. The separate circular itself was not acquired.
  - Local inspection: `2026-10-04T09:34:45.711789+00:00`; original HTTP/download timestamp: unknown.

### Material claims and limits

| Claim | Page in each PDF | Supported meaning / limitation |
|---|---|---|
| NATURALGAS; Natural Gas Futures; 1,250 mmBtu | 1 | Standard futures only; no mini/options cross-attribution |
| Contract Listing, Contract Start Day, Last Trading Day refer to Contract Launch Calendar | 1 | Explicit table dates govern these published schedules |
| Maximum six months, six calendar months available for trading | 1 | Lifecycle rule; not proof of actual historical listings |
| Mondays through Fridays | 1 | General trading period; no holiday-date list |
| 09.00 am to 11.30 / 11.55 pm, based on US daylight saving time | 1 | Conditional session rule; no dated DST map or explicit timezone, so exact per-contract cutoff remains null |
| Cash settlement; DDR references NYMEX front-month settlement on MCX last trading day and last available RBI USDINR rate | 2 | Settlement mechanism; does not supply a separate maturity/settlement date or replacement expiry |
| Explicit calendar-year launch/expiry table and reference circular | 2025: 3; 2026/2027: 2 | Only the printed dates/year scope; no extension from “onwards” in filenames |

No explicit holiday-adjustment rule or holiday calendar was found in any of the seven pages. No complete later-amendment history or dated effective interval for every specification clause was established. The referenced circular dates are citations, not proof that every clause became effective on that date. No independent historical trading/EOD records were provided.

### All explicit published rows

| Contract month | Launch date | Published expiry / last-trading date | PDF page |
|---|---|---|---|
| 2025-01 | 2024-07-29 | 2025-01-28 | 2025 p.3 |
| 2025-02 | 2024-08-28 | 2025-02-25 | 2025 p.3 |
| 2025-03 | 2024-09-26 | 2025-03-26 | 2025 p.3 |
| 2025-04 | 2024-10-29 | 2025-04-25 | 2025 p.3 |
| 2025-05 | 2024-11-26 | 2025-05-27 | 2025 p.3 |
| 2025-06 | 2024-12-27 | 2025-06-25 | 2025 p.3 |
| 2025-07 | 2025-01-29 | 2025-07-28 | 2025 p.3 |
| 2025-08 | 2025-02-26 | 2025-08-26 | 2025 p.3 |
| 2025-09 | 2025-03-27 | 2025-09-25 | 2025 p.3 |
| 2025-10 | 2025-04-28 | 2025-10-28 | 2025 p.3 |
| 2025-11 | 2025-05-28 | 2025-11-24 | 2025 p.3 |
| 2025-12 | 2025-06-26 | 2025-12-26 | 2025 p.3 |
| 2026-01 | 2025-07-29 | 2026-01-27 | 2026 p.2 |
| 2026-02 | 2025-08-27 | 2026-02-24 | 2026 p.2 |
| 2026-03 | 2025-09-26 | 2026-03-26 | 2026 p.2 |
| 2026-04 | 2025-10-29 | 2026-04-27 | 2026 p.2 |
| 2026-05 | 2025-11-25 | 2026-05-26 | 2026 p.2 |
| 2026-06 | 2025-12-29 | 2026-06-25 | 2026 p.2 |
| 2026-07 | 2026-01-28 | 2026-07-28 | 2026 p.2 |
| 2026-08 | 2026-02-25 | 2026-08-26 | 2026 p.2 |
| 2026-09 | 2026-03-27 | 2026-09-25 | 2026 p.2 |
| 2026-10 | 2026-04-28 | 2026-10-27 | 2026 p.2 |
| 2026-11 | 2026-05-27 | 2026-11-24 | 2026 p.2 |
| 2026-12 | 2026-06-26 | 2026-12-28 | 2026 p.2 |
| 2027-01 | 2026-07-29 | 2027-01-25 | 2027 p.2 |
| 2027-02 | 2026-08-27 | 2027-02-23 | 2027 p.2 |
| 2027-03 | 2026-09-28 | 2027-03-25 | 2027 p.2 |
| 2027-04 | 2026-10-26 | 2027-04-27 | 2027 p.2 |
| 2027-05 | 2026-11-25 | 2027-05-25 | 2027 p.2 |
| 2027-06 | 2026-12-29 | 2027-06-25 | 2027 p.2 |
| 2027-07 | 2027-01-27 | 2027-07-27 | 2027 p.2 |
| 2027-08 | 2027-02-24 | 2027-08-26 | 2027 p.2 |
| 2027-09 | 2027-03-29 | 2027-09-27 | 2027 p.2 |
| 2027-10 | 2027-04-28 | 2027-10-26 | 2027 p.2 |
| 2027-11 | 2027-05-26 | 2027-11-24 | 2027 p.2 |
| 2027-12 | 2027-06-28 | 2027-12-28 | 2027 p.2 |

Every row in the JSON retains the raw row text, literal date strings, normalized dates, source URL, original document SHA256, document title, page number and circular citation. There are 36 unique contract months, no duplicate rows and no cross-source conflicts within these three disjoint annual schedules. This does not rule out amendments in other publications.

### Production assessment

| Instrument | Published schedule entries inspected | Observed trading records from these documents | Validated production expiries | Remaining evidence |
|---|---:|---:|---|
| NATURALGAS futures | 36 | 0 | 0 | Independent provenance and amendment/effective-scope review; dated cutoff/DST and holiday evidence required by the existing gate; reviewed complete contract mapping/coverage |
| NATGASMINI | 0 | 0 | 0 | Its own mini-contract specification/calendar/circular and lifecycle evidence |
| CRUDEOIL | 0 | 0 | 0 | Its own specification/calendar/circular and lifecycle evidence |
| CRUDEOILM | 0 | 0 | 0 | Its own mini-contract specification/calendar/circular and lifecycle evidence |

NATURALGAS now has concrete, page-cited published schedule evidence, but cannot obtain production validation from these documents alone. No production resolver, calendar, broker adapter or scheduler was changed. AUTO/NEXT remains FAIL-CLOSED.

### Local-inspection regression validation

See `validation/mcx_local_specification_validation.json` for the current test/type/checksum results. The earlier validation record remains unchanged. Forty-one added regression cases protect literal row extraction, all three raw PDF hashes, page-level claims, operator provenance, schedule-versus-trading separation and non-activation.
