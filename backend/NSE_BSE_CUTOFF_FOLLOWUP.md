# NSE/BSE cutoff discovery follow-up — 2026-10-05

**Production calendar NOT INSTALLED; AUTO/NEXT FAIL-CLOSED. MCX remains DISABLED / OUT_OF_SCOPE.** No application, test, calendar configuration or production-gate code changed in this follow-up.

## Newly observed official NSE route

Starting with the retained official consolidated-circular index HTML, acquired these literal script-src URLs once each:

- `https://www.nseindia.com/assets/js/nse-min.js?v=01102026`
- `https://www.nseindia.com/dist/js/static/public/assets/js/regulatoryStatusModule.js?v=01102026`
- `https://www.nseindia.com/dist/js/components/quick-links.js?v=01102026`

The quick-links source explicitly calls `B.get('/api/circulars')`. The circular card renders the returned `circFilelink` as the download URL. This is an observed official request contract, not a generated archive pattern.

A single GET to **https://www.nseindia.com/api/circulars** returned HTTP 200 and **150 circular metadata records**. Raw SHA256: `5f9227080d8756a1dc4304b3bb72c82fb00d3ab739310d53c3ed3ddbe7d8f3cc`; retrieval **2026-10-05T02:32:09.458746+00:00**.

The response states fromDate `28-09-2026`, toDate `05-10-2026`; actual publication dates range **2026-09-28 through 2026-10-01**. Six records are NSE Futures & Options circulars: quantity-freeze limits, mock trading, stock-contract lot revision/introduction, and index-constituent changes. None of those titles substantiates the missing production cutoff amendment. Their PDF/ZIP links were retained as metadata, not downloaded merely to increase source counts.

Response date fields are not proof of accepted historical request parameters. No guessed date-filtered API request was sent. This recent index is not complete historical coverage, expiry evidence or proof that an earlier amendment does not exist.

## BSE discovery reused locally

The already retained official `chunk-XNUURNBE.js`, `chunk-SCEKLEXO.js` and `appConfig.json` publish:

- API base: `https://api.bseindia.com/BseIndiaAPI/api`
- Advanced-search component: `/getDataAdvance_New/w`
- Latest-notices component: `/LatestNewBindset_New/w`
- Exact search parameter fields and the `Derivatives` segment, retained in the parsed report.

No BSE network request was made. The API host's previously retained robots response is HTTP 403. The existing acquisition policy therefore cannot authorize this route; it was not bypassed or repeatedly retried. This **does not mean BSE lacks official data**. Existing official SENSEX/BANKEX master observations remain intact.

## Retention and next evidence requirement

New raw bytes, actual response metadata, retrieval timestamps and hashes are in:

- `artifacts/staging/nse-bse-cutoff-followup-20261005/` — two sandbox DNS failures, explicitly not official document evidence.
- `artifacts/staging/nse-bse-cutoff-followup-network-20261005/` — three successful script acquisitions after network permission; cached scripts reused without further requests.
- `artifacts/staging/nse-bse-cutoff-circulars-20261005/` — successful official circular-index JSON.
- `backend/calendar_evidence_acquisition/parsed/nse_bse_cutoff_followup_20261005.json` — source chain, six literal F&O metadata rows, BSE request references and remaining blockers.

There were **four successful HTTPS requests**, plus two sandbox DNS failures before escalation. No automatic redirects, broker requests, MCX requests or security bypasses. No new expiry was generated or production-validated.

Remaining: the official NSE historical circular-search mechanism or an operator-retained official date-filtered export; accessible BSE cutoff/amendment documents or operator-retained official notices; and the historical listing/publication coverage and independent production reviews documented in `NSE_BSE_REMAINING_EVIDENCE_REPORT.md`. Do not resolve the 15:30/15:40 applicability conflict by choosing a convenient time.

## Validation

- Complete relevant suite: **1,221 passed / 0 failed / 0 errors / 0 skips**, warnings treated as errors; includes four saved Phase 2 diagnostics.
- Mypy passed for the six previously touched parser/calendar/control/test files; syntax validation passed.
- Retained and new source checksums, release checksum verification, and `git diff --check` passed.
- No code/tests weakened or changed. Original PDFs/October archives preserved. No broker calls, orders, migrations, service restarts, scheduler activation or calendar installation.

Validation details: `backend/validation/nse_bse_cutoff_followup_validation.json`. All files added in this follow-up are enumerated and hashed by `backend/validation/NSE_BSE_CUTOFF_FOLLOWUP_SHA256SUMS`; no pre-existing repository file was modified.
