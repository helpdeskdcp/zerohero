# Order-flow candidate OF-1 — specification, committed BEFORE any outcome is computed (2026-10-20)

**Disclosure.** I have seen: (a) the frozen AUTOSCALP's aggregate results (gross ~ -0.34%/trade), (b) that AUTOSCALP has almost stopped trading since the 2026-09-29 filter (only ~7 signals since, all TRENDING_DOWN; so there are ZERO AUTOSCALP trades on days that have Dhan tick data, which is why OF-1 cannot be a filter on them), (c) tick coverage counts per symbol/day. I have NOT computed any order-flow signal outcome. Parameters below are the pre-existing defaults of `app/pa_of_lab/orderflow.py`, not tuned.

## Candidate
- **Instruments:** front-month FUTURES ticks (`data/dhan_orderflow_ticks.db`, kind FUTURE): NIFTY, BANKNIFTY, NATURALGAS, CRUDEOIL. (SENSEX/BSXFUT excluded: too few ticks.)
- **Bars:** 5-minute bars built by `app/pa_of_lab/ticks.py` (dedupe / late / stale / gap policies; levels via the live `FootprintBar`). Aggressor side is INFERRED (quote rule / tick rule), never broker-confirmed.
- **Signals:** `app/pa_of_lab/confirm.of_signals` = ABSORPTION (lookback 20, volume >= 1.5x average, |delta| >= 30% of volume, range small, next bar confirms) and EXHAUSTION (lookback 12, new extreme with CVD divergence + reversal close). A signal needs bars i and i-1 with data-quality OK (closed, >=30 ticks, >=60% quote-rule classified, <=10% unknown side, no feed gap) inside one gap-free segment.
- **Trade (research simulation on the FUTURE, long or short by signal side):** entry at the OPEN of the bar after the signal bar; stop and 2R target from the signal (`rr=2`); stop-first on the 5m bar; time exit after 12 bars (60 min); flat at session end (NSE 15:15, MCX 23:15); one position at a time per symbol.
- **Cost:** 6.3 bps round trip for index futures (strategy_lab cost model since 2026-04-01). For MCX futures the cost model is NOT validated: the same 6.3 bps is used as a labelled placeholder.

## What is reported
Number of signals and trades, per symbol and day; gross bps per trade, net bps per trade, win rate (net>0), mean and median hold, exits by reason; data-quality coverage (share of bars with OK quality, days with data, how many signals were blocked by quality).

## Decision rules (pre-registered)
- **Now (offline, ticks since 2026-09-29):** DESCRIPTIVE ONLY. With only a handful of sessions per symbol no inference is made, whatever the numbers look like.
- **Forward (from the first live session after the Dhan feed is back):** evaluated cumulatively. `ACCUMULATING` until n >= 60 trades AND >= 20 distinct trading days. Then `PASS_FOR_NEXT_STAGE` iff mean NET bps > 0, day-clustered one-sided p <= 0.05, AND the Wilson 95% lower bound of the win rate exceeds the break-even win rate of the realised payoffs; else `FAIL`. A pass licenses only a larger paper stage; nothing is ever called proven. Frozen: any change to the order-flow code or parameters starts a new epoch.
- This candidate is never connected to orders; it does not influence AUTOSCALP, the arena or the rules engine.
