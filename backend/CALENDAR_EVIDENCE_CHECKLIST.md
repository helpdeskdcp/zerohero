# Track A — historical provider-calendar evidence checklist

Prepared 2026-10-02 from repository evidence only. **Verified production
calendars: none. All 28 provider/instrument combinations below remain
UNAVAILABLE.** No external requests were made, no dates were inferred and no
calendar implementation or production manifest was changed.

Machine-readable companion: [provider_calendar_evidence_checklist.json](validation/provider_calendar_evidence_checklist.json).
This is a collection/review checklist, **not** an installable calendar. The
implemented schema remains `provider-calendar/v1`; there are no installed real
calendar versions. Test-only `synthetic-v1`/`synthetic-v2` are not evidence.

## Required evidence for each row

Every instrument below requires **separate Dhan and AngelOne evidence**. A
matching date at another provider does not prove historical listing at the
requested provider. Default fallback remains a whole-provider fallback under
the existing contract, not permission to substitute calendar records.

1. **Coverage and exact expiries (H):** declare the requested historical as-of
   interval `[start, end)`. This task specifies no activation/backfill interval;
   none is invented here. For every instant in that interval, obtain the complete
   provider-listed eligible expiry universe, including maturities beyond the
   interval's end needed for NEXT. Preserve additions, withdrawals and corrections.
   Do not generate dates from weekdays, month ends or today's rules.
2. **Trading calendar (T):** original dated exchange/segment holiday calendars,
   special-session notices, emergency closures and contract-rule amendments.
   Include historical effective intervals. A missing daily candle is not proof
   of an exchange holiday. NSE COM is not a verified MCX calendar.
3. **Publication/listing (P):** original source bytes and locator, publication
   and capture times with timezone, listing/delisting times, effective intervals,
   completeness evidence, version and SHA-256. A download timestamp does not prove
   that information was public at a past timestamp. Never backdate a current list.
4. **Expiry cutoff (X):** the last trading date AND exact time/timezone for each
   option contract, with source evidence for every holiday shift or amendment.
   Do not assume 15:30, midnight, or a universal exchange rule.
5. **Dhan identity (D):** archived complete expiry-list responses plus historical
   security IDs, segment and contract mappings. The current endpoint/master is
   a source to investigate, not an historical archive.
6. **AngelOne identity (A):** archived complete master versions with symbol/name,
   token, exchange segment, instrument type, expiry and publication/listing proof.
   Captured quotes for a few strikes do not establish the complete expiry list.
7. **MCX relationship (M):** option-to-underlying-future mapping and historically
   valid IDs, independently for main and mini products. An option's cutoff must
   not be inferred from the futures expiry or from a different-sized contract.
8. **Independent review (R):** a reviewer compares each normalized field and
   completeness/temporal claim against retained sources. Record reviewer, review
   date, specific citations, hashes, discrepancies and disposition. A source URL,
   code comment, checksum or passing synthetic test is not independent verification.

## Instrument-by-instrument checklist

`H/T/P/X/D/A/R` and, for MCX, `M` are mandatory for **every** corresponding row.
The inventory column lists existing third-provider metadata only. These are
observed file contents, not the required Dhan/AngelOne expiry set, and not proof
that the interval is complete. Exact Dhan/AngelOne historical dates remain missing.

| Exchange / underlying | Contract type and repository support | Existing date inventory | Required evidence / provider mapping | Evidence date/version and independent review |
| --- | --- | --- | --- | --- |
| NSE NIFTY / NIFTY 50 | Index OPTION; separate FUTURE identity | Upstox: 105 dates, 2024-10-03–2026-09-29 | H/T/P/X/D/A/R; Dhan code `13/IDX_I`; AngelOne NFO/OPTIDX | Upstox file fetched 2026-09-30; no verified Dhan/AngelOne calendar version; **not independently verified** |
| NSE BANKNIFTY | Index OPTION; do not assume today's monthly-only rules for history | Upstox: 30 dates, 2024-10-01–2026-09-29 | H/T/P/X/D/A/R; Dhan `25/IDX_I`; AngelOne NFO/OPTIDX | Same fetch date; no verified provider calendar version; **not independently verified** |
| NSE FINNIFTY | Index OPTION; supported in Dhan adapter/registry | Upstox: 31 dates, 2024-10-01–2026-09-29 | H/T/P/X/D/A/R; Dhan `27/IDX_I`; AngelOne NFO/OPTIDX | Same fetch date; no verified provider calendar version; **not independently verified** |
| NSE MIDCPNIFTY | Index OPTION; supported in Dhan adapter/registry | Upstox: 31 dates, 2024-09-30–2026-09-29 | H/T/P/X/D/A/R; Dhan `442/IDX_I`; AngelOne NFO/OPTIDX | Same fetch date; no verified provider calendar version; **not independently verified** |
| BSE SENSEX | Index OPTION; separate FUTURE identity | Upstox: 103 dates, 2024-10-04–2026-09-24 | H/T/P/X/D/A/R; Dhan `51/IDX_I`; AngelOne BFO/OPTIDX; obtain BSE notices | Same fetch date; no verified provider calendar version; **not independently verified** |
| BSE BANKEX | Index OPTION; supported in Dhan adapter/registry | Upstox: 31 dates, 2024-09-30–2026-09-24 | H/T/P/X/D/A/R; Dhan `69/IDX_I`; AngelOne BFO/OPTIDX; obtain BANKEX-specific amendments | Same fetch date; no verified provider calendar version; **not independently verified** |
| MCX NATGAS / NATURALGAS | OPTION on futures; separate underlying FUTURE | No qualifying historical provider expiry inventory | H/T/P/X/D/A/M/R; Dhan MCX_COMM with dated future security ID; AngelOne MCX/OPTFUT | Historical publication/listing/cutoff artifacts absent; version/date missing; **not independently verified** |
| MCX NATGASMINI | Distinct mini OPTION on futures | Missing; main NATURALGAS calendar is insufficient | H/T/P/X/D/A/M/R; own historical mini IDs and listing set for both providers | Missing; **not independently verified** |
| MCX CRUDEOIL | OPTION on futures; separate underlying FUTURE | No qualifying historical provider expiry inventory | H/T/P/X/D/A/M/R; Dhan MCX_COMM with dated future ID; AngelOne MCX/OPTFUT | Missing; **not independently verified** |
| MCX CRUDEOILMINI / CRUDEOILM | Distinct mini OPTION on futures; different repository aliases | Missing; main CRUDEOIL calendar is insufficient | H/T/P/X/D/A/M/R; independently prove provider alias, mini ID and historical option/future mapping | Missing; **not independently verified** |
| MCX GOLD | Registry/calendar identity exists; working option-feed/contract type not established | Missing | First prove exact derivative/segment support; then H/T/P/X/D/A/M/R | Missing; **not independently verified** |
| MCX GOLDMINI | Registry/calendar identity only; do not invent a broker alias or copy GOLD dates | Missing | Independently establish contract type, mini IDs and support; then H/T/P/X/D/A/M/R | Missing; **not independently verified** |
| MCX SILVER | Registry/calendar identity exists; working option feed not established | Missing | First prove exact derivative/segment support; then H/T/P/X/D/A/M/R | Missing; **not independently verified** |
| MCX SILVERMINI | Registry/calendar identity only; separate mini support must be proved | Missing | Independently establish contract type, mini IDs and support; then H/T/P/X/D/A/M/R | Missing; **not independently verified** |

Identity sources: `app/instruments.py::_SEED`,
`app/optionchain/sources/dhan_chain.py::_UNDERLYING_SCRIP/_MCX_SYMBOLS` and
`app/hist_options/provider_calendar.py::EXCHANGES/SYMBOL_ALIASES`. Numeric IDs
above describe **current code mappings**, not verified historical constants.
INDIAVIX is explicitly non-tradable in the registry and is excluded. Dynamic
user-registered equity derivatives cannot be inventoried without their declared
universe; unknown calendar identities remain unavailable. No production settings
database was opened to manufacture that universe.

## Sources available for independent review

| Source locator | Evidence actually present | Missing before verification |
| --- | --- | --- |
| `https://api.dhan.co/v2/optionchain/expirylist` (POST) | Endpoint implementation in `dhan_chain.expiry_list`; current-state contract | Archived responses for requested historical intervals, full universe, historical IDs, publication/listing and cutoff evidence |
| `https://images.dhan.co/api-data/api-scrip-master.csv` | URL in `connectors/dhan_instruments.py` | Versioned historical master snapshots and authenticated publication provenance |
| `https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json` | URL and current cache used by `instruments.py` | Historical versions, complete-universe coverage and timestamps; current cache is not an archive |
| Upstox `/v2/expired-instruments/expiries` | Retrospective normalized metadata in `app/hist_options/data/expiry_metadata_upstox.json`, reported fetch 2026-09-30 | Original response/review lineage, listing/cutoff proof; also the **wrong provider** for Dhan/AngelOne historical aliases |
| `https://www.nseindia.com/api/holiday-master?type=trading` | Source declaration and 2026 holiday values in `nse_official_holidays_2026.json`, reported fetch 2026-09-30 | Independent review of original response, historical years, special sessions, relevant contract/cutoff notices; no MCX equivalence |
| Dhan `/v2/charts/historical` | `observed_trading_days.json`, fetched 2026-09-30; NIFTY 2022-01-03–2026-09-29, SENSEX 2024-01-01–2026-09-29 | Official holidays and full option-listing/cutoff evidence; observed index bars cannot supply these |
| Official NSE/BSE/MCX historical contract, amendment and holiday circulars | `HIST_OPTIONS_EXPIRY_AUDIT.md` states the original audit lacked official circulars; no qualifying document set established here | Exact document IDs, archive URLs, issue/effective dates, source bytes and independent review. **URLs/document IDs missing; none invented** |

The old audit/capability matrix mentions live MCX dates and a possible
futures-minus-business-days pattern. Those are not independently verified
historical calendar evidence and are deliberately not copied into a manifest.

Existing artifact hashes, rechecked in this task:

| Artifact | SHA-256 |
| --- | --- |
| `expiry_metadata_upstox.json` | `f47398777e5c649cc786d1e5cef99d496e3af654f1f7d2b50478c4eb423b74ae` |
| `observed_trading_days.json` | `7d4ef92788f7c731a98dd6ac942c9d9b8e39458ccbdff67be4c3fe5e59bb67d0` |
| `nse_official_holidays_2026.json` | `70bf4ddd2bd5b014a1ceb4c881a012e68d522ae3511d765a3511ff887e4472a0` |

## Acceptance and current behavior

No row can become VERIFIED until H/T/P/X/D/A/R (and M where applicable) have
concrete source citations and independent review for the selected provider and
declared interval. A calendar can be complete for one interval without covering
another. Superseded/conflicting versions require explicit resolution; today's
list must never be used retroactively.

AUTO/NEXT remains fail-closed for missing/unverified/incomplete evidence,
wrong identity/provider, future publication/listing, uncovered intervals,
conflicting versions, invalid timestamps, expired-only entries or fewer than
two eligible maturities for NEXT. Explicit dates remain authoritative without
being labelled calendar-verified. No provider preference, expiry rule or
test-only synthetic calendar was installed or changed in this task.


## Alternative-source acquisition update — 2026-10-02.4

- [x] Acquire and hash Dhan detailed public master once; retain expiry/type/side/flag per exchange (270 groups, 202,243 total master rows).
- [x] Retain official column definitions and distinguish current listings from historical listing evidence.
- [x] Inspect stored research expiry provenance: 22 audit rows cite Upstox; do not promote DHAN_ROLLING price-source labels to Dhan calendar proof.
- [x] Reuse AngelOne snapshot and Dhan docs; no duplicate or known blocked URL retries.
- [ ] Obtain dated historical exchange contract universe, circular transitions, holiday/session and cutoff evidence for each requested interval.
- [ ] Obtain provider-specific historical publication/listing/withdrawal and complete maturity sets, including NEXT maturities.
- [ ] Establish unresolved mini/metal historical consumer and instrument type, without inheriting main-contract scope.
- [ ] Independent review and signed/traceable disposition against original bytes.

All 28 combinations remain UNVERIFIED. Current public Dhan CSV is accessible; historical archive is not established. Public exchange archives are blocked here; authorized provider exports may be necessary. The 28-row acquisition matrix and individual access/authorization requirements are in CALENDAR_EVIDENCE_ACQUISITION.md and manifest.json. No production calendar activation is authorized by this evidence pass.


## Static archive continuation — 2026-10-02.5

- [x] Separate offline exchange observations from provider listing verification.
- [x] Cache static source attempts, including permanent and transient failures; no automatic retry.
- [x] Parse explicit archive schemas; retain hashes and raw rows; quarantine undocumented numeric date encodings.
- [x] Acquire two secondary datasets without authenticated APIs; retain source families and limitations.
- [x] Keep derived holiday candidates UNVERIFIED and require complete supplied session evidence.
- [ ] Obtain independently reviewable exchange-origin historical files and cutoff/publication/holiday evidence.
- [ ] Independently establish provider historical listing scope; never infer it from exchange or secondary observations.

No broker token is required for exchange-calendar construction or runtime resolution. All 28 provider combinations remain UNVERIFIED. Official archive acquisitions in this pass: 0; secondary datasets: 2. Production AUTO/NEXT remains unchanged and fail-closed.


## Official EOD gate — 2026-10-02.6

- [x] Discover BSE report API from retained official site code/config; retain exact failure.
- [x] Attempt NSE/MCX report landing discovery once; no old file-template reuse.
- [x] Add exact-link-only acquisition, source hashing, parser versions and explicit date/identity fields.
- [x] Preserve BSE expiry/maturity and NSE expiry/actual-expiry separately; never fill missing expiry.
- [ ] Validate a successful current official report listing/download for each exchange.
- [ ] Inspect actual BSE/MCX file schemas before installing a field mapping.
- [ ] Acquire sufficient official observations and session/publication/cutoff evidence for each required range.

All verification remains UNVERIFIED. Secondary datasets do not qualify as official historical verification.
