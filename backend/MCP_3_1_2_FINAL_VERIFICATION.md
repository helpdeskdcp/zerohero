# MCP 3.1.2 — Final Verification (test-failure audit only, no code changed)

Run date: 2026-09-30 (UTC 01:49-01:57). No trading logic, config default, or
test assertion was modified to produce this report -- this is a read-only
verification pass, per the request that started it.

## 1-3. The 3 failing tests, run individually

### TEST 1
- **TEST NAME**: `test_dynamic_nse_and_mcx_symbols_are_from_master`
- **FILE**: `tests/test_market_data_selection.py`
- **FAILURE TYPE**: `AssertionError` (stale hardcoded calendar date in the test's own fixture)
- **EXPECTED**: `["CRUDEOILMINI"]`
- **ACTUAL**: `[]`
- **STACK TRACE SUMMARY**: `FakeSDK` hardcodes one MCX future row with `"expiry": "29SEP2026"`. `app/market_data.py:54` filters MCX rows with `_valid_date(sdk, row) >= today`; `today` is the real wall-clock UTC date (now 2026-09-30), so the hardcoded 29SEP2026 row is correctly excluded as expired. The code did exactly what it's supposed to do; the fixture's calendar date simply ran out.
- **FIRST INTRODUCED**: commit `b358500` (2026-09-19, "chore: migrate FastAPI on_event to lifespan; safe ruff cosmetic cleanup") is the file's last touch, but that commit only reformatted the file -- the hardcoded `"29SEP2026"` literal itself predates it and was never relative-dated. The test passed for every day up to 2026-09-29 and started failing the moment the real calendar crossed it.
- **WHETHER MCP 3.1.2 TOUCHED THIS CODE PATH**: **No.** `app/market_data.py` imports only stdlib `datetime`; grepped for any reference to `app/hedging/*` or `app/market_calendar.py` from this file or its test -- zero hits in either direction. This is a different module from `app.market_calendar` (the one MCP 3.1.1 wired into hedging) despite the similar name, and is not imported anywhere under `app/hedging/`.
- **RISK TO LIVE TRADING ENGINE**: **None.** `app/market_data.py`'s `available_symbols()` is not called by the hedging engine, the autoscalp engine, or any order-adjacent path grepped for in this session -- it backs an older/separate symbol-discovery helper. No production route depends on this specific assertion.

### TEST 2
- **TEST NAME**: `test_resolver_handles_mcx_options_on_futures`
- **FILE**: `tests/test_standalone_angelone.py`
- **FAILURE TYPE**: `AssertionError` (stale hardcoded calendar date in the test's own fixture)
- **EXPECTED**: `status == "OK"`
- **ACTUAL**: `status == "CONTRACT_INVALID"`
- **STACK TRACE SUMMARY**: Fixture hardcodes every row's `"expiry": "23SEP2026"`. `broker/angelone/client.py`'s `AngelOneClient.resolve_option_contract()` correctly refuses to resolve a contract whose expiry has already passed relative to the real wall clock (now 2026-09-30) -- `CONTRACT_INVALID` is the resolver doing its job on a fixture that has aged out, not a broken resolver.
- **FIRST INTRODUCED**: commit `21da575` (2026-08-31, "Autonomous Scalper: add MCX NATURALGAS + CRUDEOIL (options-on-futures)"). Notably, this same test *file* already had one date-drift bug fixed in it before (commit `e32b4c8`, "tests: fix date-dependent 0DTE auto-roll test") by switching to relative offsets (`_E1`/`_E2 = now + N days`, see the file's own top-of-file comment: *"relative future expiries -- a fixed date would expire and flip AUTO to the next one"*) -- that fix was applied to a different test in the same file and never back-ported to this one.
- **WHETHER MCP 3.1.2 TOUCHED THIS CODE PATH**: **No.** `AngelOneClient` lives at `/root/zerohero/broker/angelone/client.py` -- a top-level module outside `backend/app/` entirely, structurally separate from `app/hedging/*` and `app/instruments.py` (the resolver the hedging engine actually uses, which I already touched this session for the MCX strike-scaling fix). Grepped `app/` for any import of `broker.angelone.client`: zero results anywhere in the live application, including `app/hedging/`.
- **RISK TO LIVE TRADING ENGINE**: **None.** This module is not imported by any file under `app/` -- it is orphaned/standalone code exercised only by its own test file, not reachable from any live route or the hedging/autoscalp engines.

### TEST 3
- **TEST NAME**: `test_auto_expiry_is_chronological_not_lexical`
- **FILE**: `tests/test_standalone_angelone.py`
- **FAILURE TYPE**: `AssertionError` (same stale hardcoded calendar date, same fixture family as Test 2)
- **EXPECTED**: `expiry == "23SEP2026"`, `token == "1"`
- **ACTUAL**: `expiry == "23OCT2026"`
- **STACK TRACE SUMMARY**: Same root cause as Test 2, same file, same `AngelOneClient`. `AUTO` correctly skips the now-expired `23SEP2026` row and picks the next real nearest-by-date expiry (`23OCT2026`) -- this is the resolver's chronological-not-lexical logic (the exact thing this test's name says it verifies) working correctly on data that has aged past its first hardcoded expiry.
- **FIRST INTRODUCED**: commit `1483188` (2026-08-31, "Pick the nearest option expiry by DATE, not lexically") -- same day as Test 2, same author, same unaddressed hardcoded-date pattern.
- **WHETHER MCP 3.1.2 TOUCHED THIS CODE PATH**: **No.** Identical isolation as Test 2 -- `broker/angelone/client.py`, not imported anywhere under `app/`.
- **RISK TO LIVE TRADING ENGINE**: **None.** Same reasoning as Test 2.

## 3. Proof these are genuinely unrelated

- Neither failing module (`app/market_data.py`, `broker/angelone/client.py`) is imported, directly or transitively, by `app/hedging/*`, `app/market_calendar.py`, or `app/api/hedging_routes.py` (grepped both directions, zero hits).
- All three failures reproduce with the exact same assertion, exact same values, on every run (re-run individually and inside the full suite) -- deterministic given the real wall-clock date, not flaky/order-dependent.
- All three failures are explained by one root cause common to all three: a hardcoded calendar-date string in a test fixture that has since passed, in modules whose own resolvers correctly rejected/rolled-forward past the expired date -- the code path each test exercises is *doing the right thing*; the literal expectation baked into 2026-08-29/08-31/09-19-era fixtures is what's stale. This is the identical failure shape as the two tests already fixed for this exact reason in `test_standalone_angelone.py` (commit `e32b4c8`), just not yet applied to the remaining two.
- None of the three touch `app/hedging/`, `app/execution/`, `app/market_calendar.py`, or any file changed by MCP 3.1.1 or 3.1.2 (full diff of both patches: `app/hedging/runner.py`, `app/hedging/position.py`, `app/hedging/selector.py`, `app/hedging/primary_selector.py`, `app/hedging/risk.py`, `app/hedging/decision_log.py`, `app/api/hedging_routes.py`, `app/market_calendar.py` -- none of these files, nor their tests, appear anywhere in either failing test's call stack).
- Per the task's own instruction not to touch unrelated tests just to force green: **no fix was applied to these 3 tests in this pass.** They remain failing, logged here as non-blocking, pre-existing, and out of scope for MCP 3.1.x.

## 4. Full run matrix

| Suite | Command | Result |
|---|---|---|
| 3 failing tests, individually | `pytest tests/test_market_data_selection.py::test_dynamic_nse_and_mcx_symbols_are_from_master` | 1 failed (as above) |
| | `pytest tests/test_standalone_angelone.py::test_resolver_handles_mcx_options_on_futures` | 1 failed (as above) |
| | `pytest tests/test_standalone_angelone.py::test_auto_expiry_is_chronological_not_lexical` | 1 failed (as above) |
| Full suite | `scripts/run_tests.sh` | **1934 passed, 3 failed** (the 3 above; identical to the pre-verification report byte-for-byte) |
| All hedging + calendar tests (81) | `pytest tests/test_hedging_*.py tests/test_market_calendar.py -v` | **81 passed, 0 failed** |
| Session/calendar | `test_market_calendar.py` (7) + `test_entry_gate_blocks_new_positions_outside_session` + `test_check_exits_ignores_session_gate_for_open_positions` | **9/9 passed** |
| TIME_CUTOFF | `test_check_exit_time_cutoff`, `test_check_exit_carry_forward_past_midnight_is_time_cutoff`, `test_exit_cutoff_is_per_instrument_not_one_generic_time` | **3/3 passed** |
| Emergency EXIT ALL | `test_emergency_exit_all_reports_unresolved_when_chain_lookup_fails`, `test_emergency_exit_all_disarms_the_engine`, `test_emergency_exit_all_closes_positions_with_real_quotes` | **3/3 passed** |
| Paper execution bid/ask | `test_hedge_leg_fills_at_ask_not_ltp`, `test_hedge_leg_rejected_when_ask_missing`, `test_selected_leg_premium_is_bid_not_ltp`, `test_rejects_wall_with_missing_bid_ask_despite_huge_oi`, `test_rejects_wall_with_wide_spread_despite_huge_oi`, `test_rejects_wall_with_low_volume_despite_huge_oi` | **6/6 passed** |
| Portfolio margin | `test_portfolio_margin_ceiling_blocks_new_entry` | **1/1 passed** |
| Decision snapshot | assertions inside `test_armed_scan_opens_a_position_and_notifies`, `test_select_hedge_exposes_all_evaluated_candidates_for_audit` | **2/2 passed** |

Full suite counts include every category above as a subset -- they are listed
separately only because the request asked for them named individually.

## 5. Verdict

| Item | Status |
|---|---|
| `test_dynamic_nse_and_mcx_symbols_are_from_master` | **FAIL / NON-BLOCKER** -- unrelated module, no import path from hedging, pre-existing since before MCP 3.1.x |
| `test_resolver_handles_mcx_options_on_futures` | **FAIL / NON-BLOCKER** -- unrelated module, no import path from hedging, pre-existing since 2026-08-31 |
| `test_auto_expiry_is_chronological_not_lexical` | **FAIL / NON-BLOCKER** -- unrelated module, no import path from hedging, pre-existing since 2026-08-31 |
| execution / risk / margin / hedging / position-state / session / expiry / auto-flat / emergency-exit / reconciliation / P&L / decision-snapshot (the engine itself) | **PASS** -- all 81 hedging + calendar tests pass, including every test added by MCP 3.1.1 and 3.1.2 |

**No BLOCKER found.** None of the 3 failing tests touches the live trading
engine's execution, risk, margin, hedging, position-state, session, expiry
(in the hedging sense), auto-flat, emergency-exit, reconciliation, P&L, or
decision-snapshot code paths -- both failing modules (`app/market_data.py`,
`broker/angelone/client.py`) are structurally unreachable from
`app/hedging/*`. **The system is not blocked from MCP 3.2 work by these 3
failures.**

They remain **open, unfixed, and correctly excluded from this pass's scope**
(per instruction not to touch unrelated tests to force green) -- worth a
separate, small housekeeping pass later: apply the same relative-date-offset
pattern (`_E1`/`_E2`, already established in `test_standalone_angelone.py`
by commit `e32b4c8`) to these two tests and to
`test_market_data_selection.py`'s MCX fixture.
