# Inside-Bar 2m -- "did we implement it wrong?" (vs the YouTube / web teaching)

_2026-09-08 · checked our engine against how the inside-bar strategy is actually taught (howtotrade.com, quantvps.com, TradingView, Brooks) · NIFTY + BANKNIFTY 2016-2025 walk-forward OOS_

## Short answer: no, the implementation is faithful. The strategy just has no edge on 2m.

Our engine follows the user's written spec exactly (20-EMA side filter, stop
just outside the **opposite end of the Inside Bar**, breakout within 2-3
bars, 1:2/1:3/1:4 targets, scale out at 1R/2R, <=4 trades/day, flat by 13:00).
That is the "**aggressive**" version in the canonical teaching.

The web/YouTube version differs on three points. We added each as an option
and tested it:

| canonical rule we were missing | result (OOS, both indices) |
|---|---|
| entry + stop at the **mother bar** extremes (wider risk), not the inside bar | NIFTY −0.065R PF 0.88 · BANKNIFTY −0.074R PF 0.86 — less bad, still negative |
| stop at the **50% of the mother bar** | NIFTY −0.25R · BANKNIFTY −0.37R — much worse |
| **ADX > 20 / 25** trend-strength gate | NIFTY −0.024R · BANKNIFTY −0.196R — no help |
| breakout confirmed by a **full bar close** beyond the level | NIFTY −0.040R PF 0.93 · BANKNIFTY −0.104R PF 0.81 |
| all three combined (mother + close + ADX20) | NIFTY −0.047R · BANKNIFTY −0.081R |

**GO candidates after all of this: NONE.**

## The one thing that WAS wrong -- in the new code, and I caught it

My first cut of the `breakout_confirm: "close"` path had a **look-ahead bug**.
It filled at the intra-bar breakout level *and* then simulated that same
confirming bar's high/low -- a bar we already know closed in our favour. That
printed a fake edge:

```
canon_close_confirm  (with the bug):   NIFTY OOS  +0.331R  PF 1.92  win 64%  maxDD  -8R
canon_close_confirm  (causality fixed): NIFTY OOS  -0.040R  PF 0.93  win 47%  maxDD -46R
```

Fix: a close-confirmed signal is only known after the bar closes, so the fill
and the P&L simulation now start on the **next** bar, filled at the confirming
close. Added `test_close_confirm_is_causal` to lock this. After the fix the
"edge" is gone -- it was entirely the time-travel.

## Verdict (unchanged): NO-GO

Every honest variant of the inside-bar 2m rule set -- aggressive or
conservative, with or without ADX, touch or close confirmation -- is negative
out-of-sample on NIFTY and BANKNIFTY over 10 years. The sources themselves say
sub-15-minute inside bars are "low reliability"; the data agrees. Nothing was
wired to live; `live_trading` stays false.

Sources: howtotrade.com/chart-patterns/inside-bar-pattern, quantvps.com/blog/inside-bar-breakout-strategy, tradingview.com "Inside Bar Breakout Strategy [with EMA Filter]", brookstradingcourse.com scalping-2-minute-emini-chart.
