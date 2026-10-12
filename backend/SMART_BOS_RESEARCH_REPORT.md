# Smart NSE+MCX BoS/CHoCH (Pine v6) + Renko — research report (2026-10-12)

**Verdict: NO-GO for autoscalp.** Pine signals as written lose after costs; BoS/CHoCH adds nothing; a Renko trend-continuation entry has a small *gross* edge that is about the size of realistic costs and has faded in 2025–26. Nothing was wired into autoscalp. Status labels: VERIFIED = measured here; NOT VERIFIED = could not be tested with available data.

## 1. Pine review (what the script really does)
- It is an `indicator()`: it draws entry/SL/T1-T3 but never simulates trades; there is no win-rate in it. Any "daily signal has an edge" impression comes from the drawings, not from a measurement.
- ~9.6 signals per signal-day (16,051 signals / 1,677 days on NIFTY 1m, 2017-2026): BoS repeats on every fresh swing break, not one signal per day. Longest day: 23.
- SL = signal candle low/high ± buffer, median risk 7.6 NIFTY points (~4 bps of price): any realistic cost is a large fraction of 1R.
- `ta.atr(14)` ignores the `atrLen` input; `minBuffer = 0.5` points is an index-sized constant (larger than 1m ATR on NATURALGAS).
- No exits are defined beyond T1-T3 (user also asked for T4); no session-end flatten.
- `request.security(..., [high[1], low[1], close[1]], lookahead_on)` is the standard non-repainting previous-day idiom (OK).

## 2. Port (`app/research_engines/smart_bos/`, 14 tests, `tests/test_smart_bos.py`)
`engine.py` (faithful causal state machine), `renko.py` (brick size from PRIOR sessions only, resets per session), `renko_trend.py` (entries at the first brick that reaches a run of N), `sim.py` (next-bar-open fill, adverse-first intrabar, gap fills at open, 1 position, EOD flat, costs in bps/R), `splits.py` (fixed chronological splits).
Assumptions that Pine leaves ambiguous: pivot tie handling (`allow`), previous-day = previous regular session with ≥90% of bars, swing/trend state carried across sessions, signal bar = confirmed close, fill = next open. Tests prove no look-ahead (signals/Renko state on truncated data equal the full-data ones).

## 3. Data
NIFTY INDEX 1m 2017-06 → 2026-10 (931,831 bars, 375/day, 12 inconsistent bars, volume 92% zero → volume filter off); BANKNIFTY/SENSEX INDEX 1m 2021-09 →; NATURALGAS/CRUDEOIL FUTURE 1m only since 2026-06/07 (~3.5 months). Splits fixed beforehand: TRAIN 2017-06..2021-12, VALID 2022..2023, OOS 2024-01..2026-10 (untouched until the final run).

## 4. Results (NIFTY index points; R = move / planned risk; costs are round-trip bps of price)
1. **Pine levels as written (SL=signal candle, T1/T2/T3=2R/3R/4R), TRAIN+VALID:** gross +0.03..+0.07 R/trade (0.3-0.7 bps); at 1 bp cost -0.15..-0.23 R, CI entirely below 0. NO-GO (VERIFIED).
2. **Does BoS/CHoCH add anything?** Attribution null (random entry bars, side = Renko direction, run ≥3, same count per day, 40 reps): null mean +0.247 R vs real BoS+Renko +0.188 R, p(null ≥ real)=1.0. BoS/CHoCH adds no value (VERIFIED).
3. **Renko trend-continuation (independent of BoS):** 570 configs searched on TRAIN, confirmed on VALID (both positive for 298; Spearman TRAIN↔VALID 0.79). Frozen before OOS: k=0.12 × prior-5-session range per brick, enter after a 3-brick run, SL 2 bricks, targets 3/6/9/12 bricks, exit T4.
4. **Simulator sanity:** sign-flip random-walk null (drift and autocorrelation destroyed, volatility kept): gross -0.05..+0.09 R, mean ≈ 0, versus real +0.22 (TRAIN) / +0.16 (VALID). An earlier within-day *shuffle* null looked "better than real" and was discarded as a bad null (it preserves each day's net move = hindsight drift).
5. **OOS NIFTY 2024-01..2026-10 (n=934), frozen config, run once:** gross +0.149 R (+2.7 bps), CI95 (+0.043,+0.256); net 1 bp +0.099 R (CI -0.007..+0.206); 2 bps +0.049 (CI includes 0); 3 bps ≈ 0.000; PF 1.27 → 1.17 → 1.08 → 1.00.
   Decay by year at 2 bps: 2024 +0.158, 2025 +0.011, 2026 -0.040 (NIFTY); BANKNIFTY 2025 -0.103, 2026 -0.029; SENSEX 2025 +0.013, 2026 -0.051.
6. **Other instruments, same frozen config (no retune):** SENSEX OOS gross +0.144 R (CI +0.046..+0.26), net 2 bps +0.046; BANKNIFTY OOS gross +0.075 (CI includes 0), net 2 bps -0.005.
7. **MCX (NATURALGAS n=104, CRUDEOIL n=96, ~3.5 months):** gross +0.13 / +0.22 R but CIs span zero: INCONCLUSIVE, no claim.

## 5. What this means
- A brick-scale trend-persistence effect exists in index data (gross ~2.5-4 bps/trade, 2017-2024), consistent across NIFTY/SENSEX, but it is **about equal to realistic costs** (index futures round trip ≈ 2.5-3 bps incl. STT/exchange/GST/slippage — estimate, not measured) and it has **faded to ~0 in 2025-26**. Options trading (what autoscalp does) adds spread and theta; earlier work in this repo already showed index-level edges dying in option premiums. Option-premium validation here is NOT VERIFIED (only ~3 months of option 1m bars exist).
- Autoscalp paper log (≈200 AUTOSCALP trades, 6 underlyings, 6 weeks, many NULL net_pnl) is far too small to test a Renko filter: INSUFFICIENT SAMPLE.

## 6. Decision / next steps
- **Not wired into autoscalp** (no live decision path changed; NIFTY autoscalp stays frozen). Adding an unvalidated filter would be a false-signal risk.
- Safe, additive option if you want it: log the Renko trend state (dir/run/brick) as a shadow feature on each autoscalp entry for forward validation; re-test after ≥ 300 closed paper trades per underlying.
- Re-run on NIFTY futures 1m + real futures costs before any further work; MCX needs more history than 3.5 months.
- Reproduce: `python scripts/smart_bos_research.py baseline|signflip|oos`.
- Orderflow upgrade (Pine → orderflow signals) not attempted: Renko run-state could be tested as a context feature there only with the same split discipline.
