# Hybrid Market Profile v13.0 — Pine ↔ Python parity audit (2026-10-14)

Research only. No live orders, no production data, no connector/DB-schema changes. Code commit: `39a3412` (not deployed — see §8).
Raw evidence lives next to this file: `PARITY_DISCREPANCIES.csv`, `PARITY_DISCREPANCIES_pre_weekday_fix.csv`, `parity_summary.json`, `parity_run_output.txt`, `baseline_reproduction.txt`.

## 1. Verdicts

| item | status | evidence |
|---|---|---|
| Original Pine source available | **VERIFIED (with a caveat)** | The source existed only in the chat (pasted in 9 fragments). It was reassembled and stored as `app/research_engines/hybrid_mp/pine/hybrid_mp_v13.pine` (693 lines, sha256 `ea4bf3523efe19a4658abf5051f2ae3f15a95cac016a51634f96460e8e93ad1e`). Fragment joins: `SESSIONS / ⏎DAY RESET`, `mtfLon⏎gOK`, `CANDLE ⏎PATTERNS`, `bullCont⏎inuation`, `HELPE⏎RS`, `close[2⏎]`, `alertcondition(⏎t4Event`, plus one indentation join (`if not done4`, 12 spaces) — **the user should diff this file against the TradingView original**. |
| Internal parity: engine vs literal Pine transliteration | **VERIFIED after fixes** | 56/56 comparisons exact: 207,026 events, 27,617 entry signals, 7 datasets × 8 Pine-input configurations (§3). Before the fixes: **FAILED** (§4). |
| External parity: Python vs TradingView trade list / plots | **PENDING — UNVERIFIED** | An indicator has no trade list; Pine cannot be executed here; PyneCore's Pine→Python compiler is a paid cloud service and a hand translation would not be independent. See §7 for the exact export that closes this gap. |
| TradingView semantics of the built-ins that matter (pivots, ATR, highest) | **VERIFIED by reading a TradingView-validated implementation** | PyneCore 6.10.10 (Apache-2.0, source read only, never executed): `ta.pivothigh/low` keep the NEWEST of equal extremes ⇒ a pivot high needs `v ≥ every left bar` and `v > every right bar`; `ta.atr = rma(tr(handle_na=True))` with an SMA seed; `ta.highest` na-resets. |
| Repainting / look-ahead / future-bar access | **FAILED once, FIXED, now VERIFIED** | Found: end-of-session detection looked at bar i+1 (§4-D3). Prefix-invariance tests (full vs truncated data) pass for chart signals, MTF direction (`request.security(f()[1], lookahead_on)` uses only completed higher-TF bars), SMC context, baseline and fixed configurations. |
| Instrument-specific settings (profile auto-detect) | **NOT PORTED (by design)** | Session (NSE 09:15–15:30 / MCX 09:00–23:30) and mintick are explicit parameters. Pine's `str.contains(sym,"CE"/"PE")` and `"GOLD"` heuristics are not reproduced. |

## 2. What was compared
`engine.py` run with the **script's own execution** (`fill="close"`, level exits, `exit_model="PINE"`, no end-of-day exit) and `want_events=True` vs `reference_literal.py`, a statement-by-statement transliteration of the Pine file (scalar `var` state, explicit series lookups, naive loops; shares **no code** with the engine; naive `request.security` built from 1-minute bars). Compared per bar and in order: BoS/CHoCH, pending-setup creation/expiry/session-cancel, rejected retests, BUY/SELL signals with entry type (RETEST/CONT), SL and R, "signal but R < mintick", T1–T4 hits, SL hit, T4 complete. Floats compared with 1e-7 tolerance.

Pine inputs exercised: defaults · Breakout+Retest only · MTF 3-of-4 · VWAP · pattern filter · previous-candle SL · pivot 3 / continuation 3 / retest 4 · all filters together.

| dataset | 1m bars | span | chart | signals compared |
|---|---|---|---|---|
| NIFTY INDEX | 93,168 | 2025-10-08 → 2026-10-12 | 5m | 3,005 |
| NIFTY INDEX | 16,527 | 2026-08-07 → 2026-10-12 | 1m | 2,271 |
| BANKNIFTY INDEX | 93,154 | 2025-10-10 → 2026-10-14 | 5m | 3,243 |
| SENSEX INDEX | 92,614 | 2025-10-14 → 2026-10-14 | 5m | 3,256 |
| CRUDEOIL FUTURE (MCX session 09:00–23:30) | 58,871 | 2026-07-02 → 2026-10-14 16:40 | 1m | 9,026 |
| synthetic random walk, real-looking volume, 2% missing candles, overnight gaps (seed 11) | 25,733 | 70 sessions | 1m | 5,527 |
| same family (seed 12) | 25,731 | 70 sessions | 5m | 1,289 |

Result: **56 of 56 runs EXACT MATCH**, `parity_run_output.txt`. The same comparison now runs inside the test suite (`tests/test_hybrid_mp_audit.py`, 14 parameterised cases + Friday coverage + Saturday-session gating).

## 3. Coverage of the requested checks
* **Candle-close confirmation:** all signals are evaluated on completed bars; entry at the signal close is Pine's plotted behaviour and is kept only in the `as-plotted` mode.
* **Incomplete higher-timeframe candles:** a chart bar uses the direction of the last *completed* higher-TF bar (value of `f()[1]`); the short last 40-minute bucket of the day (15:15–15:30) is treated as a normal bar once the next bucket starts. *Anchoring of the 40-minute buckets at 09:15 is an assumption (TradingView session-anchored), not verified.*
* **Warm-up:** ATR valid from bar `atrLen`, `ta.highest(high[1],L)` from bar `L`, pivots from `2·pivotLen`; signals before that are blocked by na checks in both implementations (every dataset starts cold).
* **Session resets / timezone:** IST fixed offset (+05:30, no DST); `newTradingDay` = calendar day change; pending setups cleared on a new day only when no trade is active; overnight carry exercised (as-plotted mode keeps 30% of 5m trades overnight on real data).
* **Missing candles:** 0.4% missing 1-minute bars in NIFTY, 2% injected in the synthetic sets — both implementations agree (`data_quality_audit.txt`).
* **Weekend / special sessions:** NIFTY data contains 3,663 bars on Saturdays/Sundays (budget-day and Muhurat sessions). Pine's `"…:23456"` session string excludes them, so both implementations drop them (a test pins this).

## 4. Defects the audit exposed (all demonstrated, all fixed, regression tests added)
| id | defect | how it was found | effect | fix / test |
|---|---|---|---|---|
| **D1** | **Weekday formula off by one**: `((days)+4)%7` instead of `+3` ⇒ every **Friday treated as a weekend, every Sunday as a weekday**. Present in `hybrid_mp`, `smart_bos`, the hybrid dashboard and **the live platform 1-second candle engine** (`app/platform/candles/engine_1s.py`). | The independent reference signalled on a Friday (2025-10-17 11:25) where the engine did not; all 56 first-run comparisons mismatched. | In backtests ~20% of sessions were silently dropped (NIFTY 5m fixed: 4,961 → 6,185 trades after the fix). Pre-fix evidence: 606 of 18,621 bars mismatched, engine 245 signals vs reference 355 (`PARITY_DISCREPANCIES_pre_weekday_fix.csv`). **In production the platform would reject every Friday tick as `outside_session` starting Friday 2026-10-16.** The G3 test only used a Saturday, which was right by accident. | `(days+3)%7`; `tests/test_weekday_regression.py` uses real dates for all seven weekdays in every affected module (mutation-checked: fails with the old formula). |
| **D2** | **Pivot tie rule** differed from TradingView (engine allowed right-side ties). | Parity run with `pivot_ties="allow"`: NIFTY 1m 116 mismatching bars, CRUDEOIL 2,576, BANKNIFTY 5m 1; NIFTY 5m/SENSEX 5m/synthetic exact (ties are rare on 5m). `PARITY_DISCREPANCIES.csv` (317 rows, capped at 200 per comparison). | Different swing levels on flat-tick data (MCX, 1m). | `pivots_tv` (new default `pivot_ties="tv"`); unit test. |
| **D3** | **End-of-session detection used the existence of bar i+1** (`day[i+1] != day[i]`) — a data-driven look-ahead; with missing end-of-day candles it would flatten at the wrong bar. | Code audit (Phase 2 item "future-bar access"). | Only matters with gaps in the data, but a live system cannot know the next bar exists. | Clock rule `minute+tf ≥ session end`; positions carried over because the closing bars are missing are flattened at the next day's first open and flagged `EOD_MISSED`; test with truncated data. |
| **D4** | `~p.use_pattern_filter` applied the bitwise NOT to a Python bool (`~False == -1`): correct only by two's-complement accident. | Code audit. | None in output (verified), but fragile. | explicit boolean; dtype test. |
| D5 | Hybrid dashboard "market open" used the same wrong weekday formula (showed MARKET CLOSED on Fridays). | grep after D1. | status text only | `_nse_open()` + test. |
| D6 | `smart_bos` engine (earlier Pine port) had the same formula. | grep after D1. | Its Pine-baseline numbers were produced on Mon–Thu data. | one-line fix + test. **Re-run done** (`smart_bos_rerun_after_weekday_fix.txt`): Pine baseline gross +0.014…+0.044 R (was +0.03…+0.07), −0.18…−0.25 R at 1 bp (was −0.15…−0.23) → NO-GO re-confirmed; the frozen Renko-trend OOS figures are **identical** (n=934, gross +0.149 R, net 2 bps +0.049 R) because that path never used the weekday formula. |

## 5. Deviations from Pine that are kept on purpose (not defects)
Profile auto-detection · labels/lines/tables/alerts · dashboard text · `Planned Qty` (informational) · **VWAP is NaN when fewer than 50% of bars carry volume** (the captured index data has 85–92% zero-volume bars, a vendor artefact; TradingView's index feed has none — the filter is then inactive in both) · a TWAP proxy exists only in the *fixed* variant and is labelled as such · `syminfo.mintick` is a parameter (0.05 used for all instruments).

## 6. Limitations
* The engine and the literal reference were written by the same author from the same text: a shared misreading of Pine would not be caught. The pivot-tie rule is the one place where an outside reference (PyneCore's TradingView-validated source) was available — and it did catch an error.
* Events are compared in the script's own execution model. Execution assumptions that Pine does not define (fills, partial exits, end-of-day exit) are not part of parity; they are audited in `BACKTEST_CORRECTNESS_REPORT.md`.
* MTF bucket anchoring, `request.security` gap handling on missing higher-TF bars, `ta.vwap` anchor, and exchange-calendar holidays are unverified against TradingView.

## 7. How to close external parity (needs the user, ~10 minutes)
In TradingView add, to the original indicator, hidden plots (`display=display.data_window`) for: `buySignal`, `sellSignal`, `buyRejected`, `sellRejected`, `breakH`, `breakL`, `bullCH`, `bearCH`, `t1Event…t4Event`, `slHitEvent`, `entry`, `sl`, `R`, then use *Export chart data* for the same symbol, timeframe (5m / 1m), range and the same inputs. Give the CSV to the audit and `scripts/hybrid_mp_parity.py` can diff it event by event. Without it external parity stays UNVERIFIED.

## 8. Deployment status (important)
Fixes are committed, **not deployed**. `oi-dashboard.service` still runs the old platform candle engine ⇒ **Friday 2026-10-16 from 09:00 IST the platform candle stream would drop every tick** (observation-only stream; no orders involved). Deploying needs one service restart, which requires the user's go-ahead.
