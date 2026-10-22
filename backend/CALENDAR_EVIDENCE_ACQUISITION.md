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

# Browser/manual ingestion framework — 2026-10-02.7

[Usage guide](calendar_evidence_acquisition/EVIDENCE_ENGINE.md). Added acquisition tooling and an atomic review ledger; no new official observations were acquired. Browser route is implemented but not live-verified because Playwright is absent. Ingestion and SHA256 do not automatically verify or activate a calendar. Existing 28-combination evidence status and production fail-closed behavior are unchanged.

---

# Official EOD continuation — 2026-10-02.6

Read [CALENDAR_EXCHANGE_EVIDENCE_REPORT.md](CALENDAR_EXCHANGE_EVIDENCE_REPORT.md) for the current 28-combination official-only report. New evidence-only acquisition tooling preserves BSE expiry/maturity, NSE UDiFF actual-expiry fields and MCX mapped expiry. Three official discovery attempts were blocked; no actual report link was validated and no date-range download was started. All 28 remain UNVERIFIED. Production calendar NOT INSTALLED; AUTO/NEXT unchanged and FAIL-CLOSED. Earlier sections below are preserved acquisition history.

---

# Static archive continuation — 2026-10-02.5

**NOT READY — evidence missing.** Calendar building is now explicitly independent of broker authentication. The existing production resolver was already offline and remains unchanged. The new tooling lives exclusively under `calendar_evidence_acquisition/`; nothing automatically loads or installs its output into production.

## Acquired evidence

Seven unique public GETs, zero retries, zero duplicate requests, zero broker/API-token calls. Two static NSE files for the actual research start date 2026-08-03 were tried once: `https://archives.nseindia.com/content/fo/NSE_FO_contract_03082026.csv.gz` and `https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_20260803_F_0000.csv.zip`. Both failed with curl 92 / HTTP/2 INTERNAL_ERROR, without a response status/body. These failures do not establish missing exchange records. No BSE/MCX or authenticated provider endpoints were retried. NSE commodity files were not acquired: NSE commodity listings cannot prove the scoped MCX contracts, and no historical NSE commodity consumer was established.

Three successful GitHub discovery responses identified two public repository bundles. Both bundles were cached and hashed once; no code from them was executed or installed. They are **SECONDARY_DATASET**, not official archives. An official-looking CSV header or a README attribution does not authenticate an exchange original.

| Dataset | Exact observation coverage | Extraction | Limitation |
|---|---|---|---|
| `shayakbanerjee99/nifty-options-elt`, `src/tests/test_data/test_bhavcopy_20260813.csv` | 2026-08-13 only | 1,717 NIFTY rows; 18 expiry candidates | Repository test fixture; editing/synthesis cannot be excluded. No listing/publication/cutoff proof. |
| `thyAnupam/Nifty-Options-Dataset`, `OptionsPrices2.csv` | 2017-01-02..2020-01-29, sparse explicit rows | 634 accepted rows; 35 expiry candidates; 17 quarantined rows | NIFTY identity declared by README, not CSV. Numeric dates such as 42857/42880 have no documented epoch and were not converted. |

The first file also contains NSE stock contracts whose ticker is `MCX`; those are **not MCX-exchange commodity contracts**. Exchange, underlying, contract type and date remain distinct. All raw rows, including invalid ones, remain in original retained bytes.

Six August-file expiry candidates match the retained Upstox expiry set. This is corroboration of candidate dates only: the local research expiry labels already derive from that same Upstox source and are **not another independent vote**. The second dataset is outside current 2026 consumer windows and does not extend required coverage. No weekly/monthly rule, holiday shift, launch/discontinuation or transition effective date was inferred. Gaps remain gaps. No independently verified continuous historical range was proved.

Exact URLs, retrieval timestamps, HTTP errors and SHA256 are in `source_register.json`, `static_sources_20261002/`, and the URL-addressed `cache/`. Each cache entry has metadata and raw bytes; parsed CSV sources additionally have retained member bytes and a hashed inventory. Eight earlier failed URL attempts were imported to prevent future retry. Existing Upstox metadata, the local rolling audit inventory and AngelOne master inventory were reused without network requests.

## Offline implementation and boundaries

`static_archives.py` provides:

- An append-only URL cache with SHA256 verification, explicit source identity, failure retention and refusal to retry incomplete/corrupt attempts. There is no built-in network transport and no credential loading.
- Strict CSV/GZIP/ZIP parsing of explicit legacy FO, UDiFF FO and named contract-master layouts. Unsupported layouts/epoch formats fail closed. ZIP members are read in memory, never extracted to supplied paths. Official format tests are **synthetic format fixtures**, not acquired official evidence.
- Explicit quarantine with row numbers/reasons when requested; strict rejection is the default. Source bytes and rejected data are preserved.
- Offline exact-date exchange observation lookup. It returns source-labelled candidates, rejects provider-specific requests, and rejects intraday as-of timestamps because EOD publication time is unproved. Different source sets remain traceable; subsets do not imply a conflict.
- Candidate-only previous-trading-day adjustment using an explicit effective interval, rule reference and complete supplied session evidence. Missing dates are not presumed weekends. Outputs remain `DERIVED_RULE` / `UNVERIFIED`. No real dates were generated with this helper because supporting official rule evidence is missing.

The existing `app/hist_options/provider_calendar.py` continues to handle reviewed provider calendars offline. It is not redesigned and is not wired to these candidates. Explicit dates retain their prior contract; AUTO/NEXT remains fail-closed without all required provider evidence. Runtime lookup makes no broker calls. Neither exchange observations nor mathematical rules establish historical provider listing coverage.

## Remaining coverage requirements

All 28 provider/instrument combinations remain UNVERIFIED. For each pair, the complete requested historical provider calendar interval is still missing. The per-combination `static_archive_evidence` records in manifest.json separate consumer demand, observed candidates, source class, acquisition state, confidence and missing ranges.

| Instrument | Required consumer interval for each provider, not verified coverage |
|---|---|
| NSE NIFTY | 2026-07-01..2026-10-01 |
| NSE BANKNIFTY | 2026-07-24..2026-10-01 |
| NSE FINNIFTY | 2026-09-04..2026-10-02 |
| NSE MIDCPNIFTY | 2026-09-04..2026-10-02 |
| BSE SENSEX | 2026-09-03..2026-10-02 |
| BSE BANKEX | 2026-09-04..2026-10-02 |
| MCX NATURALGAS | 2026-07-13..2026-10-02 |
| MCX NATGASMINI | Not established; identify historical consumer and contract type first |
| MCX CRUDEOIL | 2026-07-13..2026-10-01 |
| MCX CRUDEOILMINI | Not established; identify historical consumer and contract type first |
| MCX GOLD | Not established; identify historical consumer and contract type first |
| MCX GOLDMINI | Not established; identify historical consumer and contract type first |
| MCX SILVER | Not established; identify historical consumer and contract type first |
| MCX SILVERMINI | Not established; identify historical consumer and contract type first |

Next acquisition: obtain dated static exchange masters/bhavcopies and dated cutoff/holiday/rule-change publications for the required intervals through an accessible archive or legally retained copy. Review original provenance and exact historical applicability. Provider-listing verification remains a separate evidence track; broker tokens are not required to build the exchange observation calendar. No production calendar has been installed.

Focused-suite results are retained in validation.json. No unrelated full-suite run is necessary. Earlier report sections below are historical snapshots of previous passes.

---

# Alternative-source evidence pass — 2026-10-02 (package 2026-10-02.4)

Deployment: **NOT READY — evidence missing**. Calendar statuses remain **0 VERIFIED, 0 PARTIALLY_VERIFIED, 28 UNVERIFIED, 0 CONFLICTING, 0 NOT_APPLICABLE**. No production calendar/version was installed; AUTO/NEXT and provider fallback are unchanged. Earlier findings below remain historical records of their respective passes.

## New acquisitions and local analysis

Three unique public GETs, two successful downloads, one transport failure, zero retries or duplicate requests. Four cached source acquisitions were avoided (Dhan expired-options docs, expiry-list docs, F&O calendar and the retained AngelOne master). The redundant compact Dhan CSV was not downloaded because the documented detailed CSV supplies the required fields. Previously blocked exchange endpoints were not retried.

| Source | Acquisition | What it proves / limitations |
|---|---|---|
| [Dhan detailed master](https://images.dhan.co/api-data/api-scrip-master-detailed.csv) | HTTP 200; 34,396,652 bytes; SHA256 `e639b46af3ccd6d525180fd669b50c62f2be66c21111a3181fe3afe6a83d8300` | 202,243 current records; 270 target expiry/type/side/flag groups. Every selected expiry is after retrieval. No historic listing/publication times, cutoff or complete historical universe. |
| [Dhan instrument documentation](https://dhanhq.co/docs/v2/instruments/) | HTTP 200; SHA256 `36a9b707a9570e2868a2177488d35fc596536ef162c8d651cfd47d8fe1684d8f` | Defines expiry date, monthly/weekly flag, underlying and exchange columns; identifies both bulk URLs. No historical archive contract. |
| [NSE historical contract-wise report](https://www.nseindia.com/report-detail/fo_eq_security) | curl 92, HTTP/2 INTERNAL_ERROR; no HTTP response status/body | Distinct report entrypoint attempted once. Transport failure does not establish that historical evidence does not exist. |

Exact retrieval timestamps, raw bytes, hashes and errors are retained in `alternative_sources_20261002/` and `source_register.json`. These are public document/file requests, not live broker API calls. Current Dhan master and Dhan documentation are one provider source family, not two independent attestations.

The immutable, temporary copy of `hist_options.db` yielded 8,470 stored NIFTY bars over **2026-08-03T03:45Z..2026-08-17T10:09Z**, grouped by source, exchange, underlying, expiry label, side and timeframe. Three stored expiry labels are 2026-08-04/11/18. All 22 expiry-audit records cite **Upstox expired-instrument metadata** with `BROKER_METADATA_PATTERN`. The local `VERIFIED` label belongs to the older resolver, not Phase 3 evidence verification. These dates are therefore **not independently observed Dhan expiry dates**. Their Tuesday/seven-day pattern does not authorize filling any missing dates or backdating a rule. A compressed identity/provenance projection and audit records are retained under `raw/`; the original DB and snapshots are untouched.

The existing AngelOne master snapshot was analyzed from cache, not reacquired. It has no historical listing/publication fields. Current MCX option expiries, futures expiries and similarly named NSE commodity contracts remain separate. Provider spellings CRUDEOILM/GOLDM/SILVERM are retained; dated lifecycle mappings are still required before historical canonicalization.

## Coverage and exact acquisition blockers

Consumer request windows below are demand, **not proven calendar coverage**. Historical verified coverage remains empty for every row. A null range means the repository has not established the relevant historical consumer/type; it must not inherit the main product's range. NEXT requires all eligible maturities at each historical T, potentially beyond the final query date. Market-bar timestamps do not establish original publication/listing timestamps.

For every row, missing exchange evidence is: dated complete contract universe; rule-change/launch/withdrawal/holiday amendments; last-trading date/time/timezone; official segment sessions; independent review. Missing provider evidence is: dated historical provider master/listing universe with IDs, original publication and listing/withdrawal times, completeness and review. Current API examples are insufficient. For MCX keep options, futures, tender and settlement dates distinct.

| Provider | Exchange | Instrument | Required consumer dates | Best exchange source | Provider source/access | Further acquisition |
|---|---|---|---|---|---|---|
| DHAN | NSE | NIFTY | 2026-07-01..2026-10-01 | FO historical contract reports/masters + dated circulars | Dhan archive export; current CSV public, historical archive not found | Yes; authorized archive export if not public |
| ANGELONE | NSE | NIFTY | 2026-07-01..2026-10-01 | FO historical contract reports/masters + dated circulars | AngelOne archived daily masters; local current snapshot only | Yes; authorized archive export if not public |
| DHAN | NSE | BANKNIFTY | 2026-07-24..2026-10-01 | FO historical contract reports/masters + dated circulars | Dhan archive export; current CSV public, historical archive not found | Yes; authorized archive export if not public |
| ANGELONE | NSE | BANKNIFTY | 2026-07-24..2026-10-01 | FO historical contract reports/masters + dated circulars | AngelOne archived daily masters; local current snapshot only | Yes; authorized archive export if not public |
| DHAN | NSE | FINNIFTY | 2026-09-04..2026-10-02 | FO historical contract reports/masters + dated circulars | Dhan archive export; current CSV public, historical archive not found | Yes; authorized archive export if not public |
| ANGELONE | NSE | FINNIFTY | 2026-09-04..2026-10-02 | FO historical contract reports/masters + dated circulars | AngelOne archived daily masters; local current snapshot only | Yes; authorized archive export if not public |
| DHAN | NSE | MIDCPNIFTY | 2026-09-04..2026-10-02 | FO historical contract reports/masters + dated circulars | Dhan archive export; current CSV public, historical archive not found | Yes; authorized archive export if not public |
| ANGELONE | NSE | MIDCPNIFTY | 2026-09-04..2026-10-02 | FO historical contract reports/masters + dated circulars | AngelOne archived daily masters; local current snapshot only | Yes; authorized archive export if not public |
| DHAN | BSE | SENSEX | 2026-09-03..2026-10-02 | Derivative contract archives + dated transition circulars | Dhan archive export; current CSV public, historical archive not found | Yes; authorized archive export if not public |
| ANGELONE | BSE | SENSEX | 2026-09-03..2026-10-02 | Derivative contract archives + dated transition circulars | AngelOne archived daily masters; local current snapshot only | Yes; authorized archive export if not public |
| DHAN | BSE | BANKEX | 2026-09-04..2026-10-02 | Derivative contract archives + dated transition circulars | Dhan archive export; current CSV public, historical archive not found | Yes; authorized archive export if not public |
| ANGELONE | BSE | BANKEX | 2026-09-04..2026-10-02 | Derivative contract archives + dated transition circulars | AngelOne archived daily masters; local current snapshot only | Yes; authorized archive export if not public |
| DHAN | MCX | NATURALGAS | 2026-07-13..2026-10-02 | Contract Available For Trading HISTORY + dated specs/circulars | Dhan archive export; current CSV public, historical archive not found | Yes; authorized archive export if not public |
| ANGELONE | MCX | NATURALGAS | 2026-07-13..2026-10-02 | Contract Available For Trading HISTORY + dated specs/circulars | AngelOne archived daily masters; local current snapshot only | Yes; authorized archive export if not public |
| DHAN | MCX | NATGASMINI | NOT ESTABLISHED | Contract Available For Trading HISTORY + dated specs/circulars | Dhan archive export; current CSV public, historical archive not found | Yes; authorized archive export if not public |
| ANGELONE | MCX | NATGASMINI | NOT ESTABLISHED | Contract Available For Trading HISTORY + dated specs/circulars | AngelOne archived daily masters; local current snapshot only | Yes; authorized archive export if not public |
| DHAN | MCX | CRUDEOIL | 2026-07-13..2026-10-01 | Contract Available For Trading HISTORY + dated specs/circulars | Dhan archive export; current CSV public, historical archive not found | Yes; authorized archive export if not public |
| ANGELONE | MCX | CRUDEOIL | 2026-07-13..2026-10-01 | Contract Available For Trading HISTORY + dated specs/circulars | AngelOne archived daily masters; local current snapshot only | Yes; authorized archive export if not public |
| DHAN | MCX | CRUDEOILMINI | NOT ESTABLISHED | Contract Available For Trading HISTORY + dated specs/circulars | Dhan archive export; current CSV public, historical archive not found | Yes; authorized archive export if not public |
| ANGELONE | MCX | CRUDEOILMINI | NOT ESTABLISHED | Contract Available For Trading HISTORY + dated specs/circulars | AngelOne archived daily masters; local current snapshot only | Yes; authorized archive export if not public |
| DHAN | MCX | GOLD | NOT ESTABLISHED | Contract Available For Trading HISTORY + dated specs/circulars | Dhan archive export; current CSV public, historical archive not found | Yes; authorized archive export if not public |
| ANGELONE | MCX | GOLD | NOT ESTABLISHED | Contract Available For Trading HISTORY + dated specs/circulars | AngelOne archived daily masters; local current snapshot only | Yes; authorized archive export if not public |
| DHAN | MCX | GOLDMINI | NOT ESTABLISHED | Contract Available For Trading HISTORY + dated specs/circulars | Dhan archive export; current CSV public, historical archive not found | Yes; authorized archive export if not public |
| ANGELONE | MCX | GOLDMINI | NOT ESTABLISHED | Contract Available For Trading HISTORY + dated specs/circulars | AngelOne archived daily masters; local current snapshot only | Yes; authorized archive export if not public |
| DHAN | MCX | SILVER | NOT ESTABLISHED | Contract Available For Trading HISTORY + dated specs/circulars | Dhan archive export; current CSV public, historical archive not found | Yes; authorized archive export if not public |
| ANGELONE | MCX | SILVER | NOT ESTABLISHED | Contract Available For Trading HISTORY + dated specs/circulars | AngelOne archived daily masters; local current snapshot only | Yes; authorized archive export if not public |
| DHAN | MCX | SILVERMINI | NOT ESTABLISHED | Contract Available For Trading HISTORY + dated specs/circulars | Dhan archive export; current CSV public, historical archive not found | Yes; authorized archive export if not public |
| ANGELONE | MCX | SILVERMINI | NOT ESTABLISHED | Contract Available For Trading HISTORY + dated specs/circulars | AngelOne archived daily masters; local current snapshot only | Yes; authorized archive export if not public |

Exchange publication routes are public but inaccessible here: NSE HTTP/2 transport failures; BSE archive API HTTP 403 and UI shell only; MCX HISTORY/root HTTP 403. No repeated blocked requests in this pass. Obtain dated official files through an accessible official archive or a legally retained original-document copy, with original URL/document ID/publication/effective dates/hash and independent review. A broker or secondary mirror may carry an exchange document but is not a second independent source.

For Dhan, the public rolling-options documentation establishes up to five years of index/stock price-data capability, not a complete listing calendar. The displayed response has no expiry/publication/listing fields. Authorized rolling-data access alone cannot close the calendar gap. MCX historical support is not established by that documentation. For AngelOne, daily-master policy does not establish an expired-master archive; seek retained snapshots or an authorized historical export. No credentials were read/requested.

No official dated rule-change document was acquired, so no new historical rule or holiday shift was asserted. The three locally derived dates and the current master do not conflict merely because they cover different times; absence is not a discontinuation claim.

Validation for this pass is recorded in `calendar_evidence_acquisition/validation.json`. Checksums cover the preserved raw bytes and generated inventories. Application code and existing tests are unchanged; evidence-only regression tests validate the package separately.

---

# Official-source acquisition pass — 2026-10-02

**NOT READY — evidence missing. No production calendar installed.**

Current plan version: **2026-10-02.3** (`calendar-evidence-acquisition/v2`).
The prior v2 manifest is retained as `manifest_20261002_2_snapshot.json`; the
previous report follows this update. No calendar engine, production behavior,
test, historical snapshot or existing research result was modified.

## Outcome and evidence levels

| Provider/instrument status | Count |
| --- | ---: |
| VERIFIED | 0 |
| PARTIALLY_VERIFIED | 0 |
| UNVERIFIED | 28 |
| CONFLICTING | 0 |
| NOT_APPLICABLE | 0 |

**Newly verified combinations: none. Historical coverage newly proved: none.**
Current API descriptions, a current calendar page and a retained local master
are useful acquisition evidence, but do not partially verify historical calendar
facts for a provider/instrument period. No conflicting dated primary calendar
sources were acquired, so CONFLICTING is not used to mean missing evidence.
Exchange-calendar evidence and provider-listing evidence remain **separate
UNVERIFIED claims** in every combination's `evidence_record.provenance`.

All 28 matrix rows now contain the requested evidence model: provider, exchange,
instrument/contract type, status, per-instrument demand interval, source URLs/hashes,
document/publication/effective fields, expiry/holiday/cutoff rules, provenance and
limitations. Unproved fields remain null. `historical_start/end` are **requested
consumer coverage**, not an assertion that the sources prove those dates.

## Sources actually acquired

23 distinct initial URLs were requested, once each; curl followed public redirects
where recorded. Three requests at most were concurrent, each bounded to 20 seconds
and 10 MB. No authenticated or live broker data API, order endpoint, credentials,
CAPTCHA solution, service restart or migration was used. Failed exchange domains
were not repeatedly retried. No per-expiry requests or date sweep was performed.

The source register records every exact URL, retrieval timestamp, original body
path, SHA-256, HTTP/curl result, evidence tier, what it proves and what it does
not prove. HTTP 200 does not imply a useful document. Public JavaScript bundles
were inspected as text only; no remote script was executed.

| Retained source | What it establishes | Historical limit |
| --- | --- | --- |
| [Dhan expired-options documentation](https://dhanhq.co/docs/v2/expired-options-data/) | Official `/charts/rollingoption` interface: documented up to five years; up to 30 days per call; index/stock options; WEEK/MONTH and expiryCode; OHLC/IV/OI/spot/strike/timestamp arrays; exclusive toDate | No complete historical listing universe, original publication times, last-trading cutoff or expiry field in the displayed response. Examples are not observed contracts. MCX support is not established by this document. |
| [Dhan F&O expiry calendar](https://dhan.co/fno-expiry-calendar/) | Current provider page with index weekly/monthly/quarterly/half-yearly and commodity schedule descriptions and holiday note | No historical publication/version interval, transition treatment or complete provider listing history. Not used to generate past dates. HTML rowspans retained; neighboring labels alone must not be misread as rules. |
| [AngelOne SmartAPI documentation](https://smartapi.angelone.in/docs) and its linked public bundle | Says tradable-instrument file is updated daily and recommends daily downloads; historical candle interface uses exchange/token/interval/fromdate/todate | No historical expired-master archive was established. This is not proof that a private archive cannot exist. |
| Existing `backend/data/instrument_master.json` | Actual local master rows, public instrument IDs/types/expiry fields, checksum and per-product inventory | No original download/publication/listing timestamps. File mtime is not publication evidence. Current mini/metal rows do not establish historical listing or a historical consumer. |
| BSE site archive modules and configuration | Exact official archive search path, date/subject parameters and document/attachment routes | The actual archive query returned HTTP 403; routing code is not a circular. |

The local AngelOne master is retained losslessly as
`angelone_master_snapshot.json.gz`, with hashes of both original bytes and compressed
bytes in `angelone_local_master_inventory.json`. The original file was not edited.
This is a **current local data snapshot**, not a production calendar or a new broker
master download. Its attribution follows the repository loader, not an independent
signed download attestation.

The Dhan rolling endpoint remains **not called**. Any later authenticated acquisition
would require separately authorized access and would use its documented batching.
No credentials were requested. Even an actual rolling-price response would not,
by itself, replace the provider calendar/listing evidence required by AUTO/NEXT.

## Network and discovery results

| Target | Result | Disposition |
| --- | --- | --- |
| NSE official contract specification page | curl 92 HTTP/2 INTERNAL_ERROR, no HTTP status/body; same failure as earlier index access | ACCESS_BLOCKED; stop repeating this host |
| NSE archive contract-master candidate for observed 2026-07-01 start | curl 92 HTTP/2 INTERNAL_ERROR; no body | Candidate filename convention only, not a discovered official index entry. File existence/absence is unproved; no date brute force. |
| MCX Contract Available For Trading -> History entry point | HTTP 403 Access Denied, same as prior site access | ACCESS_BLOCKED; no rows extracted; no bypass attempted |
| BSE notices page | HTTP 200 site shell, identical bytes to prior homepage | INSUFFICIENT; traced its public archive route rather than claiming a calendar |
| BSE official archive search, expiry subject, 2025-01-01..2026-10-02 | HTTP 403 Access Denied | ACCESS_BLOCKED; exact request retained for an authorized accessible environment |
| Initial Dhan `/expiry-calendar/` candidate | HTTP 404 | NOT_FOUND at that URL; sitemap located working `/fno-expiry-calendar/` without retrying the failed URL |
| Google discovery | HTTP 429 challenge | Not solved or bypassed; no source evidence |
| DuckDuckGo discovery | HTTP 202 challenge | Not solved or bypassed; no source evidence |
| Bing discovery | HTTP 200 but unrelated Gemini/astrology results | Quarantined as irrelevant; no official URL or calendar claim taken from it |

No TLS failure was observed. Transport/access failure, irrelevant output and
missing historical archive discovery do **not** prove that the required evidence
is genuinely unavailable. The search pages are discovery artifacts, never Tier 1
or Tier 2 evidence. No secondary calendar was promoted to primary evidence.

## Historical scope preserved

Per-consumer/per-instrument ranges were rechecked against retained query inventory,
research_369 and current caller signatures, rather than replaced by one universal
window. Eight products retain dated acquisition proposals; six retain unresolved
scope. Both providers need their own evidence for each proposed interval.

| Instrument | Initial requested consumer dates (IST, inclusive) | Basis |
| --- | --- | --- |
| NIFTY | 2026-07-01..2026-10-01 | Observed option/replay context, not proved listing availability |
| BANKNIFTY | 2026-07-24..2026-10-01 | Observed option/replay context, not proved listing availability |
| FINNIFTY | 2026-09-04..2026-10-02 | Observed option/replay context, not proved listing availability |
| MIDCPNIFTY | 2026-09-04..2026-10-02 | Observed option/replay context, not proved listing availability |
| SENSEX | 2026-09-03..2026-10-02 | Observed option/replay context, not proved listing availability |
| BANKEX | 2026-09-04..2026-10-02 | Observed option/replay context, not proved listing availability |
| NATURALGAS | 2026-07-13..2026-10-02 | Observed option/replay context, not proved listing availability |
| NATGASMINI | UNRESOLVED | Historical consumer/type scope not established |
| CRUDEOIL | 2026-07-13..2026-10-01 | Observed option/replay context, not proved listing availability |
| CRUDEOILMINI | UNRESOLVED | Historical consumer/type scope not established |
| GOLD | UNRESOLVED | Historical consumer/type scope not established |
| GOLDMINI | UNRESOLVED | Historical consumer/type scope not established |
| SILVER | UNRESOLVED | Historical consumer/type scope not established |
| SILVERMINI | UNRESOLVED | Historical consumer/type scope not established |

The union 2026-07-01..2026-10-02 remains an initial planning envelope only.
research_369's actual observations are 2026-08-03..2026-08-17; its stored expiry
labels do not extend that observation window. Spot history from 2006, metadata
expiry lists from 2024 and documentation/test example dates do not create automatic
provider-calendar requirements for those years. Arbitrary historical API requests
outside reviewed periods remain unavailable. Prior database snapshot/WAL/query
limitations remain disclosed; no live DB scan or mutation was repeated in this pass.

## Rule changes, holiday adjustments and MCX semantics

No dated official rule-change circular was obtained, so **no effective-dated rule
was asserted or activated**. The existing seven code-pattern candidates remain
UNVERIFIED. A new record may be populated only with source/document number,
publication date, effective date, old/new rule, affected instruments and contract
types, transition treatment and original-source hash. Current Tuesday/Thursday
statements are not backdated. No holiday adjustment was calculated without the
applicable official adjustment rule and dated exchange holiday evidence.

The MCX acquisition request explicitly requires original history/export columns:
instrument type, symbol, commodity, product month, option type, strike, start/end
dates and tender/staggered start/end. Contract availability, option expiry,
underlying-future expiry, delivery/tender period and settlement remain distinct.
Also obtain the original launch calendar, option commencement notices, contract
spec amendments and trading-hour/cutoff changes. No actual MCX history rows were
retrieved, and no main calendar was substituted for a mini calendar.

## Remaining gaps and exact next action

Use an authorized environment that can access the official archives, or obtain
original exchange-issued downloads from the owner:

1. **MCX first:** export Contract Available For Trading -> History for the gas/crude
   requested periods above, retaining original field names, dates and product/type
   identities. Obtain associated launch/commencement/cutoff/holiday circulars.
   Establish the mini/metal historical consumer scope before selecting their periods.
2. **NSE/BSE:** obtain dated complete contract masters and expiry/rule-change/holiday
   notices covering the per-instrument intervals, including original publications and
   listings before each start and eligible NEXT maturities beyond each end. The exact
   BSE archive request and unresolved NSE archive candidate are in the source register.
3. **DHAN/ANGELONE independently:** obtain original dated complete listing/master
   archives, underlying/security/token mapping and listing/delisting/publication
   evidence. Rolling prices, current calendars and current masters are insufficient.
4. Have an independent reviewer reconcile every normalized entry, effective interval,
   completeness claim and cutoff against original bytes and superseding notices;
   record review identity/date/reference and immutable hashes/version. Only then
   assess VERIFIED for the actual historical consumer scope. Activation remains a
   separate review step, not an automatic consequence of acquiring documents.

## Validation and files

Only acquisition artifacts were added/updated: this report, manifest,
coverage_inventory.json, source_register.json, original response bodies/retrieval
metadata, retained local master snapshot/inventory, prior-manifest snapshot,
validation.json and SHA256SUMS. No calendar code or existing tests were changed.

| Current run | Passed | Failed | Errors | Skipped |
| --- | ---: | ---: | ---: | ---: |
| Focused relevant calendar/evidence suite | 639 | 0 | 0 | 0 |
| Saved Phase 2 diagnostics | 4 | 0 | 0 | 0 |

**643 distinct passes total** (639 + 4), not 643 plus another 4. No warnings with
`-W error`; no tests added, weakened, removed or skipped. The existing isolated
runner blocked test network/.env/subprocess access and restricted SQLite to temp
locations. The unrelated full suite was not run.

Validated hashes of all 21 retained response bodies and the compressed/decompressed
local master. SHA256SUMS covers the current package and report. `git diff --check`
passed; `git status --short` inspected. All 596 pre-existing inspected app/test files
match their pre-task hashes. Existing snapshots, prior evidence files and user/Claude
changes remain preserved. No production calendar, broker API call/order, migration
or service restart occurred. Deployment remains **NOT READY — evidence missing**.

---

## Previous coverage/acquisition report — preserved planning history

# Historical coverage and evidence acquisition plan — 2026-10-02

**NOT READY — evidence missing. No production calendar installed.**

## Proposed required coverage

**Initial observed option/replay acquisition scope: START = 2026-07-01;
END = 2026-10-02, inclusive, Asia/Kolkata.** Equivalent interval:
`[2026-07-01T00:00:00+05:30, 2026-10-03T00:00:00+05:30)`.
This is a concrete proposal derived from stored observations and identified callers,
not a universal API promise or proof of provider listing availability.

- Start: `market_history.db::market_candles`, NIFTY OPTION / DHAN_HISTORICAL,
  daily bar `2026-06-30T18:30:00+00:00` = **July 1 IST**. Its minute bars start
  `2026-07-01T03:51:00+00:00`. These are actual stored time labels, not generated
  expiries; their contract/listing validity still requires evidence.
- End: same-session option captures through **October 2**, including FINNIFTY,
  MIDCPNIFTY/BANKEX at `2026-10-02T10:27:41.170635Z` in the bounded scan.
  A capture date is not proof of an open trading day, a fresh quote or valid listing.
- `research_369`: **August 3–17, 2026**, exactly
  `2026-08-03T03:45:00+00:00` to `2026-08-17T10:09:00+00:00`; 8,470
  `DHAN_ROLLING` NIFTY option rows and 4,235 event timestamps. Observed expiry
  labels are August 4/11/18; August 18 is **not** the end of observation coverage.
  v7 reads the stored expiry column; v8 reads saved v7 events. No AUTO calendar
  discovery is currently called by these scripts. Existing results are preserved.
- The oldest market datum found is **2006-01-02 IST** (NIFTY INDEX daily;
  UTC `2006-01-01T18:30:00+00:00`). This spot-history range is recorded separately
  and does not justify requiring option calendars back to 2006.

Historical API `as_of` and explicit research sessions are caller-controlled and
have no finite configured universal range. The proposed interval covers observed
initial option/replay demand only. Earlier/later requests must continue to fail
closed unless their own period has reviewed evidence. Six registered products
have no demonstrated calendar consumer with proved type/date scope; their twelve
provider rows retain null required dates, rather than copying another product's range.

## Current manifest and inventory

The current human-reviewable plan is **`calendar_evidence_acquisition/manifest.json`**,
package `2026-10-02.2`, acquisition schema v2. It includes all 28 combinations,
per-row required date bounds/reasons, exact evidence obligations, primary and
secondary sources, access classification, acquisition and independent verification
methods, and null final calendar hash/version fields.

`calendar_evidence_acquisition/coverage_inventory.json` retains the actual bounded
SQL range results, market-data edge samples, query errors/limitations, legacy
source ranges, six research CSV ranges, Kaggle inventory and 233 files' date-literal
leads. It contains no order/account/credential data. Its hash in the manifest binds
the inventory bytes; this is **not** a verified calendar hash.

The previous `validation/calendar_acquisition/` package, source bytes, checksums and
nine tests are preserved unchanged as the prior acquisition snapshot. The prior
report is retained below this update. The v2 plan supersedes its unresolved-range
planning statement only; it does not promote any old source to VERIFIED.

## Instrument/provider plan

Each row below represents **two independent evidence requests**, DHAN and ANGELONE.
Identical requested windows reflect the same consumer and the existing default/
fallback requirement, not interchangeable calendars or a claim that both providers
listed the product throughout. Individual observed source ranges remain in JSON.
All 28 statuses remain **UNVERIFIED**.

| Exchange | Derivative | Required type | Proposed start | Proposed end | Qualification |
| --- | --- | --- | --- | --- | --- |
| NSE | NIFTY | OPTION | 2026-07-01 | 2026-10-01 | Observed option context; own-provider archive required |
| NSE | BANKNIFTY | OPTION | 2026-07-24 | 2026-10-01 | Observed option context; own-provider archive required |
| NSE | FINNIFTY | OPTION | 2026-09-04 | 2026-10-02 | Observed option context; own-provider archive required |
| NSE | MIDCPNIFTY | OPTION | 2026-09-04 | 2026-10-02 | Observed option context; own-provider archive required |
| BSE | SENSEX | OPTION | 2026-09-03 | 2026-10-02 | Observed option context; own-provider archive required |
| BSE | BANKEX | OPTION | 2026-09-04 | 2026-10-02 | Observed option context; own-provider archive required |
| MCX | NATURALGAS | OPTION | 2026-07-13 | 2026-10-02 | July legacy provider/expiry unknown; September+ provider captures |
| MCX | NATGASMINI | OPTION | Unresolved | Unresolved | Consumer/type scope not established; do not infer dates |
| MCX | CRUDEOIL | OPTION | 2026-07-13 | 2026-10-01 | July legacy provider/expiry unknown; September+ provider captures |
| MCX | CRUDEOILMINI | OPTION | Unresolved | Unresolved | Consumer/type scope not established; do not infer dates |
| MCX | GOLD | FUTURE | Unresolved | Unresolved | Consumer/type scope not established; do not infer dates |
| MCX | GOLDMINI | FUTURE | Unresolved | Unresolved | Consumer/type scope not established; do not infer dates |
| MCX | SILVER | FUTURE | Unresolved | Unresolved | Consumer/type scope not established; do not infer dates |
| MCX | SILVERMINI | FUTURE | Unresolved | Unresolved | Consumer/type scope not established; do not infer dates |

NATGAS maps to NATURALGAS; CRUDEOILM maps to CRUDEOILMINI in the current registry.
Legacy cycles contain minis and GOLDM/SILVERM labels, but neither provider/expiry
columns nor an integrated calendar consumer proves their historical identity.
GOLD/GOLDMINI/SILVER/SILVERMINI remain FUTURE registry identities, not proved option
feeds. Do not promote legacy aliases, metal spot series or a main-contract calendar.

## Conflicting or differently scoped ranges

| Range | Evidence / reason | Effect on proposed calendar scope |
| --- | --- | --- |
| 2006-01-02–2026-09-25 IST | Dhan NIFTY INDEX daily bars | Spot only; excluded from option date requirement |
| 2008-01-01–2026-05-18, varying by dataset | Kaggle spot inventory; NIFTY 1m starts 2008, BANKNIFTY starts 2015, metals 2014, SENSEX 2021 | Each dataset's exact range retained; not provider-specific derivatives |
| 2021-01-01–2026-03-30 | Kaggle NIFTY FUTURE daily, explicit expiry labels | Separate third-provider futures research |
| 2021-09-29–2026-09-25 IST | Dhan daily FUTURE rows labelled with a single 2026 expiry | Continuous-vs-contract provenance needs separate review; not proof a 2026 contract existed in 2021 |
| 2017 onward price history; configured 2022-04-09–2026-09-28 | Orderflow-system NIFTY INDEX research uses volume-qualified start | No automatic option calendar obligation inferred |
| 2024-09-30–2026-09-29, varying by instrument | Upstox retrospective expiry metadata; separate Phase 1 rolling resolver/tests | Expiry labels, not historical Dhan/AngelOne publication/listing observations |
| 2026-07-13–2026-08-28 | External legacy `oi_history.db::cycles`; bridge declares NIFTY/gas/crude | Provider and expiry unknown; source clarification required |
| 2026-08-03–2026-08-17 | `research_369`, hist_options.db and six saved event CSVs agree | Concrete NIFTY option/replay context |
| September 2026 | Older option-premium research reports use captured subset; spot research extends to 2015 | Older reports are snapshots, not hard present-day DB bounds |
| 2023-02-23 / 2024-06-27 exchange dates, received September 2026 | FINNIFTY/BANKEX stale exchange timestamps | Cannot answer historical queries in 2023/2024; do not backdate scope/listing |
| 2000-01-01–2031-02-17 date literals | Retention sentinel and mocked future-expiry tests; other tests exercise missing/metadata boundaries | Test fixtures do not create production historical demand |
| Arbitrary caller dates | Historical API as-of and explicit backtest sessions | No finite universal range; unsupported periods remain unavailable |

Exact table/source/timeframe subranges are retained in the inventory rather than
flattened into one misleading provider/instrument period. The inventory's SQL
`expiry_min/max` results were renamed `sql_lexical_expiry_min/max`: DDMMMYYYY string
sorting is not chronological expiry sorting and is never used to choose dates.

## Caller implications

The manifest records callers and their contracts; the broader Phase 3 wrapper
inventory remains in `PROVIDER_CALENDAR_CONTRACT.md`.

- Market-hub / captured Dhan and AngelOne chain readers / option-chain API:
  aliases need their own provider evidence at the caller's historical as-of.
- Session option queries additionally need an as-of in the requested IST session.
  Several legacy research callers omit it; installing evidence alone would not
  make those historical requests valid. No caller semantics were changed here.
- `orderflow/backtest` uses explicit sessions or up to 400 captured sessions,
  pins the selected bar provider for premium context, and preserves explicit expiry.
- Historical analytics groups or explicitly selects expiry; the Greek engine
  separately consumes attributed AngelOne captures. Neither proves an AUTO universe.
- Independent research helpers select nearest captured expiry and sometimes symmetric
  premium time with fixed cutoff. They remain unverified research, requiring a
  separate contract/provenance review before promotion. No saved result was rerun.

Additional derived datasets: `expiry_z2h.db` contains 23 SENSEX session windows
from 2026-09-03 to 2026-10-02; `premium_decoupling.db` observations span
2026-09-24 to 2026-10-02 (per-symbol bounds retained); `research_events.db`
has no event rows. Expiry-zero-to-hero callers take explicit expiry/session
parameters. These do not establish a complete historical calendar. Structural,
edge and monitor database schemas contain evaluations/signals/settings rather
than expiry calendar tables; settings values were not read.

## Network/source diagnostics

No new external request was made in this task. The original retained access results
were classified without repeatedly retrying blocked endpoints.

| Source | Exact observed result | Classification / next method |
| --- | --- | --- |
| NSE circular index | Sandbox DNS resolution failure (curl 6); external HTTP/2 INTERNAL_ERROR (curl 92); HTTP/1.1 timeout (curl 28), zero bytes | ACCESS_BLOCKED. Obtain official dated circular/annexure files through an accessible archive or owner-supplied original download. No observed TLS or HTTP-status failure. |
| MCX official site | HTTP 403, explicit Access Denied | ACCESS_BLOCKED. Obtain original dated product/expiry/holiday notices through an authorized accessible source. |
| BSE official site | HTTP 200, site shell, no historical document | INSUFFICIENT. Locate exact historical contract/circular files; a homepage is not expiry evidence. |
| Dhan v2 option-chain documentation | HTTP 200; expiry endpoint describes active instruments | INSUFFICIENT. Obtain original historical complete-list/master versions; documentation is not historical calendar data. |
| Dhan/AngelOne historical listing archive | No historical endpoint/archive located in repository | NOT_FOUND. Request dated provider archive/export; not proof the provider lacks one. |
| Current masters / Upstox expired lists / observed trading days / NSE 2026 holiday JSON | Existing locators/local evidence with incomplete period, provider, publication, listing or cutoff dimensions | INSUFFICIENT for historical provider calendar verification; retained as leads/corroboration only. |

AVAILABLE means the required source is obtainable, not automatically calendar-verified.
NEEDS_INDEPENDENT_REVIEW requires original evidence plus a pending independent
review. No source was assigned that status merely because a generic webpage was
retrievable. TLS failure and genuinely unavailable historical evidence were **not**
established. Missing historical endpoint discovery is distinct from archive absence.

## Exact evidence blockers before VERIFIED

1. Complete, original, dated DHAN and ANGELONE listed-contract universes for each
   proposed interval; security/token and exchange/type mappings; historical listing
   and delisting/publication timestamps. Request each provider independently.
2. Official exchange contract/expiry files, weekly/monthly and special maturities,
   launch/withdrawal/rename/restructure notices and every relevant rule-change annexure.
3. Official segment-specific trading calendars and holiday/emergency amendments;
   actual last-trading date/time/timezone for each contract. Do not calculate missing
   dates from current rules or infer an option cutoff from futures expiry.
4. Original publication/listing evidence before the proposed start and all maturities
   eligible at each T, including NEXT beyond the proposed end. The observation-window
   END does not truncate the required maturity universe.
5. Independent reviewer identity/date/reference and field-by-field reconciliation,
   including completeness, effective periods, conflicts and source/normalized hashes.
   Final calendar version/hash fields remain null until a reviewed normalization exists.
6. Historical consumer/type scope for minis and registry metals; provenance for legacy
   cycles; consistent database snapshot to establish full bounds beyond partial scans.
7. API scope decision if coverage beyond this initial observed window is required.
   No finite window can satisfy arbitrary historical requests without period-specific evidence.

No new official rule-change period was verified. Prior code-pattern transition
candidates remain UNVERIFIED; no historical expiry date was manufactured.

## Database inspection limits and safety

Database inspection used `mode=ro&immutable=1` and `PRAGMA query_only=ON`, without
application imports. Existing database bytes were not edited; no migration,
checkpoint, repair, service restart or order operation was performed. Market-data
queries only; credentials and order/account tables were not read.

Market-history and L2 databases had WAL content; immutable main-file reads exclude
it. Some main files changed during inspection, so the samples are not a consistent
live snapshot. Two full range queries hit the 30-second read budget; two bounded
NIFTY Dhan capture edge queries hit their 3-second budgets. Successful indexed samples
are retained, and failures are explicit. A `chanakya.db` read reported **database disk
image is malformed** while that main file changed. This is an inspection blocker,
not a proven persistent-corruption diagnosis; no repair was attempted. A consistent
owner-provided snapshot is needed for definitive full-range/integrity conclusions.

## Validation for this update

| Relevant run | Passed | Failed | Errors | Skipped |
| --- | ---: | ---: | ---: | ---: |
| Evidence tests (prior 9 + new 7), focused | 16 | 0 | 0 | 0 |
| Calendar/relevant 623 + evidence 16, combined | 639 | 0 | 0 | 0 |
| Saved Phase 2 diagnostics | 4 | 0 | 0 | 0 |

**643 distinct passing cases**; repeated focused runs are not counted twice.
No warnings reported with `-W error`. Tests used the existing isolated runner
with network/.env/subprocess guards and temporary SQLite. The unrelated full
repository suite was not rerun. Validation details and exact relevant file list
are in `calendar_evidence_acquisition/validation.json`.

Seven new evidence-plan tests protect 28-row identity coverage, traceable range
bounds, unresolved mini/metal scope, IST midnight conversion, unverified status,
source references/hashes and accurate network-failure classification. All prior
calendar implementation and tests remain unchanged; no production calendar,
trading logic or provider fallback was changed.

`git diff --check` passed and `git status --short` was inspected. The changed
report is this file; additions are `calendar_evidence_acquisition/{manifest.json,
coverage_inventory.json,validation.json,SHA256SUMS}` and
`tests/test_calendar_coverage_plan.py`. Existing raw data, snapshots, earlier
evidence packages and user/Claude changes remain intact.

---

## Previous acquisition report — preserved snapshot, version 2026-10-02.1

# Historical provider calendar evidence acquisition — 2026-10-02

**NOT READY — evidence missing.** Acquisition package version `2026-10-02.1`,
schema `calendar-evidence-acquisition/v1`. This is a review inventory, **not** a
`provider-calendar/v1` manifest. No application loader reads it. No production
calendar is installed, and calendar implementation/behavior was not changed.

## Evidence outcome

| Calendar status | Combinations |
| --- | ---: |
| VERIFIED | 0 |
| PARTIALLY_VERIFIED | 0 |
| UNVERIFIED | 28 |
| CONFLICTING | 0 |
| NOT_APPLICABLE | 0 |

The 28 combinations cover 14 registered identities for each of DHAN and ANGELONE.
An accessible general API page does not partially verify an instrument's historical
calendar. No contradictory dated primary calendar documents were obtained, so
none is labelled CONFLICTING. Lack of listing evidence does not prove NOT_APPLICABLE.
There are zero independently reviewed historical calendars and zero production
calendar versions. No secondary source was promoted to primary evidence.

## Files and complete matrix

- `validation/calendar_acquisition/manifest.json`: canonical complete matrix, source
  inventory, status definitions, unverified rule-change candidates, and **156**
  provider/instrument-specific acquisition requests. Each request identifies source,
  required date scope, instrument, provider, reason and current status.
- `validation/calendar_acquisition/sources/`: retained unmodified public HTTP bodies.
  Dhan documentation is primary API-contract evidence; BSE site-shell and MCX access
  denial are accessibility artifacts, **not** historical calendar evidence.
- `validation/calendar_acquisition/SHA256SUMS`: package/source integrity; a hash does
  not establish authenticity or independent verification.
- `tests/test_calendar_evidence_acquisition.py`: nine cases protecting package
  coverage, referential integrity, source hashes, unresolved dates and rejection
  of non-VERIFIED review states. Synthetic evidence remains test-only.

Each JSON matrix row explicitly includes provider, exchange, canonical underlying,
contract type, historical range, exact-expiry requirements, trading-calendar needs,
publication/listing and cutoff evidence, source references, source date, calendar
version/hash and independent review disposition. Source references resolve through
`source_catalog`; missing facts are null with a reason, never invented dates.

**Required production as-of range is unspecified.** An owner clarification has
been requested. This is not defaulted to the range of the available Upstox data.
For each requested interval obtain all eligible maturities (including NEXT beyond
the interval end), original publication/listing evidence before its start, and
all amendments affecting it. Current downloads cannot be backdated to that interval.
The inventory spans below are *corroboration leads only*, not approved coverage.

| Provider | Exchange | Underlying | Required type | Corroborating expiry inventory | Status |
| --- | --- | --- | --- | --- | --- |
| DHAN | NSE | NIFTY | OPTION | Upstox 2024-10-03–2026-09-29 (105) | UNVERIFIED |
| ANGELONE | NSE | NIFTY | OPTION | Upstox 2024-10-03–2026-09-29 (105) | UNVERIFIED |
| DHAN | NSE | BANKNIFTY | OPTION | Upstox 2024-10-01–2026-09-29 (30) | UNVERIFIED |
| ANGELONE | NSE | BANKNIFTY | OPTION | Upstox 2024-10-01–2026-09-29 (30) | UNVERIFIED |
| DHAN | NSE | FINNIFTY | OPTION | Upstox 2024-10-01–2026-09-29 (31) | UNVERIFIED |
| ANGELONE | NSE | FINNIFTY | OPTION | Upstox 2024-10-01–2026-09-29 (31) | UNVERIFIED |
| DHAN | NSE | MIDCPNIFTY | OPTION | Upstox 2024-09-30–2026-09-29 (31) | UNVERIFIED |
| ANGELONE | NSE | MIDCPNIFTY | OPTION | Upstox 2024-09-30–2026-09-29 (31) | UNVERIFIED |
| DHAN | BSE | SENSEX | OPTION | Upstox 2024-10-04–2026-09-24 (103) | UNVERIFIED |
| ANGELONE | BSE | SENSEX | OPTION | Upstox 2024-10-04–2026-09-24 (103) | UNVERIFIED |
| DHAN | BSE | BANKEX | OPTION | Upstox 2024-09-30–2026-09-24 (31) | UNVERIFIED |
| ANGELONE | BSE | BANKEX | OPTION | Upstox 2024-09-30–2026-09-24 (31) | UNVERIFIED |
| DHAN | MCX | NATURALGAS | OPTION | None | UNVERIFIED |
| ANGELONE | MCX | NATURALGAS | OPTION | None | UNVERIFIED |
| DHAN | MCX | NATGASMINI | OPTION | None | UNVERIFIED |
| ANGELONE | MCX | NATGASMINI | OPTION | None | UNVERIFIED |
| DHAN | MCX | CRUDEOIL | OPTION | None | UNVERIFIED |
| ANGELONE | MCX | CRUDEOIL | OPTION | None | UNVERIFIED |
| DHAN | MCX | CRUDEOILMINI | OPTION | None | UNVERIFIED |
| ANGELONE | MCX | CRUDEOILMINI | OPTION | None | UNVERIFIED |
| DHAN | MCX | GOLD | FUTURE | None | UNVERIFIED |
| ANGELONE | MCX | GOLD | FUTURE | None | UNVERIFIED |
| DHAN | MCX | GOLDMINI | FUTURE | None | UNVERIFIED |
| ANGELONE | MCX | GOLDMINI | FUTURE | None | UNVERIFIED |
| DHAN | MCX | SILVER | FUTURE | None | UNVERIFIED |
| ANGELONE | MCX | SILVER | FUTURE | None | UNVERIFIED |
| DHAN | MCX | SILVERMINI | FUTURE | None | UNVERIFIED |
| ANGELONE | MCX | SILVERMINI | FUTURE | None | UNVERIFIED |

NSE: NIFTY (NIFTY 50 alias), BANKNIFTY, FINNIFTY, MIDCPNIFTY. BSE: SENSEX,
BANKEX. MCX gas: NATURALGAS (NATGAS alias), NATGASMINI. MCX crude: CRUDEOIL,
CRUDEOILMINI (CRUDEOILM alias). MCX metals: GOLD, GOLDMINI, SILVER, SILVERMINI
are registry FUTURE identities; option-feed/type availability remains unproved.
They are included as acquisition obligations, not newly enabled products. A symbol
alias is not proof of a historical rename. Index FUTURE calendars, if required,
need separate evidence rather than reuse of the OPTION matrix entries.

## Primary-source access and limits

Public, unauthenticated documentation/site GETs only were attempted. No API POST,
provider account, master download, credential, order or live broker endpoint was used.
Retained files must be treated as documents, not scripts to execute.

| Source | Access result | What can be concluded |
| --- | --- | --- |
| [Dhan option-chain v2 documentation](https://dhanhq.co/docs/v2/option-chain/) | HTTP 200, original bytes retained | Under `Expiry List`: “Retrieve dates of all expiries of any underlying, for which Options Instruments are active.” Inputs identify underlying/segment. This supports a current active-list API contract, not historical availability, publication/listing dates, completeness at past T or cutoff times. Documentation examples are not observed calendars. |
| [NSE circular index](https://www.nseindia.com/resources/exchange-communication-circulars) | Sandbox DNS failure; approved external HTTP/2 error; HTTP/1.1 retry timed out after 20 seconds with zero bytes | No circular acquired; no exact historical change dates established. |
| [BSE](https://www.bseindia.com/) | HTTP 200 site shell retained | Not a dated contract publication; no historical calendar facts obtained. Specific circular/archive identifiers still required. |
| [MCX](https://www.mcxindia.com/) | HTTP 403 access denied retained | No product specification or historical expiry publication obtained. |

Retrieval timestamps and hashes are in `source_catalog`. Publication dates are
unknown; download time is not publication time. Dhan documentation version is v2,
which is **not** a calendar version. No independent reviewer attestation exists.
The access results do not prove that official archives do not exist; they bound
what this acquisition actually obtained. No access restriction was bypassed.

The existing declared NSE 2026 holiday JSON and observed Dhan index-candle dates
retain their original hashes. Neither proves full provider historical listings.
Upstox's retrospective expired-expiry lists belong to another provider and are
corroboration only for these DHAN/ANGELONE rows. The original files remain untouched.
A current Dhan/AngelOne master cannot establish a historical publication timeline.

## Rule-change periods: leads, not verified exchange facts

**Zero exact official rule-change periods were established in this acquisition.**
The following exact ranges are already encoded as metadata-pattern evidence in
`app/hist_options/expiry.py`; their source is code and retrospective metadata,
not retained dated exchange circulars. They remain UNVERIFIED. They must not be
used to manufacture production calendar dates. The code keys these rules on
nominal expiry date, which is not necessarily a circular's publication/effective date.

| Instrument | Existing rule version | Repository pattern interval (inclusive) | Required primary evidence |
| --- | --- | --- | --- |
| NIFTY | NIFTY_WEEKLY_THU_v1 | 2024-10-01–2025-08-31 | Original notice, annexure, transition listings and subsequent corrections; none acquired |
| NIFTY | NIFTY_WEEKLY_TUE_v2 | 2025-09-01–2026-09-29 | Original notice, annexure, transition listings and subsequent corrections; none acquired |
| BANKNIFTY | BANKNIFTY_MONTHLY_THU_v1 | 2025-01-01–2025-08-31 | Original notice, annexure, transition listings and subsequent corrections; none acquired |
| BANKNIFTY | BANKNIFTY_MONTHLY_TUE_v2 | 2025-09-01–2026-09-29 | Original notice, annexure, transition listings and subsequent corrections; none acquired |
| SENSEX | SENSEX_WEEKLY_FRI_v1 | 2024-10-01–2025-01-03 | Original notice, annexure, transition listings and subsequent corrections; none acquired |
| SENSEX | SENSEX_WEEKLY_TUE_v2 | 2025-01-04–2025-08-31 | Original notice, annexure, transition listings and subsequent corrections; none acquired |
| SENSEX | SENSEX_WEEKLY_THU_v3 | 2025-09-01–2026-09-24 | Original notice, annexure, transition listings and subsequent corrections; none acquired |

- Candidate code boundaries: NIFTY/BANKNIFTY 2025-09-01; SENSEX 2025-01-04 and
  2025-09-01. These are **not** independently verified official effective dates.
  Year 2000 / 2999 bounds are implementation sentinels, not evidence of rule duration.
- BANKNIFTY's `2024-12-01` unavailable guard is not proof of the actual weekly
  discontinuation/last-expiry date. Obtain its original notice and final listings.
- FINNIFTY, MIDCPNIFTY and BANKEX weekly/monthly introduction, withdrawal and changes
  remain undated here. Absence of a code rule does not imply no historical changes.
- Each MCX main/mini and metal needs its own launch, maturity schedule, final trading
  cutoff, option-to-future mapping where applicable, and revisions. Never infer an
  option expiry by subtracting business days from its future's expiry.
- For every product obtain special/holiday-shifted contract dates, segment-specific
  holidays/special sessions and emergency changes. No NSE calendar is copied to
  BSE or MCX. Renames/restructures and discontinued periods require official
  identity/lifecycle records plus each provider's mapping/listing history.

## Exact acquisition and review boundary

The manifest's per-combination requests require:

1. Original dated exchange contract files, expiry/last-trading notices and annexures
   for the owner-selected historical window, including corrected/withdrawn contracts.
2. Official segment trading-day calendars and all special/holiday/emergency changes;
   contract-specific final trading times with timezone and applicable rule versions.
3. Original launch, withdrawal, weekly/monthly transition and rename/restructure
   circulars, with publication and effective dates distinguished from nominal expiry.
4. DHAN-specific complete historical expiry-list/master versions with dated underlying
   security IDs/segments, or ANGELONE-specific complete master/token versions and
   historical listing/delisting evidence. Do not borrow one provider's history.
5. Each MCX main/mini mapping and contract specification independently. For metals,
   first prove the actual historical provider/type support; registry names are insufficient.
6. Independent field-by-field review of original bytes, document identifiers/URLs,
   publication/listing/cutoff timestamps, completeness, effective intervals and hashes.
   Record reviewer, review date and review reference. Resolve conflicting sources
   before creating any VERIFIED normalization. Hash/schema checks are not this review.

Exact historical expiry dates and source document IDs cannot be supplied honestly
until those archives and the production coverage interval are supplied. They are
explicit missing fields in the manifest, not replaced with a present-day schedule.
No calendar installation, automatic loader, live verification or production
migration is part of this package. Existing snapshots and earlier reports remain intact.

## Runtime behavior preserved

Historical AUTO/NEXT stays unavailable for UNVERIFIED, PARTIALLY_VERIFIED,
CONFLICTING or NOT_APPLICABLE evidence; absent publication/listing/cutoff evidence;
out-of-period or future-published versions; incomplete eligible universes; conflicting
versions; and NEXT without a second eligible expiry. An active expiry after as-of T
is normal; *future publication/listing* must never authorize a historical choice.

Explicit expiry stays exact and is not labelled calendar-verified. Provider-less
queries still try DHAN, then ANGELONE only under the existing contract, then
unavailable. Explicit providers never silently fall through; calendars are not
substituted across providers. No trading/Greek/expiry behavior was changed.

## Validation

Commands use `validation/isolated_pytest.py`, a clean environment, warnings as errors,
network/environment-file guards and temporary databases. The relevant file list
is preserved in `validation/calendar_acquisition/validation.json` for reproduction.

| Run | Passed | Failed | Errors | Skipped |
| --- | ---: | ---: | ---: | ---: |
| Existing calendar/relevant suite (includes historical tests) | 623 | 0 | 0 | 0 |
| New evidence tests, focused | 9 | 0 | 0 | 0 |
| Final combined relevant suite | 632 | 0 | 0 | 0 |
| Saved Phase 2 diagnostics | 4 | 0 | 0 | 0 |

The final distinct total is **636 passed** (632 + 4); repeated focused/baseline
runs are not additional coverage. No warnings reported with `-W error`.
The unrelated full-repository sweep was not rerun; its prior failures/timeouts and
classification remain in `PHASE3_VALIDATION_INVESTIGATION.md`.

`git diff --check` passed. `git status --short` inspected. Hash comparisons found
no changes to 681 pre-existing inspected code/test/report/validation files; the
three existing local market/calendar JSON artifacts also match their prior hashes.
All additions in this task are the evidence package, this report and its test file.
No pre-existing code, tests, reports, research snapshots or user changes were overwritten.
No live broker calls/orders, credentials read, production schema/data migration or
service restart occurred. Public documentation GETs are listed above; test network
access remained blocked. No synthetic calendar was installed or labelled production-verified.

## Remaining work

Confirm the required historical as-of window, acquire the exact exchange notices
and provider archives enumerated by the manifest, and arrange independent review.
Then create immutable provider-calendar versions from proved entries for only the
reviewed effective periods in a separately reviewed activation task. Until then,
**NOT READY — evidence missing**. No claim of live broker/calendar correctness or
production readiness follows from the passing tests.


### Alternative-source pass validation results

- Focused evidence package: **22 passed** (16 preserved + 6 new).
- Existing calendar/historical coverage plus evidence: **645 passed** (623 + 22).
- Saved Phase 2 diagnostics: **4 passed**; **649 distinct passing cases** including diagnostics.
- Failures/errors/skips/warnings: **0**. Warnings were treated as errors.
- No unrelated full-suite rerun. No pre-existing application/test content changed.
- New coverage: actual source projection, same-symbol exchange separation, third-provider expiry derivation, hash/URL provenance, transport failure classification and no historical promotion from current observations.
- `git diff --check`: passed. SHA256 ledger verified; previous source bytes preserved.
- Deployment: **NOT READY — evidence missing**. No live broker API calls/orders, production migration/restart or calendar behavior change.


### Static archive continuation validation — 2026-10-02.5

- Focused new archive/cache suite: **24 passed**.
- Full relevant calendar/evidence suite: **669 passed** (previous 645 + 24 new).
- Saved Phase 2 diagnostics: **4 passed**; **673 distinct passing cases** total.
- Failures/errors/skips/warnings: **0**; warnings treated as errors.
- `git diff --check`: passed. SHA256 verification covers raw sources, cache entries, parsed inventories, code, tests and reports.
- Prior application and test file hashes unchanged. Earlier raw evidence preserved. Unrelated full suite not run.
- Official archive files acquired: **0**. Secondary datasets acquired: **2**. New network requests: **7** (2 transport failures, 3 discovery responses, 2 dataset bundles). Prior failed attempts imported: **8**. Existing local source inventories reused: **3**. Broker/API token calls and duplicate requests: **0**.
- Calendar statuses: **0 VERIFIED, 0 PARTIALLY_VERIFIED, 28 UNVERIFIED, 0 CONFLICTING, 0 NOT_APPLICABLE**. Candidate row coverage is not independently verified calendar coverage.
- Production calendar installed: **NO**. AUTO/NEXT unchanged and fail-closed. No orders, production migration or service restart.


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
