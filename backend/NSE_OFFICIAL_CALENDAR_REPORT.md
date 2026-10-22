# Official expiry calendar implementation and evidence — 2026-10-03

**Production calendar: NOT READY / NOT INSTALLED. Application AUTO/NEXT remains FAIL-CLOSED.**

The offline official-calendar implementation is delivered. No production calendar
is loaded or installed, no broker configuration or trading path changed, and no
production database migration, ingestion, scheduler activation or restart occurred.
Tests demonstrate resolver behavior, not completeness of real exchange evidence.

## Files created or changed

- `app/nse_official/calendar.py`: official source/evidence validation and offline
  resolution/preview using the existing provider-calendar resolver.
- `app/hist_options/provider_calendar.py`: small extension points for an explicit
  evidence subclass/schema and an optional, evidenced cycle filter. Existing
  DHAN/ANGELONE identity rules and default resolution behavior remain unchanged.
- `tests/test_official_expiry_calendar.py`: 57 regression cases. Synthetic reviewed
  manifests exist only inside tests and are never installed as production data.
- `calendar_evidence_acquisition/parsed/nse_official_calendar_evidence_20261003.json`:
  retained source register, rule transitions, 30 literal expiry observations and gaps.
- `calendar_evidence_acquisition/parsed/nse_calendar_acquisition_20261003.json`:
  local master candidate inventory; raw numeric date values intentionally undecoded.
- `artifacts/staging/nse-calendar-review-20261003/`: original source bytes, metadata,
  extracted circular text, a copy of the existing child master and SHA256 checksums.
- This report and `validation/official_calendar_validation.json`.

## Evidence acquired and exact sources

All downloads were read-only HTTPS requests with certificate verification,
HTTP/1.1, bounded timeouts, identifying agent headers and robots-policy checks.
No browser identity spoofing, broker credentials, arbitrary proxy or denial bypass.
An initial sandbox DNS/transport failure was retried only after network permission.
No existing historical archive was redownloaded.

| Official source | Result | What it establishes |
| --- | --- | --- |
| `https://www.nseindia.com/static/market-data/market-timings` | HTTP 200, SHA256 `022cacc11dec6fd6a39e44569ffdfa78d1349d1990cd6fa0b67ade7408c41227` | Current displayed session context. Equity derivatives normal close is **15:40 hrs.**; it is not promoted to an evidenced per-contract expiry cutoff or historical rule. |
| `https://www.nseindia.com/static/market-data/securities-information-contracts-available-for-trading` | HTTP 200, SHA256 `548bdc3344aabe97e44cd7737f5fb46fc6cd7e462a0aeac0faa208328e8b145a` | Official index of specifications and reports. It is not itself a full contract master. |
| `https://www.nseindia.com/static/products-services/equity-derivatives-contract-specifications` | HTTP 200, SHA256 `9e91df5dcaafc532985e648eb36d7c1ca2156801f0d546268d78d7d064e07ea8` | Current weekly/monthly/quarterly/half-yearly product cycles and holiday-adjustment text; displayed update `11/08/2026`. No historical publication timestamp invented. |
| `https://nsearchives.nseindia.com/web/sites/default/files/inline-files/FAOP68747.pdf` | HTTP 200, SHA256 `e1b56024a511135ffd5c6c3c097881dd0ac2c37b15b5f61e94fe84e51cf66762` | Official circular NSE/FAOP/68747 (111/2025), June 25, 2025: dated rule transition and explicit annexure expiry observations. |

The canonical page URLs were **not guessed by adding `/static`**. Each was
published in an official same-host HTTP 301 Location. The original request stopped;
its response was recorded; the exact destination was separately reviewed and
requested with further redirects disabled. This did not change the application's
redirect policy. The PDF URL was copied from the acquired specification's href.
The archive host's robots response was 404, which the existing policy parser
permits; that does not verify any calendar. Request metadata and hashes are retained.

Previously observed official expiry sources reused without another download:

- `https://www.nseindia.com/api/option-chain-contract-info?symbol=NIFTY`
- `https://www.nseindia.com/api/option-chain-contract-info?symbol=BANKNIFTY`
- `https://www.nseindia.com/api/option-chain-contract-info?symbol=FINNIFTY`
- `https://www.nseindia.com/api/option-chain-contract-info?symbol=MIDCPNIFTY`

Their original exact URLs, response bytes, hashes and retrieval timestamps remain
in `nse_completion_20261003.json` and the earlier immutable staging directories.
No report/archive URL was manufactured from a filename or date.

## Rule changes — retained facts, no generated dates

Circular NSE/FAOP/68747 explicitly distinguishes:

1. Existing contracts expiring on/before August 31, 2025 remain unchanged, with
   the circular's stated treatment of long-dated NIFTY/BANKNIFTY options retained.
2. Existing NIFTY/BANKNIFTY long-dated options September 2025 onwards are realigned
   at **July 31, 2025 EOD** and reflected in that contract master.
3. Newly generated contracts expiring on/after September 1, 2025 use the revised
   Tuesday convention from **June 26, 2025 EOD onwards**.
4. The annexure contains explicit dates and states that dates may change for
   declared holidays. Neither "EOD" nor a publication date is converted into an
   invented clock time. The circular is not used to fill unobserved weekdays.

Eight explicit NIFTY half-yearly annexure dates also occur in the retained current
selector: 2026-12-29, 2027-06-29, 2027-12-28, 2028-06-27, 2028-12-26,
2029-06-26, 2029-12-24 and 2030-06-25. Their cycle annotations retain the circular
qualification; they do not become complete verified calendar records. Other cycle
classifications remain unknown instead of being inferred from their position in a list.

## Existing local contract master discovery

The preserved parent `Reports-Archives-Multiple-01102026.zip` contains
`NSE_FO_contract_01102026.csv.gz`. Its exact child SHA256 is
`395d41189cccdaa19f7544c4263c265c9f978cd3dd8a98a97f4ba8a1d64bfbcd`.
A copy is retained as **OPERATOR_PROVIDED_ARTIFACT / UNVERIFIED**. The original
parent and Bhavcopy child were not modified.

The candidate contains 9,158 NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY FUTIDX/OPTIDX
rows. Fields include `XpryDt`, `StartDt`, `MtrtyDt`, identifiers and lot quantities.
The date fields are numeric. No retained official layout establishes their epoch,
timezone or exact meaning. **No numeric date conversion was attempted**, no expiry
was derived from a contract name, and these candidate rows were not ingested.
Observed daily-report metadata matches the filename, but does not independently
prove the supplied bytes' download provenance.

## Implementation contract

`OfficialCalendarEvidence` extends the existing reviewed evidence model with schema
`official-exchange-calendar/v1`, official same-exchange source allowlists, exact
source bytes/hashes, explicit retrieval times and claim references for:

- complete coverage and contract mapping;
- publication and effective interval;
- each expiry, listing timestamp and last-trading cutoff;
- each weekly/monthly/quarterly/half-yearly classification, when known.

A nonempty reviewer and review reference are required. A VERIFIED label plus a
hash does not prove the interpretation: the trusted curator must review each
claim against its retained source. This constructor is not an untrusted HTTP upload
or an automatic importer. No real reviewed official calendar object is shipped.

`OfficialCalendars.preview(want, underlying, as_of, instrument='OPTION', cycle=None)`:

- CURRENT/AUTO selects the earliest **listed and still tradable** expiry.
- NEXT selects the second distinct eligible expiry; insufficient entries fail closed.
- Explicit expiry must be present and eligible; it is never replaced by an alias.
- MONTHLY/WEEKLY filters require classifications for every eligible candidate;
  unknown classification makes selection unavailable, not a guess.
- Effective and listing/trading intervals are half-open. At the exact evidenced
  final-trading instant the expiring entry is no longer eligible.
- Freshness is measured from the evidenced publication time using an explicit
  positive `max_age_seconds` policy. Re-fetching old content cannot make it fresh.
- Future publication, uncovered intervals, stale/incomplete evidence, conflicting
  versions, missing source bytes/claims/review and wrong exchange fail closed.
- All instants must carry timezones. General market hours are not injected into entries.

A successful offline preview still says `production_calendar=NOT_INSTALLED`,
`auto_next=FAIL-CLOSED`, `preview_only=true`. No adapter, service, scheduler or
production consumer was switched to this calendar. The prior production gate
remains in force. Bare lists/current selectors cannot authorize it.

NSE, BSE and MCX are isolated namespaces. Normalized instrument types (OPTIDX /
FUTIDX for index contracts, OPTFUT / FUTCOM for MCX) require an evidenced mapping;
they are not assumed to be raw exchange-file codes. Broker listing evidence is
separate: an NSE official manifest cannot satisfy a DHAN/ANGELONE request.

## Coverage and remaining blockers

| Instrument | Validated literal expiry observations | Fully reviewed resolvable entries |
| --- | ---: | ---: |
| NIFTY | 18 | 0 |
| BANKNIFTY | 6 | 0 |
| FINNIFTY | 3 | 0 |
| MIDCPNIFTY | 3 | 0 |
| SENSEX / BANKEX | 0 in this official NSE workflow | 0 |
| MCX contracts | 0 in this official NSE workflow | 0 |

Total: **30 validated date observations; 8 annexure-corroborated cycle annotations;
0 fully verified, resolvable production-calendar entries.**

Still required: independent master download provenance and official field layout;
complete applicable contract universe; exact publication/listing and cutoff
coverage; remaining cycle classifications; holiday/exception and lifecycle
applicability; independent review. BSE and MCX require their own official evidence.
An NSE commodity page is not MCX evidence. Existing provider calendars remain unverified.

## Validation and safety

Final result: **1,018 passed, 0 failed, 0 errors, 0 skipped, 0 warnings**; includes 57 new calendar cases and all four Phase-2 diagnostics. Focused calendar/provider run: 176 passed. Mypy passed for 13 modules; syntax and `git diff --check` passed. All **266 source checksum entries** passed. See `validation/official_calendar_validation.json` for machine-readable results. The full
relevant calendar/evidence/NSE suite and all four Phase-2 diagnostics are run with
network, secrets, subprocesses and non-temporary DB/file mutation blocked.
No assertion was weakened, no test skipped or removed. Synthetic positive
resolution tests are explicitly test-only. Real source tests verify retained
hashes and that incomplete evidence remains uninstalled.

No production calendar, ingestion, broker call/order, migration, trading activation
or service restart. **NOT READY — evidence and independent review remain incomplete.**
