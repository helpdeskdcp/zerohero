# NSE/BSE remaining evidence review — 2026-10-05

**INSTALLATION BLOCKED. Production calendar NOT INSTALLED. NSE/BSE AUTO/NEXT FAIL-CLOSED. MCX DISABLED / OUT_OF_SCOPE and unchanged.**

This continuation acquired additional official NSE documentation and replayed retained BSE master bytes. It did not activate a calendar, fabricate expiry dates, change runtime resolution, or treat a successful test as production evidence. MCX has no pending production requirements and was not acquired or revalidated for production.

## Evidence acquired and retained

The new acquisition directory is `artifacts/staging/nse-bse-requirements-20261005/`. Each request has original response bytes, exact URL, retrieval time, actual HTTP status/headers and SHA256. Five unique HTTPS GETs were made with certificate verification, bounded timeouts, robots checks, no proxy, no redirect following and no retries. These were official NSE requests only, not broker requests.

1. `https://www.nseindia.com/static/resources/consolidated-master-circulars` — HTTP 200; exact href discovered in the previously retained official market-timings page. Retrieved **2026-10-05T02:18:36.592402+00:00**. SHA256 `923ae7f613fa50d12ce204f2e2af29a8cdbcabdcad845ead54a07386dfd20700`.
2. `https://nsearchives.nseindia.com/content/circulars/FAOP73928.zip` — HTTP 200; exact href labelled **F&O Consolidated Circular** in that index. Retrieved **2026-10-05T02:19:26.375855+00:00**. SHA256 `faf73ca3a85e84f3275673a27e546a7b9852236455aa85d0db7639d394f45365`.
3. The separately observed official NEAT-platform href returned HTTP 301 to `/static/trade/platform-services-neat-trading-system`; it was recorded, not followed. It is not calendar evidence.
4. The other two requests were robots checks: www.nseindia.com HTTP 200 and nsearchives.nseindia.com HTTP 404. The existing policy permits the latter; robots responses do not constitute calendar evidence.

ZIP member `FAOP73928.pdf` has SHA256 `2889bb2c455474aa96619ef6e10ab33bd71cc4501effd92de455897169c96c71`. All member hashes, raw-source discovery links and relevant workbook cells are retained in `calendar_evidence_acquisition/parsed/nse_bse_remaining_evidence_20261005.json`.

### What the new NSE source proves

- PDF page 1: **NSE/FAOP/73928**, circular 51/2026, **April 28, 2026**, a compilation through March 31, 2026 replacing the earlier consolidated circular. This does not prove the absence of later amendments.
- PDF page 45: **15 explicitly listed weekday holidays and four weekend holidays for 2026**. Muhurat trading on November 8 has timings to be notified separately. No expiry date was generated from the list.
- PDF page 46: **normal market close 15:30**. The retained current NSE Equity Derivatives market-timings page explicitly displays **15:40**. The effective applicability of these competing claims remains unresolved. Neither is injected as a historical contract cutoff.
- ZIP member `PART-D.xlsx`, **Annexure 1, D17**: contract.txt expiry_date is seconds from January 1, 1980 and denotes the last trading day; rows 78/80 explain contract introduction and maturity.
- **Annexure 13, C5** identifies `NSE_FO_contract_ddmmyyyy.csv.gz`; row 14 maps expiry_date to XpryDt, typed CHAR(12); rows 21/23 map introduction/maturity fields. These are schema claims, not additional observed expiries. The numeric staged master was not decoded into timezone-aware cutoffs.

This advances the old report's “no retained epoch layout” gap. It does **not** establish the staged master bytes' independent download provenance, the exact numeric timestamp/timezone mapping, historical listed universes or production eligibility.

### BSE observations added without network reacquisition

The existing official master and version 14.2 schema were reused:

- `https://www.bseindia.com/downloads1/Live_Masters_for_Equity_Derivatives.zip`
- `https://www.bseindia.com/downloads1/EOD_FF_and_Sample_Files_BSE_Derivativ_segment.zip`

Their exact discovery URLs, retrieval times, raw/member/schema hashes and row numbers accompany every normalized observation. No schema sample rows were used as contracts.

The parser now accepts an explicit `underlying="BANKEX"` in addition to its unchanged SENSEX default. Unsupported symbols are rejected. Exact symbol filtering yielded **1,049 BANKEX contracts: 1,046 options and three futures**, with literal monthly expiry dates **2026-10-29, 2026-11-26, 2026-12-31**. There are no duplicate or conflicting identifiers. The existing SENSEX snapshot remains **4,051 contracts / 21 observed expiry dates**.

All records remain **OBSERVED_OFFICIAL_EXPIRY**, not VALIDATED_PRODUCTION_EXPIRY. XpryDt and settlement EndDt remain separate; MtrtyDt is still a filler. Date-only StartDt is not converted into a listing instant. No contract is assigned an invented intraday cutoff.

## Exact installation blockers

| Scope | Observed evidence | Production-validated entries | Still required |
|---|---|---:|---|
| NSE NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY | 30 retained literal current expiry observations; new dated circular, holiday list and master-layout evidence | 0 | Independent provenance for the staged master; reviewed numeric date/time mapping; complete historical listing/publication intervals and cycle mapping; applicable timezone/cutoff and later amendments resolving 15:30 versus 15:40; independent review and existing gate reports |
| BSE SENSEX | 4,051 contracts / 21 expiry dates from one snapshot | 0 | Historical/publication/listing coverage; effective cutoff/timezone; resolve specification 15:40 versus FAQ 15:30; applicable holiday amendments and independent production review |
| BSE BANKEX | 1,049 contracts / 3 expiry dates from that same snapshot | 0 | Historical/publication/listing coverage; BANKEX-specific effective cutoff/timezone and applicable holiday amendments; independent production review |
| MCX | Retained history only; no work performed | Not applicable | **None — disabled/out of scope** |

Existing consumer windows remain requirements, not proven coverage:

| Instrument | Required start | Required end inclusive |
|---|---|---|
| NIFTY | 2026-07-01 | 2026-10-01 |
| BANKNIFTY | 2026-07-24 | 2026-10-01 |
| FINNIFTY / MIDCPNIFTY / BANKEX | 2026-09-04 | 2026-10-02 |
| SENSEX | 2026-09-03 | 2026-10-02 |

A current selector or a single dated master does not establish the universe available throughout these intervals. Official exchange observations also do not prove Dhan/AngelOne historical listings. No provider calendar is silently substituted.

The production gate still requires reviewed reports for full-suite health, contract master, expiry calendar, historical ingestion, live freshness, provenance, duplicates and discrepancy validation. This relevant-suite run does not grant those independent production attestations. The existing resolver and gates were not changed.

## Validation and read-back

- Previous confirmed relevant baseline: **1,211 passed**.
- Added **10 tests**: six BANKEX filtering/fail-closed cases and four retained-source integrity/replay cases. The initial six BANKEX regressions failed before the parser accepted explicit BANKEX; they passed after the minimal change.
- Focused BSE/evidence run: **36 passed**.
- Complete relevant run: **1,221 passed, 0 failures, 0 errors, 0 skips**, warnings treated as errors. Includes the four saved Phase 2 diagnostics and the existing disabled-scope coverage.
- Mypy: **passed, six files**. An initial type-check found three optional-XML typing errors in the new evidence test; explicit assertions corrected them before the final run.
- Syntax: **passed, all three changed Python files**.
- Existing/source checksums: **424 entries passed across 22 manifests**. A separate release manifest binds this change's new artifacts. Only the BSE parser code hash was refreshed in the prior direct-acquisition manifest; its previous/new values and reason are retained. No source hash was rewritten to excuse modified evidence.
- `git diff --check`: **passed**.
- Scope/gate read-back: MCX DISABLED, OUT_OF_SCOPE, NOT_ENABLED, AUTO/NEXT DISABLED, pending requirements empty. NSE/BSE gates BLOCKED; all 12 AUTO/NEXT previews for the six supported indices returned unavailable, with no expiry selected.
- October 1 Bhavcopy remains `d44a052b79a3961910f74783960ebbd5043696dbed39e7fb86ec84929fd60338`; parent archive remains `2c5aae28b3d6682ee8dc2c7162041acfbf5c6999233d653a2efd157450f40fb0`.

No production calendar was installed, so this is a **non-installation/readiness read-back**, not a claim of successful installation. No MCX code/configuration/evidence changed; no broker calls, orders, trading changes, migrations, scheduler activation or service restarts occurred.

## Exact files changed in this continuation

Modified:

- `backend/calendar_evidence_acquisition/bse_master.py`
- `backend/validation/OWN_EXCHANGE_DIRECT_SHA256SUMS` (only BSE parser hash)

Created:

- `backend/tests/test_bse_bankex_evidence.py`
- `backend/tests/test_nse_bse_remaining_evidence.py`
- `backend/calendar_evidence_acquisition/parsed/bse_bankex_master_observations_20261005.json.gz`
- `backend/calendar_evidence_acquisition/parsed/nse_bse_remaining_evidence_20261005.json`
- `backend/NSE_BSE_REMAINING_EVIDENCE_REPORT.md`
- `backend/validation/nse_bse_remaining_validation.json`
- `backend/validation/nse_bse_remaining_checksum_changes.json`
- `backend/validation/NSE_BSE_REMAINING_SHA256SUMS`
- `artifacts/staging/nse-bse-requirements-20261005/77d03cc3bb6163db3dd3.body`
- `artifacts/staging/nse-bse-requirements-20261005/77d03cc3bb6163db3dd3.json`
- `artifacts/staging/nse-bse-requirements-20261005/9402c64d404525236f8d.body`
- `artifacts/staging/nse-bse-requirements-20261005/9402c64d404525236f8d.json`
- `artifacts/staging/nse-bse-requirements-20261005/b97722fb25702322f8f1.body`
- `artifacts/staging/nse-bse-requirements-20261005/b97722fb25702322f8f1.json`
- `artifacts/staging/nse-bse-requirements-20261005/be5fadb6a8203b917339.body`
- `artifacts/staging/nse-bse-requirements-20261005/be5fadb6a8203b917339.json`
- `artifacts/staging/nse-bse-requirements-20261005/d967bf4322e7a80c7656.body`
- `artifacts/staging/nse-bse-requirements-20261005/d967bf4322e7a80c7656.json`
- `artifacts/staging/nse-bse-requirements-20261005/nse-73928-2889bb2c4554.txt`
- `artifacts/staging/nse-bse-requirements-20261005/SHA256SUMS`

All unrelated pre-existing tracked/untracked work is preserved. No commit was made.

Next required evidence action: obtain authoritative effective cutoff/amendment evidence and historical contract publication/listing coverage for NSE/BSE, then independently review the retained source claims against the existing calendar schema. Do not install or enable AUTO/NEXT before those checks succeed.
