# Provider calendar continuation — 2026-10-02

Implementation is fail-closed. **No real Dhan or AngelOne historical calendar
has been verified or installed in this phase.** Broker requests, production
database migrations and service restarts are outside this change.

## Contract and versions

`app.hist_options.provider_calendar` provides `CalendarEvidence` and
`ProviderCalendars`. The manifest schema is **provider-calendar/v1**. Calendar
versions are immutable, source-specific identifiers supplied by the curator;
there are **zero production calendar versions**. `synthetic-v1` and
`synthetic-v2` exist only in tests and are not exchange-calendar claims.

An evidence object contains a normalized manifest, its SHA-256, retained source
bytes, verification status, reviewer identity and a review reference. Its JSON
manifest must identify:

- provider, exchange, underlying, instrument (`OPTION` or `FUTURE`);
- schema version, calendar version, source reference and source-byte SHA-256;
- publication time and an effective-from/effective-to interval;
- an explicit attestation that the listed expiry set is complete for that interval;
- entries with expiry date, listing time and final trading cutoff.

All times require offsets and normalize to UTC. Effective and tradable intervals
are half-open: `[from, to)`. Expiry dates are calendar dates in Asia/Kolkata.
At the exact final trading cutoff a contract is no longer eligible. No NSE,
BSE or MCX closing hour is inferred. Holiday shifts and historical rule changes
must already be substantiated in the versioned evidence; weekdays are not used
to manufacture missing entries.

The manifest is a reviewed normalization of the retained source, not a broker
response format. A hash proves byte integrity, **not authenticity, completeness,
publication time or correctness**. `VERIFIED` is a trusted curator's attestation,
not a status inferred from successful parsing or passing tests. This input is
not exposed as an untrusted HTTP upload. A curator must check the manifest
against its source and retain the review before using that status.

For a historical timestamp T, evidence must have been published by T and cover
T. Only contracts listed by T and still tradable at T are eligible. AUTO/CURRENT
select the earliest eligible maturity; NEXT selects the second distinct maturity;
LATEST selects the last. An expiry date after T is normal for an active option.
Future **publication or listing** must never authorize historical selection.
Overlapping applicable versions are a conflict, not an implicit latest-wins rule.

Explicit expiry dates bypass discovery and stay exact. Their provenance says
`EXPLICIT_NOT_CALENDAR_VERIFIED`; an explicit date is not a verification claim.
Existing quote-time, source and post-expiry reconstruction guards still apply.
The Phase 1 rolling WEEK/MONTH resolver is a separate reconstruction contract;
its metadata/rule statuses are not promoted into provider-calendar verification.

## Integration and compatibility

The existing Python `expiry_dates` keyword now accepts a `ProviderCalendars`
object. Bare provider/date mappings can no longer authorize aliases. Existing
positive tests keep their selection assertions and now supply synthetic reviewed
evidence. Explicit-date callers need no new argument.

No automatic file loader or network discovery is enabled for historical reads.
An integration with reviewed evidence constructs immutable evidence objects and
passes the collection to the existing market-hub or captured-chain reader.
The HTTP reader currently has no installed historical evidence and returns
`NO_DATA` with `CALENDAR_EVIDENCE_UNAVAILABLE` for offline aliases. Optional-chain
Python adapters preserve their established `None` no-data return; market-hub's
result envelopes retain detailed reason codes and provider attempts.

Default provider policy is unchanged: DHAN, then ANGELONE, then unavailable.
Each provider must satisfy its own calendar and data contract. An explicit
provider never falls through. Invalid primary evidence may permit a valid
fallback; it cannot be relabelled as fallback evidence. Historical provider-date
overrides from live discovery cannot bypass verification.

Calendar provenance survives in result/row metadata, chain capabilities and
option-leg provenance. A session series selects one expiry **as of the supplied
timestamp**, then reads that contract's history; it does not reselect a different
expiry at each bar. Automatic session reads require an as-of in the requested
IST session. A calendar for today cannot select an expiry for a past session.

## Caller inventory

| Path | Historical alias behavior |
| --- | --- |
| `histcap/read_contract.options` and `resolve_expiry` | Central verified-evidence gate; session/provider/expiry isolation. |
| `market_hub.session_option_quotes_result`, `session_option_quotes`, `_hist_chain`, `get_chain` | Evidence passes through; public no-data envelopes retain calendar reasons. |
| `optionchain/sources/dhan_captured.fetch` | Dhan evidence only, exact selected expiry; no network. |
| `optionchain/sources/angelone_chain.fetch` | AngelOne evidence only; source/time/listing/exchange-bound local views. |
| `optionchain/resolve.get_chain` | Both captured providers use the gate; historical discovery overrides cannot bypass it. |
| `optionchain/api.api_optionchain` | `as_of` disables live lookup. Offline AUTO/NEXT requires installed evidence; currently unavailable. Baseline comparison uses the explicit selected expiry. |
| `market_snapshot.build_snapshot`, `optionchain/qualified_watch`, `microstructure/api` | Their offline option-context calls inherit the gate; no calendar supplied today. Live paths are unchanged. |
| `hedging/runner` offline chain reads | Inherit the gate. No execution code or strategy behavior was edited. |
| `orderflow/backtest` | Explicit option expiry remains authoritative; omitted expiry remains unavailable without evidence. |
| `scripts/orderflow_histsrc`, `orderflow_composite_ablation`, `orderflow_sequence_research` | Provider/expiry-less session reads remain unavailable without calendar evidence and an appropriate as-of. No saved research output was rewritten. |
| Greek exposure engine | Discovers all separately attributed captured expiries, not an AUTO/NEXT choice. AngelOne filters remain unchanged. |
| `session_bars`, historical analytics surfaces | Session candles or explicitly grouped expiries; no automatic option-contract selection added. |

Live-only discovery in `instruments`, `connectors/dhan_instruments`,
`optionchain/sources/dhan_chain`, `market_data`, capture workers and runtime was
inspected, not repurposed as a historical calendar. Its current masters/list
responses cannot prove historical membership or publication time.

Legacy research scripts under `data/research/shared_harness/options.py`,
`reversal_sweep_2026-09/option_premium_test.py`,
`false_breakout_reversal_2026-09/option_premium_test.py`,
`vwap_ema_scalp_2026-09/option_premium_test.py` and their
`calibrated_rerun_2026-09/run.py` consumer independently select the nearest expiry
from captured subsets. They are **not provider-calendar verified**; some also
use symmetric premium lookup and a fixed 15:30 cutoff. They and their saved
results are untouched research artifacts, not certified production readers.
They must not be promoted as verified historical AUTO/NEXT consumers. Migrating
those research contracts and rerunning results requires a separate explicit
research-integrity task and suitable source evidence.

## Instruments and actual evidence

Identity coverage includes NSE NIFTY/NIFTY 50, BANKNIFTY, FINNIFTY, MIDCPNIFTY;
BSE SENSEX/BANKEX; MCX NATURALGAS/NATGAS, NATGASMINI, CRUDEOIL,
CRUDEOILMINI/CRUDEOILM. Minis remain distinct contracts. Registry-only GOLD,
GOLDMINI, SILVER and SILVERMINI identities are accepted when evidence is supplied;
this does not claim working option feeds for them. Unsupported identities fail
closed. These mappings come from `instruments._SEED` and Dhan adapter identifiers,
not from guessed expiry schedules.

Repository evidence inspected, without broker calls:

| Artifact | What it establishes / why it is not installed |
| --- | --- |
| `app/hist_options/data/expiry_metadata_upstox.json` | Retrospective expired-expiry metadata fetched 2026-09-30, labelled Upstox. Not Dhan/AngelOne, no historical listing/complete-list interval or trading-cutoff proof. |
| `app/hist_options/data/observed_trading_days.json` | Dhan daily index candle dates. Observed trading days do not establish complete option listings or official holiday/expiry cutoffs. |
| `app/hist_options/data/nse_official_holidays_2026.json` | Declared NSE holiday source, current year only. Not a provider expiry list. Its COM segment is explicitly not verified as MCX. |
| `data/instrument_master.json` | Current cached master, not a versioned historical archive. Not used as proof of historical availability. |

Inspected artifact SHA-256 values (unchanged):

```
expiry_metadata_upstox.json
f47398777e5c649cc786d1e5cef99d496e3af654f1f7d2b50478c4eb423b74ae
observed_trading_days.json
7d4ef92788f7c731a98dd6ac942c9d9b8e39458ccbdff67be4c3fe5e59bb67d0
nse_official_holidays_2026.json
70bf4ddd2bd5b014a1ceb4c881a012e68d522ae3511d765a3511ff887e4472a0
```

## Intentional unavailable cases and external boundary

- Missing calendar, bare lists, wrong provider/exchange/underlying/instrument,
  or unsupported identity.
- Unverified evidence, missing review/source/version, source/manifest hash
  mismatch, unsupported schema, malformed dates or incomplete expiry universe.
- Future publication, uncovered effective interval, overlapping versions,
  naive timestamps or a session/as-of date mismatch.
- No contract listed and tradable at T; NEXT with fewer than two eligible
  maturities; all relevant contracts expired.
- Valid calendar but no matching valid provider capture: ordinary data-unavailable
  behavior, including the existing explicit-provider no-fallback policy.

For real activation, obtain archived provider-specific contract lists/master
snapshots with traceable publication/listing times, complete-universe coverage,
and official final-trading/holiday-adjustment evidence for each instrument and
effective interval. Retain source bytes and a review establishing the normalized
mapping. Version and review any correction; never backdate today's list. MCX
options need option-contract cutoff evidence, not just underlying-future expiry.
This task stops at that external-verification boundary. No credentials were read,
no external calendar was fetched and no existing artifact was relabelled VERIFIED.

## Validation and delivery

Status: **implementation delivered; real calendar verification and a green full
repository suite remain blocked**. Synthetic tests establish resolver behavior,
not actual broker/calendar correctness.

| Run | Passed | Failed | Errors | Skipped |
| --- | ---: | ---: | ---: | ---: |
| Phase 2 baseline rerun before implementation | 466 | 0 | 0 | 0 |
| Final relevant suite, including 157 new regression cases | 623 | 0 | 0 | 0 |
| Original four Phase 2 diagnostics | 4 | 0 | 0 | 0 |
| Full repository sweep: completed reports only | 2192 | 36 | 0 | 8 |

All 466 baseline case identities are retained. Tests cover AUTO/NEXT, exact
dates, provider-specific fallback, NSE/BSE/MCX adapters, minis, listing and
expiry cutoffs, calendar version boundaries, timezone equivalence, wrong-provider
and exchange rejection, source integrity, historical API no-network behavior and
calendar provenance. Existing monthly, IV, storage/migration, quality and Greek
tests remain in the passing relevant suite. No coverage percentage was collected.

The full sweep attempted **207 test files** in isolated processes. Seven files
exceeded a 90-second process deadline; their incomplete runs are **not included**
in the table's completed-case counts:

- `test_autoscalp.py`
- `test_autoscalp_final_signal_gate.py`
- `test_dhan_connector.py`
- `test_dhan_postback.py`
- `test_main_auth.py`
- `test_p61_filters_ablation.py`
- `test_smart_scalper_scheduler.py`

The 36 reported failures are outside the passing calendar/relevant suite:

| File in `tests/` | Failed cases |
| --- | ---: |
| `test_ai_shadow.py` | 1 |
| `test_hedging_runner.py` | 10 |
| `test_order_pressure.py` | 2 |
| `test_orderflow_v1.py` | 1 |
| `test_phase_h_five_symbol_pipeline.py` | 6 |
| `test_reverse_engineering_status_report.py` | 1 |
| `test_sdk_candle_metadata.py` | 2 |
| `test_shadow_notify.py` | 10 |
| `test_stale_feed_watchdog.py` | 1 |
| `test_standalone_angelone.py` | 2 |

Failures include prohibited non-temporary lock/file/database access, downstream
assertions after blocked persistence, the prohibited subprocess self-test,
per-test deadlines and stale date-dependent MCX fixtures. Eight existing tests
skip without their captured research datasets. No assertions were weakened, no
tests were deleted/skipped by this change, and no production paths were opened
to obtain green results. The initial monolithic full-suite attempt also stalled
in `test_dhan_connector` during asyncio shutdown before calendar implementation;
it was interrupted and replaced by the bounded per-file sweep. These results
are not a claim that every unrelated failure has been root-caused or repaired.

Validation used the existing backend virtualenv and an empty inherited
environment. `/tmp/phase3_pytest.py` blocks outbound sockets, credential-file
reads, subprocesses, non-temporary SQLite connections and writes outside temporary
storage. Warnings were treated as errors; no warning allowances were added.
Existing live-order tests use their mocks under these restrictions; no real broker
access was possible through those Python boundaries.

Artifacts:

- `/tmp/phase3_relevant_before.xml`, `/tmp/phase3_relevant_final.xml`
- `/tmp/phase3_diagnostics.xml`
- `/tmp/phase3_full_final/`: per-file logs/XML and `results.json`
- `/tmp/phase3_calendar.patch`: this continuation only, preserving earlier diffs
- `/tmp/phase3_calendar_files.json`: continuation file manifest

Files changed in this continuation (paths relative to `backend/`):

| Path | Purpose |
| --- | --- |
| `app/hist_options/provider_calendar.py` | New pure evidence model, versioned manifest validation and as-of selection. |
| `app/histcap/read_contract.py` | Verified alias gate, session boundary and quote/calendar matching. |
| `app/market_hub.py` | Calendar provenance and explicit unavailable reasons. |
| `app/optionchain/resolve.py` | Reject live discovery overrides on historical requests. |
| `app/optionchain/api.py` | Explicit calendar-unavailable HTTP reason. |
| `app/optionchain/sources/angelone_chain.py` | Verified selection, listing/exchange filter and calendar provenance. |
| `app/optionchain/sources/dhan_captured.py` | Calendar provenance on captured chain/legs. |
| `tests/calendar_fixtures.py` | Clearly synthetic evidence builder. |
| `tests/test_provider_calendar.py` | New pure calendar regression suite. |
| `tests/test_historical_read_contract.py` | Provider fallback, provenance, adapter and bypass regressions. |
| `tests/test_optionchain_api.py` | Offline AUTO/NEXT reason and no-network tests. |
| `tests/test_optionchain_datalayer.py` | Upgrade evidence fixture; preserve selection assertions and verify provenance. |
| `HISTORICAL_READ_CONTRACT.md` | Replace the unverified-list contract with the evidence requirement. |
| `PROVIDER_CALENDAR_CONTRACT.md` | Evidence inventory, contract, caller map, validation and external boundary. |

`git diff --check` passes. Working tree changes remain uncommitted. Previous
Phase 1/2 changes, original metadata files, raw data and research snapshots are
preserved. **No live broker calls/orders, production migration, service restart,
credential change or production snapshot rewrite occurred.**

Next calendar task: obtain and independently review archived Dhan/AngelOne
option-calendar evidence with completeness, publication/listing times and final
trading cutoffs, then supply reviewed manifests through the implemented boundary.
Separately repair full-suite test isolation/date fixtures before claiming a green
repository-wide validation or deploying this continuation.
