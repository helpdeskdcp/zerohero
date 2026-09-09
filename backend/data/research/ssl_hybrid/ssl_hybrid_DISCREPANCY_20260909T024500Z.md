# SSL Hybrid PRO -- "TradingView showed 99% wins, backtest shows ~45%" -- what's wrong

_2026-09-09. User asked: do NOT change the Pine logic, find the mistake. Answer: the
mistake is not in the port or the backtest accounting -- it is in how the number was
read off TradingView._

## 1. The Pine script has no strategy tester and no win/loss counter

It is `indicator(...)`, not `strategy(...)`. It contains only `label.new`, `plot`,
`plotshape`, `table`, `alertcondition`. There is **no `strategy.entry` / `strategy.exit`,
no trade simulation, no win counter, no win-rate cell**. "99% winning / 1% loss" is
therefore not a value this script computed -- it is a visual/manual read of the chart
labels.

## 2. Why the chart looks ~99% right but isn't

- **Repainting.** `var int ssl1State := close > ssl1High ? 1 : close < ssl1Low ? -1 :
  ssl1State[1]` plus `ta.crossover(ssl1Up, ssl1Down)` (where ssl1Up/Down swap on that
  state) recalculates on the live, unclosed bar. On a running chart a BUY label appears
  mid-bar; if price reverses before the close, `strongBull` goes false and the label
  vanishes. Only the setups where price kept going are ever seen. Bar-replay from
  scratch, or gating on `barstate.isconfirmed`, removes the illusion.
- **`max_labels_count = 500`.** TradingView keeps only the last 500 labels -- on 15m
  that is ~1-2 months. The judgement is on a tiny, recent, survivor-filtered slice.
- **No SL-before-T1 check by eye.** T1 is only `1.5 * ATR(14)` from entry. Price tags a
  level that close "eventually" on almost any chart; scrolling forward to see "did it
  reach T1" silently skips every case where SL hit first.

## 3. The backtest accounting is NOT the culprit

Same signal logic (unchanged), 7 different trade-accounting models:

| model | NIFTY 15m win% | BANKNIFTY 15m win% |
|---|--:|--:|
| my backtest (SL-first intrabar, thirds T1/T2/T3, session-end flat) | 41 | 41 |
| TV-optimistic bracket (whole @ T1; ambiguous bar = WIN; no time limit) | 44 | 43 |
| conservative bracket (ambiguous bar = SL) | 43 | 43 |
| first-touch-ever (no time limit, ambiguous = WIN) | 44 | 43 |
| whole @ T1 else opposite-signal / EOD, equidistant SL live | 44 | 43 |
| + SL -> break-even after +0.5R | 32 | 31 |
| + SL -> BE after +0.25R then SL disabled ("ride to T1") | 56 | 55 |

Also checked 60m, 240m and daily, 2010-2025: every model, every timeframe lands at
**44-60% win, net negative, PF 0.7-0.98**. A symmetric 1.5-ATR bracket on a trend-filter
entry cannot reach 99% without a genuine predictive edge -- that is arithmetically off
the table.

## 4. One real port-fidelity gap (does not rescue the strategy)

Our historical NSE cash-index data has **zero volume**, so:
- `volumeOK = volume >= ta.sma(volume,20) * 1.0` degenerates to `0 >= 0` -> **always
  true** -> the 5-pt volume component is always granted. On a volume-bearing TradingView
  symbol it is true ~50% of the time, so that chart fires *fewer* signals than this port.
- `ta.vwap` needs volume; with none we substitute a session-anchored cumulative HLC3
  mean (disclosed proxy). The real VWAP gate is stricter.

Both make the port a *looser* strategy than the volume-fed chart -- which would make the
backtest look *better*, not worse. Neither closes a 45% -> 99% gap.

## 5. How to settle it on TradingView

Convert the indicator to a real `strategy()` -- identical conditions, `strategy.entry`
on buySignal/sellSignal, `strategy.exit` with the ATR stop + T1 limit -- and run the
**Strategy Tester** over years of history with the bar-magnifier on and standard
commission/slippage. That win rate will match this backtest (~40-50%), not 99%.

## Verdict: unchanged -- NO-GO. No live wiring. `live_trading` stays false.
