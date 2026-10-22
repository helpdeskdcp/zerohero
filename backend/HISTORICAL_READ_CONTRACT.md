# Historical read contract — Phase 2 continuation

Owner decision: an omitted provider means **DHAN primary, ANGELONE fallback,
then unavailable**. An explicit provider selects only that provider. Fallback
selects a whole result; it never fills holes with another provider's rows.
Existing capture source names remain canonical; no new database schema is used.

## Series and caller compatibility

| Reader / caller | Contract |
| --- | --- |
| `market_hub.session_bars` | Underlying/session view, not an option series. Within the selected provider, FUTURE precedes INDEX and the earliest inserted rollover bar wins. Each bar retains its instrument, expiry, source and timestamp. |
| `session_bars(..., expiry=...)` | Exact FUTURE contract; no INDEX or other-expiry substitution. `series_type=CONTRACT`. |
| `session_bars_result` | Same selection, with status, failure reasons and fallback provenance even when there are no rows. |
| `session_dates` | Dates with a usable result under the same provider/time contract; no future-only dates. |
| `session_option_quotes[_result]` | Exactly one provider and one expiry. Returned quotes retain capture ID, raw ID, instrument ID, exchange, strike, side, capture/exchange timestamps, OI and source. Ambiguous instrument identities are unavailable. |
| `market_hub.get_chain` | Dhan then AngelOne, or strict explicit provider. As-of requests cannot invoke REST. OI deltas use the same provider/expiry/leg and an earlier capture. Missing Greeks/OI remain missing. |
| `optionchain.resolve.get_chain` | Live/captured Dhan then captured AngelOne. Explicit provider wins; `prefer` retains its documented legacy ordering-hint meaning. As-of disables network. No cross-provider Greek merge. |
| `optionchain.history_analytics` | One provider per straddle, IV surface, OI profile or volume aggregate. Straddle ATM/entry inputs are backward-only; the explicitly forward P&L trajectory ends at as-of. API cache keys include provider/as-of. |
| Greek exposure engine | Explicitly AngelOne-only. Greek, IV, OI and underlying inputs cannot fall back to Dhan. Run notes retain input provenance. |
| `market_hub.snapshot`, runtime chain adapter | Explicit AngelOne context, matching their existing AngelOne websocket/token/Greek consumers. No execution behavior changed. |
| Order-flow profile and session research scripts | Continue to consume the underlying/session bar view with attributed records. Signal and risk calculations are unchanged. |
| `orderflow.backtest` | Optional provider and **option** expiry; premium reads stay with the selected session provider. Results retain underlying and premium provenance. Missing premiums retain the existing `INDEX_FALLBACK` label plus an explicit unavailable reason. |
| Premium walk / composite and sequence research | Accept attributed option records; reject mixed-provider/expiry inputs. ATM eligibility and price lookup are backward/as-of. Pure legacy tuple input remains supported without inventing attribution. |
| `scripts/orderflow_histsrc.session_oi_series(src='zerohero')` | Preserves its legacy five-field projection. With no expiry evidence the canonical reader returns no option series. Existing external/research sources and saved artifacts are unchanged. |

`UNDERLYING_SESSION` can span futures rollover captures as the original callers
expect. It is neither an adjusted continuous-futures contract nor a merged
option series. Every retained bar identifies its actual futures contract.
The IV surface is an explicitly labelled multi-expiry grid. Volume spike is an
explicit whole-chain aggregate: expiry remains in its per-contract grouping
before summing, and the included expiries are reported. Neither is presented as
a single-expiry premium series.

## Expiry and time

- Explicit expiry dates are authoritative; equivalent date formats resolve to
  the repository's canonical format, never to a different date.
- Offline AUTO/NEXT requires a reviewed `ProviderCalendars` value through the
  existing `expiry_dates` keyword. Bare provider/date mappings are unverified
  and now fail closed. Evidence must match provider, exchange, underlying,
  instrument and as-of time. A captured subset cannot prove AUTO/NEXT.
  See [PROVIDER_CALENDAR_CONTRACT.md](PROVIDER_CALENDAR_CONTRACT.md) for the
  versioned manifest, provenance requirements and external evidence boundary.
- Live aliases continue through the selected provider's own discovery path.
  A date discovered from Dhan is not passed off as AngelOne discovery.
- As-of timestamps require a timezone. Comparisons normalize offsets to UTC.
- Candles are available only when **bar close <= as-of**. Later backfill
  ingestion is permitted: this is a market-time historical bar API, not a
  reconstruction of what the collector knew before the backfill.
- Quotes require **received time <= as-of and exchange time <= as-of** when
  exchange time exists. Underlying lookups are backward and bounded. Negative
  historical age is not allowed.
- AngelOne chain overlays are bounded by the selected chain observation; later
  OI/underlying captures do not populate an earlier chain.
- Phase 1 post-expiry Greek reconstruction guards, monthly calendar evidence
  rules, percentage IV normalization and expiry-aware storage remain intact.

## Validation and unavailable states

Bar selection validates identity, OHLC finiteness/ranges, volume, timeframe,
session and closed-bar cutoff. Invalid data or interior gaps can cause an
entire default-provider result to fall back. Option captures validate expiry,
source, side, strike, LTP, OI/volume and observation time. Partial chains expose
coverage; missing fields are not fabricated.

Completeness is measured separately for exchange, underlying, expiry, strike,
side, timeframe, source and session. A complete CE series cannot conceal a PE
gap. Reasons for unavailable reconstructed Greeks remain in the quality report.
The report detects **interior gaps only**: absent contracts, session edges and
holiday expectations require external calendar/universe evidence.

Cross-broker comparison selects the latest eligible row per provider/exchange/
leg before applying the result limit. Capture time plus row ID gives deterministic
ties. The 15-minute default window is relative to the requested as-of time.

## Legacy data and operational boundaries

Source-less exports are unknown and cannot be labelled AngelOne. Derived OI
overlays require a run linked to explicit AngelOne input provenance; older
snapshots remain stored and may be contaminated. No historical snapshot is
rewritten or certified clean merely because the current engine is corrected.

No production migration is required by this continuation. Phase 1's existing
migration code is tested only against temporary databases. Production rollout,
legacy rebuilds and credential/calendar verification are separate operations.
No live broker requests, orders, service restarts or data deletion are part of
validation. Unit tests use synthetic inputs and isolated mocked boundaries.

Missing historic expiry evidence is intentionally visible. Consumers needing
automatic historical option selection must supply a verified provider calendar
or an explicit expiry; they must not restore inference from captured subsets.

## Continuation validation — 2026-10-02

Status: **COMPLETE for this provider/expiry/time isolation continuation**.
External calendar/broker verification and subsequent project phases are not
claimed complete.

- Before implementation: 325 relevant tests passed; four saved diagnostics
  reproduced defects. The previous 268 Phase 1 cases and 25 Greek-engine cases
  are all present in the final passing suite.
- Final relevant suite: **466 passed, 0 failed, 0 errors, 0 skipped** across 25
  files, with warnings treated as errors. This comprises the original 325,
  75 additional existing cases and **66 new regression cases**.
- Original diagnostics, unchanged: **4 passed**, separately. The first
  diagnostic's old AngelOne-default name predates the owner decision; its
  expiry-less request is now unavailable. New positive Dhan and strict
  AngelOne tests independently establish the actual provider policy.
- Test-first runs reproduced defects before fixes. An existing MCX fixture
  needed a fixed clock because its sample future had expired. Existing candle
  and chain fixtures now carry real source/identity fields. A quality fixture
  now keeps one strike so its asserted gap is within one contract.
- A final run was interrupted at an existing API test's asyncio executor
  shutdown. Its thread-offload boundary was mocked; its error-response
  assertions are unchanged. The isolated test and complete rerun pass.
- `git diff --check` passes. No test coverage was deleted or skipped.
- Tests ran through `/tmp/phase1_pytest.py` with an empty inherited environment,
  blocked outbound sockets and `.env` reads, and SQLite restricted to temporary
  paths. No coverage percentage was collected.
- JUnit: `/tmp/phase2_completion_validation.xml` and
  `/tmp/phase2_completion_diagnostics.xml`. Continuation-only patch:
  `/tmp/phase2_completion.patch`; manifest: `/tmp/phase2_completion_diff.json`.
- Working tree remains uncommitted. No pre-existing file was deleted; 95
  pre-existing nonsecret files remain byte-for-byte unchanged. Seven already
  modified implementation/test files were extended, preserving earlier fixes.

### Files changed in this continuation

Paths below are relative to `backend/`; pre-existing unrelated modifications
shown by `git status` are excluded from this list.

| Path | Change |
| --- | --- |
| `HISTORICAL_READ_CONTRACT.md` | Contract, compatibility, validation and limitations. |
| `app/histcap/read_contract.py` | New shared read-only provider/expiry/time selectors. |
| `app/market_hub.py` | Session, chain and date-reader isolation; attributed result envelopes. |
| `app/mathematical_confluence/context.py` | Provider-bound spot and prior-session inputs. |
| `app/greeks_engine/engine.py` | Backward underlying lookup, timestamp guards and run provenance. |
| `app/hist_options/quality.py` | Per-contract completeness and unavailable reasons. |
| `app/histcap/store.py` | Latest eligible quote selection before limiting. |
| `app/histcap/cross_broker.py` | As-of comparison and exchange identity. |
| `app/optionchain/chain.py` | Optional per-leg provenance. |
| `app/optionchain/history_analytics.py` | Isolated historical analytics, backward entry/ATM lookup and expiry-aware volume aggregation. |
| `app/optionchain/resolve.py` | Strict providers, captured Dhan and no cross-provider Greek merge. |
| `app/optionchain/api.py` | Provider/as-of inputs and provider-bound baseline/expiry resolution. |
| `app/optionchain/sources/angelone_chain.py` | Explicit attribution, proven overlays and time/expiry guards. |
| `app/optionchain/sources/dhan_captured.py` | New offline Dhan chain adapter. |
| `app/orderflow/backtest.py` | Explicit premium contract and retained research provenance. |
| `app/orderflow/premium_walk.py` | Attributed records and exclusion of future ATM candidates. |
| `app/runtime.py` | Existing AngelOne consumer explicitly selects AngelOne; no execution change. |
| `scripts/orderflow_histsrc.py` | Adapts the existing legacy research projection. |
| `tests/test_historical_read_contract.py` | New provider, expiry, time, fallback and identity regressions. |
| `tests/test_historical_quality_contract.py` | New per-contract gap regressions. |
| `tests/test_greeks_engine.py` | Underlying/OI time and provenance regressions. |
| `tests/test_cross_broker.py` | More than 5,000 rows, future/tied timestamps and exchange isolation. |
| `tests/test_optionchain_datalayer.py` | Source, expiry, legacy overlay and calendar regressions. |
| `tests/test_optionchain_api.py` | Strict provider/as-of API coverage and attributed fixtures. |
| `tests/test_optionchain_data_conflict.py` | Explicit AngelOne comparison input. |
| `tests/test_optionchain_history_analytics.py` | Provider/expiry/time, API caching, invalid data and entry-before-limit regressions. |
| `tests/test_orderflow_premium_walk.py` | Premium provenance, future exclusion and backtest provider binding. |
| `tests/test_orderflow_profile.py` | Canonical candle fixtures; original rollover assertions retained. |
| `tests/test_hist_options.py` | Correct same-contract gap fixture; Phase 1 coverage retained. |
| `tests/test_market_hub_chain_unification.py` | Explicit AngelOne consumer and adapter assertions. |
| `tests/test_market_data_selection.py` | Fixed expiry clock and isolated async offload fixture. |

Security impact: historical/as-of reads cannot invoke a live provider;
provider exceptions expose only their class in the changed resolver paths.
Authentication, credentials and deployment configuration were not changed.

Data-integrity impact: newly read/derived results retain their actual sources,
contracts and temporal boundaries. Unknown data remains unavailable. Existing
research snapshots, raw records, IDs, calendars, IV conversion and Phase 1
migration implementations were not rewritten.

Production impact: **no live broker calls or orders, no production migration,
no service restart, no commit**. This continuation adds no persistent schema.

Remaining risks: authenticated provider/IV/calendar behavior remains externally
unverified; incomplete historical calendars intentionally yield no automatic
option selection; legacy snapshots may remain contaminated; whole-session
completeness cannot be proved from interior observations alone. Tests do not
certify live broker behavior or production migration readiness.

Next task: build and validate versioned provider-specific historical expiry
calendar evidence for the callers that require offline AUTO/NEXT, using mocked
tests first and a separately authorized read-only verification process.
