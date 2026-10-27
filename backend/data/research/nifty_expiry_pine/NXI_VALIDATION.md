# NXI validation, backtest methodology and results (v1.0.0, 2026-10-27)

**Read this first.** Every statement below was produced in the **PineTS 0.13.0** engine (the Smart Chart runner) on stored NIFTY data. **Nothing was compiled or run in TradingView.** TradingView compilation is **PENDING** and must be the first thing you do.

| item | status | evidence |
|---|---|---|
| Compiles in TradingView | **PENDING (not tested)** | no TradingView access from this environment |
| Compiles / runs in PineTS | **yes** | `scripts/pine_nifty_harness.py compile|run` (no diagnostics, no runtime errors on 38,284 bars) |
| Rule tests (hand-derived expectations + independent Python reference) | **23 / 23 pass** | `tests/test_nifty_expiry_pine.py` |
| Indicator / level / HTF reference check | **0 mismatches** on 13 series x 12,000 bars | `AUDIT_RESULT.json` -> reference |
| Pivot confirmation delay | **1,159 swing highs, 0 bad** | same file -> pivot_delay |
| Look-ahead / repaint (truncation) test | **4 cuts x 72 plot series: 0 differences; closed trades identical** | same file -> causality |
| Hard-rule invariants on 130 real entries | **0 violations** | `REPORT_2y.json` -> invariants |
| In-script probability tracker vs independent recomputation | **exact match** (bull 60/26/14, bear 64/32/17) | `REPORT_2y.json` -> tracker_check |
| Probability calibration | **NOT CALIBRATED / no skill over the base rate** | `REPORT_2y.json` -> calibration_study |
| Strategy profitability | **NOT demonstrated** (see results) | below |

## 1 Data and method
* NIFTY 50 index, 1-minute bars stored in `data/historical/stocks_1m/stocks_1m.db`, aggregated to **5-minute bars anchored at 09:15 IST**: 38,284 bars, 2024-10-01 .. 2026-10-26 (~511 sessions). The index has **no volume** (every bar volume 0), so all volume features are inactive in these tests; VWAP is the TWAP fallback.
* The engine executes the Pine source exactly as pasted (`nifty_expiry_intelligence.pine`, sha256 prefix `189b16fa`). No parameter was tuned on these results; defaults were fixed before any run (the only changes after the first run were **bug fixes found by validation**, listed in section 5, none of them a result-driven threshold change).
* The Strategy Tester analogue trades the **index** with 10 units, 1.5 bps commission per side + 10 ticks (0.5 pt) slippage per fill, fills on the next bar open, partial exits 40/30/30 at 1R/2R/3R with break-even/T1 trailing, 15:15 flatten, max 2 trades per session. It is **not** an option P&L test: no premium, spread, theta or IV is modelled.
* **History contains no OI zones** (no historical option-chain OI exists in the chart), so only the previous-day high/low zones and price structure were available. The manual-OI mode cannot be back-tested.
* Statistics are recomputed by `scripts/pine_nifty_report.py` from the run's plots and closed trades, per **entry** (partial exits summed), not from the script's own panel.

## 2 Look-ahead and repainting audit
* **Higher-timeframe requests** (4 in the model + 2 for the option symbols): HTF series are requested as `f()[1]` with `lookahead_on` (previous completed HTF bar). The four HTF votes were **recomputed in Python from the 1-minute data** (bars anchored 09:15, EMA 9/21 SMA-seeded, vote rule as in the code): 12,000/12,000 bars identical per timeframe. The option-symbol requests use the same-timeframe `lookahead_off` at bar close and could **not** be exercised (PineTS accepts only the chart symbol): untested.
* **Truncation test**: the script was re-run on 35 %, 55 %, 75 % and 92 % of the history; every plotted series (72 including the decoded components) and every closed trade before the cut is identical to the full run. A script that peeked at future bars or repainted would differ.
* **Pivots / swings**: custom explicit pivot (strict left, non-strict right) confirmed R=3 bars late; 1,159 swing highs checked against `high[pivot bar]` and the window maximum: 0 errors. SMC events (BOS, CHoCH, sweeps, failed breaks, retests) and FVG open counts match an independent Python state machine **bar for bar on 2,600 real bars** and on synthetic paths.
* **Session boundaries**: day segmentation, previous-day levels, gap points, daily ATR, opening range, ATR(14) and RSI(14) match the reference on all 12,000 bars; the first/second candle events are fixed to the 09:15/09:20 bars; nothing is back-dated (`test_gap_scenario_never_changes_retroactively`).
* **Bull/bear symmetry**: the whole market was mirrored (`p' = 50000 - p`) on 3,200 real bars; every evidence component, scenario code, event bit, stress flag, signal and RSI came out mirrored. This test found a real asymmetry (tie resolved to bullish) - fixed.

## 3 Results (frozen source; the first run had 133 entries before the cooldown fix)
| metric | value |
|---|---|
| bars / sessions | 38,284 / ~511 |
| entries (closed-trade rows incl. partial exits) | **130** (276) |
| win rate (per entry) | 43.1 % |
| net profit after declared costs | **-8,653.5** (account currency, 10 units) |
| profit factor | **0.721** |
| expectancy per entry | **-66.6** (bootstrap 95 % CI -149.5 .. +21.4) |
| average win / average loss | +400.0 / -419.6 |
| max drawdown (entry-level) | 9,019 |
| CE (long) / PE (short) | n=63, PF 0.641 / n=67, PF 0.803 |
| gap down / flat open / gap up | n=28, PF **0.163** (-8,126) / n=63, PF **1.08** (+1,026; CI of mean -111 .. +153) / n=39, PF 0.819 (-1,553) |
| expiry sessions (rule-based dates) / other | n=35, PF 0.840 / n=95, PF 0.689 |
| chronological split: first 60 % of entries / last 40 % | PF 0.681 / PF 0.794 (both negative) |
| by half-year PF | 2024-H2 0.49, 2025-H1 0.71, 2025-H2 0.66, 2026-H1 0.99, 2026-H2 0.56 |
| gross R-multiple (before costs) | mean -0.002 R, median -0.084 R, 14.6 % reach >= 1 R |
| gross P&L before any cost | +894 (about +6.9 per entry) |
| net at 0 / 0.5 / 1.0 / 1.5 (declared) / 2.0 / 3.0 bps per side | +894 / -2,289 / -5,471 / **-8,654** / -11,837 / -18,202 |

**Interpretation (no spin):** the rule set shows **no tradable edge** on this index-proxy test. The signal is a coin flip before costs (mean gross 0 R) and is negative after any realistic cost. The bootstrap interval of the expectancy includes zero but its centre is negative; the one positive subset (flat open, PF 1.08) is statistically indistinguishable from zero (n=63) and was not selected on results. The pattern is consistent with the repository's earlier index-level research (all prior price-action engines also failed after costs). Nothing here supports going live or sizing real capital; **no profitability claim is made**. The gap-down subset being the worst bucket is an observation for further research, not a rule change.

## 4 Probability and calibration
* In Pine: `PROBABILITY NOT CALIBRATED (n=x/50)` until 50 resolved virtual signals per side; afterwards a **running in-sample** hit rate of "Target 1 before the stop within 24 bars" with a Wilson 95 % interval, labelled in-sample. It is never a forecast.
* Offline chronological study (train first 60 % / test last 40 % of resolved virtual signals; logistic model on |evidence index|, active components, side): bull n=60 test Brier 0.277 vs base-rate 0.244, AUC 0.49; bear n=64 Brier 0.262 vs 0.261, AUC 0.50; pooled n=124 Brier 0.257 vs 0.249, AUC 0.44. **No skill over the base rate; reliability bins do not line up.** The evidence index is therefore not a probability and must not be read as one. Headline figures such as 90 %, 95 % or 99 % are not supportable and are not displayed anywhere.
* Virtual signals still unresolved at the end of the history are excluded; timeouts (neither target nor stop within 24 bars) are counted separately by the tracker: bull 14, bear 17.

## 5 Defects found by this validation (all fixed)
1. Cooldown after a loss applied one bar late (the cooldown was created after the entry decision of the same bar) and its loss test depended on when the engine books entry commission -> now computed first and from closed-trade profits (`test_hard_rules_hold_on_real_history_including_cooldown_after_a_loss`).
2. Same-bar tie between a bullish and a bearish structure/sweep event was resolved in favour of bullish -> tie is now 0 (found by the mirror test).
3. `ta.pivothigh/low` tie handling is engine-dependent -> explicit pivot loops (identical in every engine).
4. FVG open-zone count was taken before the 8-zone cap -> cap first, then count.
5. Pine `for i = 0 to size-1` on an empty array iterates (0, -1): index error on the first empty array / zero trades -> guarded (FVG count, statistics panel).
6. `int x = f()` where `f` returns float (would not compile in TradingView; PineTS accepted it) -> `int()`.
7. Plot budget: 70 plots exceeded TradingView's 64 -> related flags packed, 48 plots.
8. `barstate.isfirst or timeframe.change("D")`: lazy `or` could skip the history-dependent call on bar 0 -> evaluated separately.
9. Dashboard number format `"+#.#;-#.#"` is not portable -> manual sign helper.
10. Max session loss was per-unit points with variable sizing -> account currency.

## 6 TradingView compile-risk list (things PineTS may accept but TradingView may not)
* script size (87 KB source) and total token/variable counts; * `input.time`/`input.symbol` defaults and `confirm=false` use; * dynamic `high[R + k]` offsets inside functions; * tuple assignment of 9 values from `request.security` with a function call; * `strategy.exit` with `qty=` plus `comment_profit/comment_loss`; * `alert()` inside a strategy with `alert.freq_once_per_bar_close`; * `table.cell` called from a function; * `str.format` patterns `{0,number,0.0}`; * `array<box>` with `na` elements. If TradingView rejects any line, send the exact message.

## 7 Test checklist for you in TradingView (not done)
1. Paste, compile, add to NSE:NIFTY 5m. 2. Confirm the dashboard shows `vol UNAVAILABLE` and `TWAP (no volume)`. 3. Strategy Tester: compare trade count / net with section 3 (differences are expected, see `NXI_LIMITATIONS.md`; investigate if the trade count differs by more than ~25 %). 4. Bar-replay a gap-down and a gap-up session and read scenario/validation text. 5. Enter OI strikes with a previous-session date and verify `MANUAL OI INPUT (dated)`; set the date wrong and verify `STALE`. 6. Select real CE/PE symbols and verify the premium rows and `Premium plan`. 7. Create the alert and check message text. 8. Switch to a 3-minute chart and verify `RUN ON 5-MIN CHART`. 9. Resize to a phone width and use compact mode.

## 7b v2.0: 3-minute entry refinement (added 2026-10-27)
**Mechanics (verified):** `tests/test_nifty_expiry_pine_3m.py` (9 tests); the 5m-chart outputs are unchanged by the refactor (72 plots x 38,284 bars and 276 trade rows identical to v1, net -8653.45); on a 3m host the engine steps equal the 5m chart's own outputs (1,800 steps, 18 series, 0 mismatches, `pine_nifty_align3m.py`); steps come only from completed 5m bars; truncation causality holds; the three refinement modes behave as specified.

**Outcome on real data (NOT conclusive):** the planned 2-year / 6-month-block 3m runs could not be completed (3m runs are very slow in PineTS and the VPS was at load ~19-27; a 2-year run reached 3.6 GB RSS and was killed). Only one window was run: 2026-09-01..2026-10-26 (`REPORT_3m_S.json`, `scripts/pine_nifty_compare3m.py` with `BLK=S`):

| setup | entries | net (Rs) | win rate | PF |
|---|---|---|---|---|
| 5-minute chart | 5 | +365.9 | 0.60 | 1.66 |
| 3m chart, refinement Off | 6 | -352.1 | 0.17 | 0.64 |
| 3m chart, Micro-breakout | 0 | 0 | - | - |
| 3m chart, Pullback then resume | 1 | +3.1 | 1.0 | - |

With n = 0-6 these numbers carry no information (bootstrap CIs span zero). The refinement is **not shown to help**; Micro-breakout produced no entries in this window. A fair comparison needs the full window on a quieter machine.

## 8 Remaining issues / not done
TradingView compile and Strategy-Tester run (pending) - 3-minute refinement (implemented in v2.0, PineTS-tested for mechanics; profitability NOT shown - sample too small) - option-symbol `request.security` path (untested) - manual OI mode (live-only, untestable on history) - dashboard visual check (rendered text verified, no screenshot) - probability calibration (not achievable in Pine; offline result: none) - everything touching real option P&L.

## 9 Change log
v2.0.0 (2026-10-27): 5m engine moved into a function read from lower-timeframe charts; 3-minute refinement modes (Off / Micro-breakout / Pullback then resume); fail-closed timing guard; see 7b for validation status.
v1.0.0 (2026-10-27): first release. Modules: sessions/gap, OI+price zones, first-candle engine, six scenarios, SMC, seller-stress, candle/volume proxy, CE/PE premium, 11-component evidence index, tracker, entry/SL/targets/partial exits, risk controls, configurable dashboard, statistics panel, alerts. Validation fixes: section 5.
