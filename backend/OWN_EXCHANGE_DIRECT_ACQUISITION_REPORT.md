# Own-exchange direct acquisition — 2026-10-04

**PARTIAL: BSE official contract-expiry observations acquired; production NOT READY.**

No production installation, AUTO/NEXT activation, ingestion, broker call/order, migration, scheduler change or restart occurred. Existing October 1 archives and previous evidence packages remain unchanged.

## Acquisition method

This pass followed the owner’s explicit instruction to inspect the actual official data routes independently of robots diagnostics. It made ten bounded, certificate-verified HTTPS GETs with no automatic redirects, identity spoofing, proxies, authentication or retries. The unattended acquisition policy and production gate were not changed. Data-route denials were respected and retained.

Seven requests succeeded: the BSE production-master ZIP, format/sample ZIP, three published JavaScript imports, and two directly linked circular PDFs. Three data requests returned 403: both MCX routes and BSE’s published underlying-list API. No response to robots.txt was used as expiry evidence.

## Exact sources, times and hashes

| Source URL | Retrieval timestamp | HTTP | SHA256 |
|---|---|---:|---|
| https://www.mcxindia.com/market-data/bhavcopy | 2026-10-04T08:34:20.320555+00:00 | 403 | `a5b24711cdb2484703e3cb22a1db4133c53b87c32c622e3abf8dece9968c606e` |
| https://www.bseindia.com/assets/includenew/js/chunk-4TDC2PZO.js | 2026-10-04T08:40:19.660606+00:00 | 200 | `9936cba0cd80a35ab8c40d7aa06335590bf0e0c3d72745a4270c63bba394bd71` |
| https://www.bseindia.com/downloads/UploadDocs/Notices/20260415-34/20260415-34.pdf | 2026-10-04T08:43:22.527173+00:00 | 200 | `792e27317b97ec9bb7622284d7713d6920f3b33b63575e4f8a16621ff656a9b2` |
| https://www.bseindia.com/downloads/UploadDocs/Notices/20260702-38/20260702-38.pdf | 2026-10-04T08:43:22.739489+00:00 | 200 | `d8ada2d72cd23340b9f26bf4133fecd8fba5360fcea0cfce7eac12783e16901c` |
| https://api.bseindia.com/BseIndiaAPI/api/ddlUnderlyingAsset/w?ProductType=IO&scrip_cd=0 | 2026-10-04T08:40:19.845026+00:00 | 403 | `1ff7686857e9353c88eb6101f529decb48820f1f9390de4209d91f46a15a4679` |
| https://www.bseindia.com/downloads1/Live_Masters_for_Equity_Derivatives.zip | 2026-10-04T08:34:18.050960+00:00 | 200 | `bde5c37743a6a9aa55871a741e82e97f15067b52c4242edecc5741a5250b6c85` |
| https://www.bseindia.com/downloads1/EOD_FF_and_Sample_Files_BSE_Derivativ_segment.zip | 2026-10-04T08:34:19.204886+00:00 | 200 | `fc5bb15ac6508894e775804ee2741ece1ec1b37b970c6d978b2df6be8f1f1c80` |
| https://www.bseindia.com/assets/includenew/js/chunk-LPPH6725.js | 2026-10-04T08:39:01.983965+00:00 | 200 | `a717433730b33092c0850e5259721cc7cc1d97debe0aaec802a4b6e72a42bc85` |
| https://www.bseindia.com/assets/includenew/js/chunk-OSCYTWKO.js | 2026-10-04T08:39:02.247905+00:00 | 200 | `8f39e5703afdc4237afa827d2d995a66893e9f97cf302ee2fc59a244c202e8d0` |
| https://www.mcxindia.com/market-data/contract-available-for-trading | 2026-10-04T08:34:20.296130+00:00 | 403 | `b4f4c80d83ad47628db8aa9f9f0a63c57cadeccc00de9b93f2feb1f8065bf3d3` |

Every raw response and actual HTTP metadata is retained under `artifacts/staging/own-exchange-direct-20261004/`. The BSE master and format URLs were literal links in retained official website JavaScript. Additional JS imports and PDF hrefs were followed only after observation in the official source. BSE API base, endpoint name and `ProductType=IO&scrip_cd=0` request parameters came from the official page implementation. No unknown endpoint/date pattern was generated.

## BSE SENSEX

- Production master archive: 2,349,529 bytes, SHA256 `bde5c37743a6a9aa55871a741e82e97f15067b52c4242edecc5741a5250b6c85`.
- Selected actual CSV member: `BSE_EQD_CONTRACT_01102026.csv`; total source rows 38,629.
- SENSEX rows: **4,051** distinct contract IDs: **4,048 options and 3 futures**. Exact `TckrSymb=SENSEX` selection; no symbol inference from contract names.
- **21 distinct observed expiry dates**. Zero duplicate rows and zero conflicting identifiers in this snapshot.
- Retained states include 394 expired contracts and 658 reserved contracts (overlap is possible). Neither is silently presented as a current tradable contract.
- Normalized records are `OBSERVED_OFFICIAL_EXPIRY`; **zero VALIDATED_PRODUCTION_EXPIRY entries**. Broker historical listings remain unverified.

### Schema and semantics

Official file-format PDF: **Version 14.2, December 2025, updated 08-12-2025**, pages 12–16. PDF SHA256 `83bf3f010e87e2b3c1ae04947547cef345409c4a216d2847e6f5e734124ab622`, retained inside the format ZIP. Its sample data was excluded from expiry observations.

- `FinInstrmId`: explicit contract identifier. `FinInstrmNm`: IO=index option, IF=index future. The similarly named `FinInstrmTp` is a filler in this master schema.
- `XpryDt`: actual last-trading **date**. `StartDt`: contract start date, not proof of the intraday listing/publication instant.
- `EndDt`: settlement/expiry/maturity date, stored separately. **`MtrtyDt` is a filler**, not a maturity-date substitute.
- `StrkPric` is published in paise; normalization uses exact decimal division by 100. Futures retain no synthetic option strike/side.
- `CtrctTp` explicitly supplies W/M/Q/H cycle tags. No weekly/monthly classification was inferred from dates.
- `PrtdToTrad` supplies active/reserved status; `DelFlg` supplies active/expired/marked-for-expiry status.

### Literal observed dates

| Expiry | Contracts | Contract types | Published cycles |
|---|---:|---|---|
| 2026-10-01 | 394 | OPTION | WEEKLY |
| 2026-10-08 | 398 | OPTION | WEEKLY |
| 2026-10-15 | 390 | OPTION | WEEKLY |
| 2026-10-22 | 374 | OPTION | WEEKLY |
| 2026-10-29 | 425 | FUTURE, OPTION | MONTHLY |
| 2026-11-05 | 342 | OPTION | WEEKLY |
| 2026-11-12 | 322 | OPTION | WEEKLY |
| 2026-11-19 | 282 | OPTION | WEEKLY |
| 2026-11-26 | 391 | FUTURE, OPTION | MONTHLY |
| 2026-12-31 | 347 | FUTURE, OPTION | HALF_YEARLY, MONTHLY |
| 2027-03-25 | 36 | OPTION | QUARTERLY |
| 2027-06-24 | 36 | OPTION | HALF_YEARLY |
| 2027-09-30 | 26 | OPTION | QUARTERLY |
| 2027-12-30 | 36 | OPTION | HALF_YEARLY |
| 2028-06-29 | 36 | OPTION | HALF_YEARLY |
| 2028-12-28 | 36 | OPTION | HALF_YEARLY |
| 2029-06-28 | 36 | OPTION | HALF_YEARLY |
| 2029-12-27 | 36 | OPTION | HALF_YEARLY |
| 2030-06-27 | 36 | OPTION | HALF_YEARLY |
| 2030-12-26 | 36 | OPTION | HALF_YEARLY |
| 2031-06-26 | 36 | OPTION | HALF_YEARLY |

These are contract expiry observations, **not proven historical coverage from October 2026 through June 2031**. One acquired snapshot cannot establish contracts available at every earlier/later timestamp. The filename date and HTTP Last-Modified header are retained metadata, not a manufactured authoritative market-data timestamp.

### Additional official evidence and limitations

The official BSE static-market JavaScript contains **16 literal 2026 equity/equity-derivatives holidays**. The list is retained separately and has not been used to derive any missing expiry. Its own note allows exchange amendments and says November 8 Muhurat hours will be separately notified.

The official SENSEX specification component publishes current cycle descriptions and trading hours ending at **03:40 PM**. A separate FAQ in the same acquired source says **3:30 pm**. Applicability/effective dates require independent review; no historical cutoff was assigned from either text. Current weekday descriptions were retained as context only, never applied backwards or used to generate dates.

Two linked circulars, 20260415-34 and 20260702-38, concern **BSE FOCUSED IT**, not SENSEX or MCX. They were retained but explicitly excluded from the requested instrument evidence.

Remaining BSE requirements: historical snapshot coverage, publication/listing intervals, independently validated contract-specific intraday cutoffs and timezone/effective periods, applicable amendments, and complete production review. **READY: NO**.

## MCX, separately by instrument

| Instrument | Observed expiries | Validated production expiries | Master/lifecycle coverage | READY |
|---|---:|---:|---|---|
| CRUDEOIL | 0 | 0 | Missing | No |
| CRUDEOILM | 0 | 0 | Missing | No |
| NATURALGAS | 0 | 0 | Missing | No |
| NATGASMINI | 0 | 0 | Missing | No |

Exact data-route results:

- `https://www.mcxindia.com/market-data/contract-available-for-trading`: HTTP 403 at 2026-10-04T08:34:20.296130+00:00. SHA256 `b4f4c80d83ad47628db8aa9f9f0a63c57cadeccc00de9b93f2feb1f8065bf3d3`.
- `https://www.mcxindia.com/market-data/bhavcopy`: HTTP 403 at 2026-10-04T08:34:20.320555+00:00. SHA256 `a5b24711cdb2484703e3cb22a1db4133c53b87c32c622e3abf8dece9968c606e`.

Both payloads are access-denied HTML, not contract data. They expose no usable first-party contract-document links or market-data request configuration. No further MCX PDF/API path was invented. This is an environment/source-access blocker, not a claim that MCX has no official records. Each instrument still needs actual own-exchange contract IDs and explicit expiry/maturity records, product/launch/circular evidence, cutoff/holiday applicability and independent review.

## Files and offline reproducibility

- `calendar_evidence_acquisition/bse_master.py`: evidence-only parser for the acquired schema; validates source hashes, literal discovery links, ZIP/CRC/path safety, schema version, dates/enums/numbers and duplicate/conflicting IDs. Never installs a calendar.
- `calendar_evidence_acquisition/parsed/bse_sensex_master_observations_20261004.json.gz`: all normalized SENSEX records with source/CSV/schema hashes and row references.
- `calendar_evidence_acquisition/parsed/own_exchange_direct_evidence_20261004.json`: source ledger, archive inventory, observed dates, holidays/specification excerpts, circular exclusions and per-instrument blockers.
- `tests/test_bse_master_evidence.py`: deterministic parser and retained-source regression tests. Synthetic toy rows test behavior only; acquired-source tests independently replay the real snapshot offline.

## Next evidence action

BSE: independently review the retained master/specification/holiday evidence, resolve timing applicability, and obtain the missing historical snapshot/publication/cutoff evidence through official channels. MCX: obtain permitted access or operator-retained official responses/documents from the identified routes, with exact URLs and raw bytes. Neither exchange can be activated from this package alone.

## Final validation

**1,104 relevant tests passed; zero failures/errors/skips**, including four Phase 2 diagnostics and 26 new regression cases. Warnings were treated as errors. Mypy for the new parser, syntax compilation and `git diff --check` passed. All 301 source/checksum entries passed, and the two October 1 archives remain byte-for-byte unchanged. No unrelated full-backend rerun was needed or claimed. Results: `validation/own_exchange_direct_validation.json`.
