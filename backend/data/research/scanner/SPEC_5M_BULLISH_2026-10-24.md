# Owner's "BULLISH SCANNER — 5 MINUTE — BALANCED" — implementation and test spec (pre-registered 2026-10-24, before it is run on history)

## The scan (owner's text, verbatim logic)
CORE (AND): (1) close > open; (2) volume > SMA(volume, 20) x 1.5; (3) RSI(14) > 50; (4) close > EMA(close, 20).
GROUP A (liquidity sweep / reversal, AND): low < previous bar's low; close > previous bar's low; close > open.
GROUP B (resistance breakout, AND): close > previous bar's high; volume > SMA(volume, 20) x 1.5; RSI(14) > 55.
FINAL = CORE AND (A OR B). Purpose stated by the owner: bullish reversal / breakout candidates for further CE analysis.

## Implementation choices fixed now
- All indicators on the 5-minute series of the instrument (Chartink convention): RSI Wilder (alpha = 1/14), EMA span 20, SMA(volume, 20) INCLUDES the latest bar, "1 candle ago" = the previous 5-minute bar; the series is continuous across sessions (no reset at the open), as a Chartink intraday scan would compute it.
- Volume does not exist on a cash index. The instruments are therefore the FRONT-MONTH FUTURES (the contract with the largest volume of the day, same rule as the rest of the project): NIFTY, BANKNIFTY, SENSEX, NATURALGAS, CRUDEOIL, GOLDM. Index options are then analysed by the owner from the signal on the future; "CE analysis" is not modelled.
- Data: market_history.db market_candles 1-minute FUTURE bars (2026-06/07 .. 2026-10-23, about 80 sessions; volume present), aggregated to complete 5-minute bars. This is short: the history cannot settle anything long-term.

## Back-test (what the data allows)
Signal at the close of a 5-minute bar; entry = open of the next bar; exit = close of bar t+h, h in {3, 6, 12} bars (15 / 30 / 60 minutes), same session only; a signal within h bars of the last counted one (same instrument, same variant) is skipped. Round-trip cost: NIFTY / BANKNIFTY 1.0 bp, SENSEX 1.5, CRUDEOIL 3.0, NATURALGAS 4.0, GOLDM 3.0. Variants: FULL (the owner's final logic), A_ONLY (CORE and A), B_ONLY (CORE and B). Baseline: every bar, long.
Verdict per variant at each horizon on the pooled signals of all instruments: INSUFFICIENT_SAMPLE if fewer than 300 pooled signals; else GO-SHADOW only if the mean net return > 0, the day-block bootstrap CI lower bound > 0 at the Bonferroni level 0.05 / 3, both halves of the sample (by date) are positive, and it stays positive dropping any single instrument; at least 2 of the 3 horizons must pass. Otherwise NO-GO.

## Live
The same logic runs on each completed 5-minute bar of the six futures inside the autoscalp tick as an OBSERVATION-ONLY feed (own SQLite file, forward returns +15 / +30 / +60 minutes filled in), with a private Telegram line that states the back-test verdict. No order, no gate, no autoscalp decision input.

## Not claimed
No option premium, theta, strikes or fills; the short futures history means a pass would be weak evidence and a fail is not proof of no effect.

## Addendum, same day, before any run: the owner's BEARISH scan (mirror) — "BEARISH SCANNER — 5 MINUTE — BALANCED"
CORE (AND): close < open; volume > SMA(volume, 20) x 1.5; RSI(14) < 50; close < EMA(close, 20).
GROUP A (liquidity sweep / reversal, AND): high > previous bar's high; close < previous bar's high; close < open.
GROUP B (support breakdown, AND): close < previous bar's low; volume > SMA(volume, 20) x 1.5; RSI(14) < 45.
FINAL = CORE AND (A OR B). Purpose: bearish reversal / breakdown candidates for PE analysis. Same implementation choices (Chartink conventions, front-month futures with volume, same data, same costs). Short returns = -(exit/entry - 1).
Variants now K = 6: FULL_BULL, A_BULL, B_BULL, FULL_BEAR, A_BEAR, B_BEAR (the Bonferroni level becomes 0.05 / 6); baselines: every bar long and every bar short. Verdict rules unchanged (pooled >= 300 signals per variant, mean net > 0, CI lower bound > 0, both halves positive, positive without any single instrument, >= 2 of 3 horizons). The live feed carries both scans.

## Reading notes supplied by the owner (2026-10-24) and how they apply here
1. These are Chartink filter specifications, not text to paste into the scanner box; each condition belongs in Chartink's filter builder with the (A OR B) grouping set explicitly. Here nothing is pasted into Chartink: the logic is implemented directly in app/scanner/intraday.py with the grouping FULL = CORE AND (A OR B) written explicitly and tested (tests/test_scanner_intraday.py).
2. "Liquidity sweep" is only a proxy: a bar that trades beyond the previous bar's low / high and closes back inside it. It is not a measured sweep of resting orders.
3. True order flow (bid/ask delta, footprint, order-book imbalance) cannot come from these clauses; this scan sees price and volume only.
4. Stronger support / resistance needs separate swing-high / swing-low logic; the previous-bar high / low used here is the weakest possible level. A swing-level variant would be a NEW pre-registered variant (not added now).
5. Live use: the feed is observation / paper only; a scanner signal alone is not a trade. Every message says so.
