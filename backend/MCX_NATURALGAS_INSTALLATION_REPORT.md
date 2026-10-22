# MCX Natural Gas-only installation assessment — 2026-10-05

**INSTALLATION BLOCKED.** Only MCX NATURALGAS futures are required for this release. NATGASMINI, CRUDEOIL and CRUDEOILM (including canonical alias CRUDEOILMINI) are DISABLED / OUT_OF_SCOPE and have no pending acquisition or validation requirements. They do not block installation. All other instruments are outside this release.

The current release decision is `calendar_evidence_acquisition/production_scope.json`. No production calendar has been installed, no installation target is claimed and no AUTO/NEXT activation occurred. This scope record is a release assessment, not a runtime activation mechanism. Existing generic research/provider support remains unchanged. Older acquisition reports retain their historical scope and are superseded for this release.

## Original evidence reinspection

All three original PDFs were rehashed and re-extracted with `pdftotext -layout`. The full output matches the retained page-preserving text byte for byte. All seven pages, claims and 36 explicit schedule entries were reviewed. No HTTP or broker request was made. No PDF or October 1 archive was modified.

| Calendar | Table page | Circular reference | Original PDF SHA256 |
|---|---:|---|---|
| 2025 | 3 | MCX/TRD/497/2024 (2024-07-26) | `4101b025fe36cb595b7843236b50e9495fd60175b0348c39e565f7d9b08f6441` |
| 2026 | 2 | MCX/TRD/320/2025 (2025-06-27) | `2f15e2ecc7f610686307d667b94c0788659b8a58acc539ec8bef96e7711cb74a` |
| 2027 | 2 | MCX/TRD/371/2026 (2026-06-29) | `4a31495a37e671df9910a8bfbeed3b06a2da752730628dfa8a7d9d6b61b55e6e` |

Official source URLs associated with the operator-provided PDFs:

- https://www.mcxindia.com/docs/default-source/products/contract-specification/natural-gas/natural-gas-january-2025-contract-onwardse3c71b50-1e45-4513-ac73-76cf7a894681.pdf
- https://www.mcxindia.com/docs/default-source/products/contract-specification/natural-gas/natural-gas-january-2026-contract-onwards2ff2141b-f6e4-4b6a-9f0e-59f260cecf9b.pdf
- https://www.mcxindia.com/docs/default-source/about-us/natural-gas-january-2027-contract-onwards.pdf

The 36 explicit contract-month/launch/expiry rows remain in `calendar_evidence_acquisition/parsed/mcx_naturalgas_local_20261004/evidence.json`; the complete human-readable table is in `MCX_SPECIFICATION_ACQUISITION_REPORT.md`. These are published FUTURE schedules, not options, historical trades or provider-listing observations. They cover the January–December contract months in each of 2025, 2026 and 2027. Page 1 links last trading day to the launch calendar, so the printed expiries support last-trading **dates**. No distinct maturity date is published. No separate maturity date is required or invented for this decision.

## Exact Natural Gas blockers

### DATED_LISTING_AND_EXPIRY_CUTOFF_REQUIRED

Timezone-aware listed_at and last_trading_at for each entry. PDFs give launch dates and 09.00 am to 11.30/11.55 pm conditional on US daylight saving, but no dated DST/session cutoff mapping or explicit timezone.

Existing check: `backend/app/hist_options/provider_calendar.py:CalendarEvidence.validated / ProviderCalendars.resolve`.

### HOLIDAY_EVIDENCE_REQUIRED

Hash-cited holiday_calendar and holiday_adjustment_rule claims required by the existing own-exchange validator. Explicit schedule dates are supported, but these PDFs do not document a holiday calendar/adjustment rule. No schedule date needs to be guessed or recalculated.

Existing check: `backend/app/nse_official/own_exchange.py:OwnExchangeCalendarEvidence.validated`.

### REVIEWED_PUBLICATION_EFFECTIVE_SCOPE_REQUIRED

Reviewed provenance attestation; actual retrieval/publication timestamps, complete effective interval/contract mapping and amendment/freshness review. Referenced circular dates and PDF creation timestamps do not by themselves establish these claims. Original operator retrieval timestamps remain unknown.

Existing check: `backend/app/nse_official/calendar.py:OfficialSource.validate / OfficialCalendarEvidence.validated`.

Page 1 in every PDF gives NATURALGAS, the 1,250 mmBtu futures contract, calendar-based listing/start/last-trading day, maximum six-month duration and the conditional session times. Page 2 gives cash settlement and DDR methodology. The calendar-year tables and circular references appear on 2025 page 3 and 2026/2027 page 2. No additional calendar/holiday/cutoff claim is manufactured from those dates.

The schedule dates themselves are supported. Production validation remains **0**, because these documents alone do not satisfy the existing timestamp-aware and reviewed-evidence gate. There is no request to obtain mini/crude evidence. There is no requirement to provide broker data.

## Scope and read-back

| Instrument | Scope | Pending requirements | Active |
|---|---|---|---|
| NATURALGAS FUTURE | Required, installation blocked | Only its own three blockers above | No |
| NATGASMINI | DISABLED / OUT_OF_SCOPE | None | No |
| CRUDEOIL | DISABLED / OUT_OF_SCOPE | None | No |
| CRUDEOILM / CRUDEOILMINI | DISABLED / OUT_OF_SCOPE | None | No |

Installation was not attempted; an installed-calendar read-back would therefore be misleading. Read-back of the scope record confirms an empty active-calendar list and empty AUTO/NEXT-enabled list. The unchanged provider with no approved evidence returns DATA_UNAVAILABLE for AUTO/NEXT.

Validation and exact files changed are recorded in `validation/mcx_naturalgas_scope_validation.json`. No production installer, scheduler, database, broker adapter or trading code was changed.
