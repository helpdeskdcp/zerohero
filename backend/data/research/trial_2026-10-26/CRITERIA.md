# One-week paper trial, 2026-10-26 .. 2026-10-30 — criteria written BEFORE the week (2026-10-25, owner's decision; paper only, no live orders)
Owner's rule: if the AI gives no signals during the trial, ZeroHero is shut down (and the owner moves to other business). Recorded as stated.
## Where "AI signals" are counted (important)
- The AI's work is the autoscalp engine: table ai_paper_trades in data/chanakya.db (last two weeks: 113 paper trades, about 10 per trading day: SENSEX, NIFTY, NATURALGAS, CRUDEOIL, GOLDM, some equity).
- The arena participant ZEROHERO_AI is separate and submitted only 1 signal in the same two weeks. Counting that one would trigger "zero signals" for the wrong reason. The trial counts ai_paper_trades, and also reports the arena participant separately.
## What the trial measures (fixed now)
1. Signals: number of autoscalp paper trades per session (expected 5-15 unless safeguards block; CRUDE/NG have net-edge suspension enforcement ON by owner's order on 2026-10-24 - if a symbol is suspended it will produce 0 by design and that is reported, not hidden).
2. Net result: sum and mean of NET P&L per trade after real costs (the net_edge monitor, not the stored r_multiple), with the trade count and a bootstrap interval. A week (~50 trades) cannot prove an edge: the interval will be wide.
3. Arena: every participant's P&L restarts at zero on 2026-10-26 (scoring window start moved; ledger untouched).
## Honest reading rules
- Signals present + net P&L negative or CI spanning zero => "the AI produces signals but no demonstrated edge" (this is what all earlier tests show). That is NOT the owner's shutdown condition (zero signals), but it is the real finding and will be reported as such.
- Net P&L positive with a CI above zero after one week would be the first positive result; it would still need to repeat for 30+ sessions before being believed.
- A week is one sample of luck. No decision on sizing, live trading or the championship ranking is derived from it.
