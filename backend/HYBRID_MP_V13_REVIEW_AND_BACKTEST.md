# Hybrid Market Profile Engine v13.0 (Pine v6) — review, Python port, backtest (2026-10-14)

**Verdict: NO-GO.** The script has no measurable edge on NIFTY/BANKNIFTY/SENSEX 1-minute data (2017-06 → 2026-10), gross R is indistinguishable from a random-walk null, and after realistic costs every split, year and exit model loses. Status labels: VERIFIED = measured/tested here; READING = from reading the code only; NOT VERIFIED = could not be tested.

## 1. What the script is
An **indicator**, not a strategy: it never simulates P&L. It plots BoS/CHoCH, finds a long/short setup (retest of the broken swing, or — in the default "Hybrid" mode — a 5-bar-range continuation breakout), draws SL (previous candle low/high) and T1–T4 = 1R…4R, and tracks "STOPPED" / "T4 COMPLETE". "Win" and "loss" are therefore **undefined** in Pine; the port makes the exit model an explicit parameter (`PINE` = literal: SL or T4, targets are only milestones).

## 2. Code review (findings, most important first)
1. **No end-of-day exit; trades can stay open for days (VERIFIED).** In the as-plotted model 8.3% of trades are held overnight, 37 for >5 days, one for 232 days (2022-04-11 gap candle, SL 922 pts = 520 bps away) — and an active trade blocks every new signal meanwhile. For options/MIS this is not tradable.
2. **Unbounded risk (VERIFIED).** SL = previous candle's low/high with no cap or ATR bound: median risk 6.3 bps of price, p90 16.7, p99 66.8, max 757 bps. The "planned qty = capital·1%/R" is therefore wildly variable and ignores lot size.
3. **Costs dominate (VERIFIED).** At a median R of 6.3 bps, **1 bp round-trip ≈ 0.16 R**. Anything with gross ≈ 0 R loses by construction.
4. **Entry is at the signal-bar close (READING + VERIFIED).** Alerts fire only after the bar closes, so the plotted entry price is not executable; next-open fills are modelled as the realistic case.
5. **After T1–T3 the stop is not moved; exit is still "STOPPED" (VERIFIED).** Only 17% of trades ever reach T4 (50% reach T1); the literal outcome is −1R or +4R, so a trade that hit T3 and then the stop is booked as a loss.
6. **The retest logic is nearly irrelevant in the default mode (VERIFIED).** 91–94% of trades are "continuation" entries (they can fire on the BoS bar itself because `trendDir` is read after the structure update); retest-only mode is much worse (−0.6 to −0.8 R net).
7. **MTF filter is decorative and noisy (VERIFIED/READING).** `requireMTF` defaults to false; `f_candleDir` falls back to candle colour, so it is ±1 almost always (≈ random). Turning it on (≥3 of 4 TFs) did not help. The `request.security(f()[1], lookahead_on)` idiom itself is non-repainting (tested: no future bar leaks).
8. **A pending setup blocks the opposite break (READING).** `newBreakUp/Dn` require `pendingDir == 0`, so a reversal break inside the 8-bar retest window is ignored; swing levels can also re-break repeatedly (no "used" flag) and structure state carries across days.
9. **Status text can lie (READING).** "BUY CONFIRMED" is set (and the alert fires) even when `candidateR < mintick` and no trade opens (2 cases in 867k bars).
10. **Profile detection (READING, not tested):** `str.contains(sym,"CE"/"PE")` flags many equities (e.g. "RELIANCE") as options; "GOLD"/"SILVER" substrings put NSE ETFs on the MCX session.
11. Only the last trade's lines are kept (`f_deleteLine` on each entry); `pointvalue`-based qty is fractional.

## 3. Port (`app/research_engines/hybrid_mp/engine.py`, 11 tests in `tests/test_hybrid_mp.py`)
Bar-by-bar state machine in the same section order as the Pine script (04 structure → 06/08 pending → 09 signals → 10/11 entry → 12 management; signals use the start-of-bar `active`; management starts at `bar_index > entryBar`; stop before targets inside a bar). Defaults are the script's defaults — **nothing was tuned**. Execution assumptions that Pine does not define are explicit flags: `exit_model` (PINE / T1–T3 / SCALE 25%×4 / SCALE_BE), `fill` (close | next_open), `gap_fills`, `eod_flat`. Tests cover: continuation fires on the BoS bar, SL even after T1, same-bar SL-vs-T4 (stop wins), +4R, SCALE accounting, next-open fill + skipped gap, gap fills, retest expiry, EOD flat, **prefix invariance (no look-ahead) for both execution modes**, MTF direction uses only completed higher bars, session-anchored resampling. **NOT VERIFIED: trade-for-trade parity with TradingView** (an indicator has no trade list to export); ties in `ta.pivothigh/low` are unspecified (strict vs allow made no difference).

## 4. Backtest design (fixed beforehand)
Data: NIFTY index 1m 2017-06 → 2026-10 (867,533 session bars), BANKNIFTY/SENSEX 1m from 2021-09, NATURALGAS/CRUDEOIL futures 1m only ≈3.5 months. Splits TRAIN 2017-06..2021-12, VALID 2022..2023, OOS 2024-01.. (no parameter fitted, so all are out-of-sample for the as-is script). **Primary:** literal Pine exits, 1m chart, REALISTIC execution (next-open fill, gap fills at the open, flat at session end), 2 bps round-trip. Index points, R = move / planned risk. Bootstrap 95% CI on mean R.

## 5. Results — NIFTY 1m, primary (16,667 trades, ≈9/day)
| split | n | gross meanR (CI) | net 1 bp | net 2 bps | win% | reach T1/T2/T3/T4 |
|---|---|---|---|---|---|---|
| TRAIN | 7,590 | +0.047 (+0.001, +0.091) | −0.120 | −0.286 | 25.2 | 50/32/23/17% |
| VALID | 3,893 | +0.057 (−0.010, +0.118) | −0.146 | −0.350 | 24.5 | 48/31/23/18% |
| OOS | 5,184 | −0.005 (−0.057, +0.045) | −0.211 | −0.416 | 23.4 | 47/31/22/17% |
Every calendar year 2017–2026 is negative at 2 bps (−0.21 … −0.44 R).

**Null benchmark (VERIFIED):** sign-flipping each 1m bar (destroys drift/autocorrelation, keeps volatility) and re-running the engine gives gross **+0.02…+0.08 R (mean ≈ +0.04)**; an iid random-walk check shows the same sign and **shrinks to ≈0 as the intrabar path is made finer** (+0.04 → ≈0). So ≈ +0.03–0.04 R of "gross edge" is an OHLC-discretisation artefact (bar extremes under-state the true path, so near stops are hit less often than in reality). The real gross numbers above are **at or below** this null — there is no edge to find.

Secondary views (all declared, none tuned) — every one is net-negative on TRAIN, VALID and OOS: as-plotted (close fill, level exits, overnight holds; gross, optimistic) TRAIN +0.076 / VALID +0.092 / OOS +0.022 R (OOS CI includes 0, and it is the unrealistic model); exits T1 −0.35…−0.46, T2 −0.32…−0.44, T3 −0.31…−0.45, SCALE −0.30…−0.43, SCALE_BE −0.31…−0.45 R (2 bps); retest-only −0.62…−0.77; pattern filter −0.37…−0.51; MTF≥3 −0.35…−0.48; strict pivots −0.29…−0.42; candle-SL off −0.29…−0.39. Chart 3m: gross +0.016/+0.046/+0.012, net −0.16/−0.16/−0.20; chart 5m: gross +0.079/+0.001/+0.005, net −0.058/−0.160/−0.158.
Other instruments (same frozen config, 2 bps): BANKNIFTY gross −0.048 (CI −0.084…−0.009), net −0.341; SENSEX gross +0.051 (+0.013…+0.089) but net −0.365; NATURALGAS gross −0.178, CRUDEOIL −0.062 (≈1,000 trades each, only 3.5 months → INCONCLUSIVE, not evidence).

## 6. Limits
Index-level R only; option premiums (theta/spread) would be worse, not tested. Cost model is an estimate (index futures ≈2–3 bps round trip), not measured. Port vs TradingView parity NOT VERIFIED. The artefact above means any gross figure below ~+0.04 R on this data is not distinguishable from zero.

## 7. Reproduce
`python scripts/hybrid_mp_research.py primary|variants|tf|other|null NIFTY` — raw outputs in `data/research/hybrid_mp/`. Not wired to autoscalp or any live path.
