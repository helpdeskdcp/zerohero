# Own-exchange source fallback discovery — 2026-10-04

**NOT READY — acquisition/coverage evidence is missing.** These results do not
claim BSE or MCX lacks official data. Accessibility from this environment,
source discovery, observed contract records and production validation are
separate states. No production behavior was changed in this continuation.

## Newly established BSE acquisition path

Retained official BSE JavaScript explicitly contains the following links. They
are literal published links, not URLs inferred from filenames or dates. The
source is `https://www.bseindia.com/assets/includenew/js/main-FB3J6BO4.js`,
retrieved 2026-10-02T13:53:52.839071+00:00, SHA256
`4f343512fd2bd532acb28143a64b0149a0ba6f7009eb970bc117a3abc5155692`.

| Priority | Official source URL | Evidence role / current limitation |
|---|---|---|
| 1 | https://www.bseindia.com/downloads1/Live_Masters_for_Equity_Derivatives.zip | Production-master candidate. Official UI says “Production Environment Master Files”, “Contract Master” and “The master files for all Simple instruments”. Target bytes not acquired; actual records and coverage unverified. |
| 1, schema companion | https://www.bseindia.com/downloads1/EOD_FF_and_Sample_Files_BSE_Derivativ_segment.zip | File-format/sample candidate, not proof of production contracts. |
| 2 | https://www.bseindia.com/markets/Derivatives/DeriReports/DeriOptionchain | Official market-data discovery page; actual rows and complete request contract missing. |
| 3 | https://www.bseindia.com/static/markets/Derivatives/DeriReports/contractindex | Specification route; previous HTTP 200 payload was an application shell. |
| 4 | https://www.bseindia.com/markets/MarketInfo/NoticesCirculars | Official circular index; a relevant dated SENSEX document remains to be acquired. |
| 5 | https://www.bseindia.com/static/markets/marketinfo/listholi | Official holiday route; holiday dates/rules not acquired. |

The separately published `New_Test_Masters_for_Equity_Derivatives.zip` is not
production evidence. `/downloads1/Master.zip` is linked under currency derivatives
in the official UI and is not a SENSEX master candidate.

Cached official endpoint configuration also publishes `/ddlExpiry_New/w`,
`/ExpiryNew/w`, `/ddlExpiry_IV/w` and `/DerivOptionChain_IV/w`. Their names do
not establish full request parameters or response schemas. No arbitrary
symbol/date request was generated. Each reference retains its discovery bytes,
SHA256 and timestamp in the fallback manifest.

A new, single GET to the independently published API host policy URL,
`https://api.bseindia.com/robots.txt`, returned HTTP 403 at
2026-10-04T06:23:10.634587+00:00. Raw denial SHA256:
`70d9716a0d7062a07deccaa3d2fedb82f14c47562187354c51840126052af67e`.
This is an access diagnostic, **not market-data evidence**. No API data requests
followed. The main BSE host's retained HTML robots response remains an invalid
policy under the existing acquisition contract; the discovered ZIP was therefore
not requested. We did not silently weaken the policy check to download it.

## MCX source fallback chain

1. Official complete contract master / contract-availability records.
2. Official market-data payload containing literal `ExpiryDate` records.
3. Official product contract-specification PDFs.
4. Official launch-calendar and amendment circulars.
5. Official trading/holiday calendar and adjustment evidence.

Each tier is explicitly represented in the acquisition manifest, with a null
specific URL where no first-party link has yet been observed. No PDF paths or
JSON endpoints were invented. The retained first-party routes are:

- https://www.mcxindia.com/market-data/contract-available-for-trading
- https://www.mcxindia.com/market-data/bhavcopy

Both previously returned HTTP 403. The retained
`https://www.mcxindia.com/robots.txt` also returned HTTP 403. These failed URLs
were not retried. No independently usable MCX discovery payload or specific PDF
link exists in the retained first-party material inspected during this pass.
This is a source-access/discovery gap, not a finding that MCX has no official data.

## Instrument results

| Exchange | Instrument | Observed official expiries | Validated production expiries | Missing evidence | Ready |
|---|---|---:|---:|---|---|
| BSE | SENSEX | 0 | 0 | Actual complete master bytes/records; lifecycle/cutoff; effective specifications/amendments; holidays; independent review | No |
| MCX | NATURALGAS | 0 | 0 | Actual own-exchange records; complete listings; option/future mapping; lifecycle/cutoff; holidays; independent review | No |
| MCX | NATGASMINI | 0 | 0 | Same, with mini-contract identity and separate expiry/lifecycle evidence | No |
| MCX | CRUDEOIL | 0 | 0 | Actual own-exchange records; complete listings; option/future mapping; lifecycle/cutoff; holidays; independent review | No |
| MCX | CRUDEOILM | 0 | 0 | Same, with mini-contract identity and separate expiry/lifecycle evidence | No |

All are `MISSING_EVIDENCE`. No `INFERRED/DERIVED` dates were created. No broker
listing evidence, NSE data, third-party data or snippets were substituted.

## Artifacts and validation

- `calendar_evidence_acquisition/parsed/own_exchange_source_fallbacks_20261004.json`:
  ordered acquisition plan, literal source references, discovery hashes/offsets,
  per-instrument missing evidence and policy results.
- `artifacts/staging/own-exchange-fallback-20261004/`: retained raw API-policy
  response, metadata and checksum file. The original acquisition copy remains
  untouched in the existing staging cache.
- `tests/test_own_exchange_fallback_evidence.py`: ten tests protecting exact
  first-party link provenance, priority ordering, test/currency-master separation,
  unknown API parameters, denial retention and fail-closed instrument status.
- `validation/own_exchange_fallback_validation.json`: test and checksum results.

The existing source cache and offline BSE/MCX providers remain unchanged. No
production integration was added for these unacquired candidates. The manifest
is an acquisition queue, not permission to bypass an unavailable/denied policy.

## Exact next acquisition

BSE: obtain the published production-master ZIP and its schema companion through
permitted official access, preserving exact URL, raw bytes and retrieval time.
Inspect actual content before deciding whether it contains SENSEX production
records, what its expiry/maturity fields mean, and which dates it covers.

MCX: obtain a permitted official page response exposing the literal market-data
request or specific contract/launch-calendar document links, then retain the
actual records/PDFs with provenance. Operator-downloaded official files may be
staged for review; they must remain operator-provided until independently checked.

For both, resolve access through the exchange/operator rather than proxies,
identity spoofing or repeated denied requests. A small current sample does not
establish historical completeness. Production stays NOT INSTALLED and AUTO/NEXT
stays FAIL-CLOSED until the existing evidence gates pass.

## Final validation results

Full backend file-isolated run: 2,811 passed, 0 failures/errors/timeouts, 8
existing skips across 222 files. The new evidence test file was added after
enumeration: all 10 cases passed separately and within the relevant suite.
Combined coverage of all 223 current backend test files: **2,821 passed,
0 failures/errors/timeouts, 8 unchanged skips**. Skips concern absent captured
research sessions/Stage-7 data; no tests were weakened or reclassified.

Relevant suite: **1,078 passed**, zero failures/errors/skips, including four
Phase 2 diagnostics. Warnings were treated as errors in this suite. All 278
source/checksum entries passed. October 1 archives remain byte-for-byte unchanged.
New test syntax and `git diff --check` passed. No application code changed.
