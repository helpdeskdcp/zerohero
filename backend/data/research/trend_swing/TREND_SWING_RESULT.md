# TREND-SWING (daily) — E1 trend hypothesis on NIFTY — result

_2026-09-09. Research/backtest only; no broker, no live wiring, `live_trading`
untouched. Existing engines NOT modified — `orderflow.indicators` imported
read-only for reuse._

## Question

The intraday runs (SSL Hybrid, orderflow v1, tri_compare E1) all showed the
trend edge getting *less bad* as the timeframe slowed (15m −0.21R → 1h
break-even). Does the **frozen E1 trend logic** — EMA stack + EMA200 slope +
Donchian breakout + ATR vol filter + anti-whipsaw (persistence + Kaufman
efficiency ratio + ADX) + ATR SL + trailing exit + vol-normalized sizing —
become a **real edge on the daily / swing horizon**, held overnight?

## Harness

- **Daily** NIFTY bars, one OHLC per IST session, 2010-01-04 … 2025-12-24
  (4098 bars, 16 years).
- Positions **held across days**. Entry at the **next bar's open** (no
  look-ahead). ATR hard stop + a chandelier/Donchian **trailing exit** + a
  confirmed **trend-flip** exit. Every trade risks **1R** (vol-normalized).
- **1 pt cost + 1 pt slippage** per round trip (matches tri_compare).
- Chronological **TRAIN 55 / VAL 20 / OOS 25** + a **purged** 6-fold expanding
  walk-forward (trades straddling a boundary + a 10-day embargo are dropped).
- Rules frozen before OOS. No look-ahead, no repaint, **no OOS optimisation**.
- Long + short **separately and combined**.
- Baselines: **buy-and-hold**, a simple **50/200 EMA cross** (long-only), a
  simple **Donchian(50)** breakout (long+short).
- **Crisis-window diagnostic** — TSMOM's core claim is "crisis alpha": R during
  the worst peak→trough index declines.
- Two parameter sets tried (baseline first, no tuning): the **frozen 15m
  params** verbatim, and an **a-priori daily-standard** set (Donchian 20, EMA
  20/50, ADX 15) — *chosen from textbook daily trend-following values, not
  fitted to results*.

## RESULT — **NO-GO** (both parameter sets, decisively)

### Combined (long+short), a-priori daily params

| slice | n | win% | expR | PF | net R | maxDD R | maxConsecL | Sharpe | exposure |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| ALL | 66 | 30.3 | **−0.244** | 0.51 | −16.1 | −17.0 | 11 | **−0.46** | 40% |
| TRAIN | 34 | 26.5 | −0.331 | 0.33 | −11.3 | −12.2 | 11 | −0.63 | 37% |
| VAL | 13 | 46.2 | +0.252 | 1.58 | +3.3 | −3.0 | 2 | +0.35 | 55% |
| **OOS** | 17 | 29.4 | **−0.392** | 0.25 | −6.7 | −7.6 | 6 | **−0.88** | 35% |

- **Frozen 15m params**: 75 trades, expR −0.18R, Sharpe −0.40, OOS Sharpe
  −1.13. Same verdict.

### Long vs Short

| | ALL n | ALL expR | ALL PF | ALL Sharpe |
|---|--:|--:|--:|--:|
| LONG only | 44 | −0.11 | 0.77 | −0.17 |
| **SHORT only** | 22 | **−0.51** | **0.06** | −0.54 |

Shorting a structurally-rising index with a daily trend rule is a disaster —
almost every short is a whipsaw into the uptrend.

### Walk-forward (purged, expanding) — **0 / 6 folds positive**

Every fold's expectancy is negative: −0.55, −0.12, −0.27, −0.10, −0.56, −0.23.

### By year — 5 mildly-positive of 16

2014, 2016, 2017, 2020, 2021 slightly green; 2011, 2013, 2015, 2022 clearly red.
Not broad, not consistent.

### Crisis alpha — **NEGATIVE**

21 peak→trough declines ≥ 10% within 90 days (incl. the 2020 −27% crash).
**E1 combined R during those windows = −12.45** (vs −3.48 outside). Buy-and-hold
lost −104R in the same windows — so E1 *did* lose less than B&H there, but it
still **lost money in the declines**. The "trend-follower makes money in
crashes" thesis **does not hold** for a single daily index series — E1 got
whipsawed on the way down in 2020, not positioned short into it.

### Baselines beat it

| strategy | all-period daily Sharpe | total R |
|---|--:|--:|
| **buy-and-hold** | **+0.67** | +84 |
| 50/200 EMA cross | +0.38 | +44 |
| Donchian(50) | +0.15 | +20 |
| **E1 trend-swing** | **−0.46** | −16 |

E1 is worse than doing nothing, worse than a dumb EMA cross, worse than a plain
Donchian.

## Verdict: **NO-GO**

Six independent reasons: all-period expectancy −0.24R over 66 trades · OOS daily
Sharpe −0.88 · 0/6 walk-forward folds positive · OOS expectancy −0.39R ·
crisis-alpha negative · loses to buy-and-hold OOS.

The OOS *trade count* (17) is below the 40 bar, but a daily single-index trend
system is inherently low-frequency (~4 trades/yr) — the verdict rests on the
4098-day return series, every walk-forward fold, the baseline comparison and the
crisis diagnostic, all of which point the same way.

## Diagnosis — why the trend hypothesis fails here

1. **It is not a timeframe problem.** 15m, 1h and daily all fail; daily is
   worse, not better. The "less bad as it slows" pattern topped out at
   break-even (1h) and reversed on daily.
2. **Single-instrument trend-following has no edge.** The TSMOM literature's
   edge comes from **(a) 40–100 diversified, uncorrelated markets** and
   **(b) crisis convexity from being positioned before slow crashes**. A lone
   NIFTY series provides neither: no diversification to smooth the ~55% loss
   rate, and the 2020 crash was too fast to catch. On one index, **buy-and-hold
   dominates every trend variant** — the index's own drift is the edge, and
   trading around it just pays costs and eats whipsaws.
3. The frozen E1 rules were tuned (implicitly) on 15m; on daily they are also
   too sparse (~4 trades/yr) — but even the a-priori daily-standard params give
   the same negative result, so it is not a parameter issue.

## What would be needed for a real trend test (not built)

A **multi-market** daily harness: NIFTY + BANKNIFTY + NIFTY futures + MCX
CRUDEOIL/NATURALGAS + (ideally) global index/bond/FX/commodity futures, each
vol-targeted, portfolio held at constant risk, ≥ 15 years. That is the setup
TSMOM actually needs. A single Indian equity index cannot test the hypothesis
fairly — and on that single index the answer is unambiguous: **NO-GO**.

Do NOT wire anything into live trading or the RK score.

_Reproduce: `python -m app.research_engines.trend_swing.backtest
--start 2010-01-01 --end 2025-12-31 --out data/research/trend_swing`
(frozen params) — or pass the daily-standard overrides shown in the config._
