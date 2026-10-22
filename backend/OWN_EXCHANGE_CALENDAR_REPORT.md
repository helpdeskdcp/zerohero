# BSE / MCX official-calendar evidence review

Status: **NOT READY — own-exchange evidence missing**. No production calendar,
AUTO/NEXT, ingestion, broker, scheduler, migration or trading activation occurred.

## Actual source acquisition

On 2026-10-03, reused the retained official BSE website JavaScript to discover
`/static/markets/Derivatives/DeriReports/contractindex`. No endpoint or expiry
was generated from a date or filename. No third-party or NSE evidence was used
to establish a BSE/MCX expiry.

| Official URL | Retrieval (UTC) | Result | What it proves |
|---|---|---|---|
| https://www.bseindia.com/robots.txt | 2026-10-03T17:28:03.891553+00:00 | 200, HTML application shell | No usable robots policy or expiry evidence |
| https://www.bseindia.com/static/markets/Derivatives/DeriReports/contractindex | 2026-10-03T17:28:04.021333+00:00 | 200, identical application shell | Route accessibility only; no contract records/specification |
| https://www.mcxindia.com/robots.txt | 2026-10-03T17:28:04.175399+00:00 | 403 | Access-policy retrieval denied; no subsequent MCX request |

Both BSE bodies have SHA256
`0fc66f1aa723c230a6859655af7567dd5bb341643af893d753f86ffc0d6f39e6`.
MCX denial SHA256:
`85e3ff559d08bf5fe1b17095df17589d8f47c94245481f4037e74ca3815c8d67`.

Exact raw bytes, retrieval timestamps, URLs, status/headers, and SHA256 are under
`artifacts/staging/own-exchange-review-20261003-network/`. The separate
`own-exchange-review-20261003/` directory retains two sandbox DNS failures;
these are environment failures, not evidence of an exchange DNS outage.
There were three external GETs, no redirects, no retries of denied endpoints,
no browser identity spoofing and no authentication/broker calls.

The existing robots parser initially treated BSE HTML as an empty allow-all
policy. Consequently the contract page was fetched before this defect was
identified. Four regression cases reproduced the issue. The parser now rejects
HTML with `ROBOTS_POLICY_INVALID`; further BSE requests stopped. MCX's HTTP 403
was never overridden. Prior denied BSE APIs and MCX history/bhavcopy URLs were
not retried. No production evidence or sidecar claiming expiry verification was
created from these responses.

## Coverage, separately by exchange and instrument

| Exchange | Instrument | Official expiry observations | Complete master | Lifecycle/cutoff | Production ready |
|---|---|---:|---|---|---|
| BSE | SENSEX | 0 | No | No | No |
| MCX | NATURALGAS | 0 | No | No | No |
| MCX | NATGASMINI | 0 | No | No | No |
| MCX | CRUDEOIL | 0 | No | No | No |
| MCX | CRUDEOILM | 0 | No | No | No |

`CRUDEOILM` maps to existing repository identity `CRUDEOILMINI`. This is an
application alias, not a claim about an acquired MCX listing. Normalization
preserves the exact source symbol. Options and futures remain distinct.

All five instruments are `MISSING_EVIDENCE`. There are zero
`OBSERVED_OFFICIAL_EXPIRY` records, zero `VALIDATED_PRODUCTION_EXPIRY` records
and zero `INFERRED/DERIVED` dates in this evidence package. Required historical
ranges are copied as planning context from the existing manifest, not declared
proven. See `calendar_evidence_acquisition/parsed/own_exchange_calendar_evidence_20261003.json`.

## Implementation

- `app/nse_official/own_exchange.py`: offline BSE/MCX provider facade using the
  existing shared official-calendar resolver and EOD parser. Its directory is
  historical; BSE/MCX sources must pass their own exchange host checks.
- No default BSE/MCX file schema was invented. A trusted curator must provide a
  field mapping reviewed against actual retained official bytes. Unknown types
  and unsupported instruments reject the whole input, rather than partial use.
- Normalization preserves expiry, maturity and actual-expiry separately; none
  substitutes for another. Contract type, strike, side, numeric lot size,
  identifiers, source symbol, row number, raw hash, discovery hash and field map
  survive. Missing optional fields and intraday timestamps retain reasons and
  null values. Exact duplicates collapse; conflicting identifiers reject input.
- A parsed record is only `OBSERVED_OFFICIAL_EXPIRY`, never a complete master or
  verified calendar. No conversion from parsed rows to reviewed calendars exists.
- Reviewed own-exchange manifests require existing independent review,
  completeness, listing/cutoff, source, version and freshness checks, plus cited
  holiday-calendar and adjustment-rule evidence. An explicit shifted nominal date
  requires an adjustment citation. No holiday dates or expiry dates are computed.
- CURRENT/AUTO/NEXT, explicit expiry and cycle filtering reuse the offline
  resolver. Successful reviewed previews can classify a result as
  `VALIDATED_PRODUCTION_EXPIRY`, but still return `preview_only=true`,
  `production_calendar=NOT_INSTALLED`, `auto_next=FAIL-CLOSED`. The category is a
  review result, not a deployment action. No real reviewed BSE/MCX manifest ships.
- Robots HTML rejection is the only changed pre-existing implementation logic.
  Source checksum manifest was versioned before updating this code hash.

## Validation

Baseline: 1,018 relevant tests passed. New regression cases: 50. Final relevant
suite: **1,068 passed, zero failures/errors/skips**, including four Phase 2
diagnostics, with warnings treated as errors. Focused suite: 127 passed.
Synthetic fixtures exercise code only and do not constitute exchange evidence.

Mypy: 14 source modules passed. Syntax compilation and `git diff --check` passed.
Source-package checksums and unchanged October 1 archive hashes are recorded in
`validation/own_exchange_calendar_validation.json`. No unrelated full backend
rerun or claim of live broker correctness.

## Missing evidence and exact next step

For BSE, obtain an official readable access policy and actual SENSEX production
contract files/specifications/notices. The observed contract route currently
returns only an application shell. For MCX, access to the official public policy
and product/launch-calendar/circular sources must be permitted; the current 403
prevents lawful automated discovery. Operator-downloaded official documents can
be staged with exact source URL and retrieval provenance, but require independent
review and must not be automatically relabelled as independently observed.

For each instrument, obtain complete effective-dated contract listings, explicit
expiry and listing dates, last-trading cutoff/timezone, amendment/launch evidence,
and the relevant official holiday calendar and adjustment rule. A small sample
or a specification weekday is insufficient. Preserve source bytes and hashes,
review file mappings, then submit reviewed manifests for offline preview. Do not
activate production until completeness and independent validation are satisfied.
