# Inside-Bar 2m -- improvement variant sweep

_2026-09-08 · NIFTY + BANKNIFTY · 2016-2025 · walk-forward OOS = most recent 30% of sessions · config-only overrides, no fitted parameters · RESEARCH ONLY_

Ran 13 principled variants of the inside-bar rule set to see whether any
configuration clears the GO bar (OOS expectancy > 0, profit factor >= 1.3,
>= 2 positive calendar years -- on **both** indices).

| variant | NIFTY OOS (n / expR / PF / +yrs) | BANKNIFTY OOS (n / expR / PF / +yrs) |
|---|---|---|
| baseline (as specified) | 1004 / -0.056 / 0.90 / 1 | 912 / -0.194 / 0.68 / 0 |
| stop 0.25 ATR past IB | 975 / -0.016 / 0.97 / 4 | 883 / -0.119 / 0.79 / 2 |
| **stop 0.50 ATR past IB** | 938 / **+0.027** / **1.05** / 6 | 849 / -0.096 / 0.82 / 3 |
| momentum >= 2.0 ATR | 433 / -0.090 / 0.84 / 2 | 378 / -0.175 / 0.70 / 0 |
| momentum >= 3.0 ATR | 109 / -0.098 / 0.82 / 3 | 102 / -0.072 / 0.87 / 3 |
| breakout window 2 bars | 936 / -0.035 / 0.94 / 4 | 835 / -0.180 / 0.70 / 1 |
| tight contraction <= 0.55 | 603 / -0.024 / 0.96 / 2 | 550 / -0.180 / 0.70 / 1 |
| breakout confirm 0.15 ATR | 882 / -0.059 / 0.89 / 2 | 792 / -0.205 / 0.66 / 0 |
| entries only before 10:30 | 189 / -0.107 / 0.81 / 0 | 192 / -0.082 / 0.85 / 0 |
| combo: mom2.0 + stop0.25 + tight0.60 | 307 / +0.018 / 1.04 / 5 | 283 / -0.133 / 0.75 / 2 |
| combo: window2 + confirm0.15 + stop0.25 | 789 / -0.040 / 0.92 / 4 | 676 / -0.144 / 0.75 / 2 |
| no scale-out, pure +2R target | 1006 / -0.063 / 0.91 / 2 | 907 / -0.210 / 0.71 / 0 |
| baseline + 0.05R cost | 1004 / -0.105 / 0.82 / 0 | 912 / -0.244 / 0.61 / 0 |

## GO candidates: **NONE**

No variant produces OOS expectancy > 0 with PF >= 1.3 on both indices.

## What the sweep tells us

- **The only lever that moves the needle is the stop distance.** Widening the
  stop to 0.5 ATR beyond the opposite IB extreme flips NIFTY OOS to marginally
  positive (+0.027R, PF 1.05, positive in 6 of 10 years). But: (a) PF 1.05 is
  far below the 1.3 bar -- this is break-even, not an edge; (b) that stop is
  ~2x the inside-bar's own range, which contradicts the spec ("stop just
  outside the opposite end of the Inside Bar"); (c) BANKNIFTY stays clearly
  negative (-0.096R, PF 0.82) under every single variant.
- **Tightening entry quality (stronger momentum, tighter contraction, bigger
  breakout confirmation, morning-only) does not help.** It cuts sample size
  and leaves expectancy negative -- the losing trades that survive the filter
  are just as bad. At momentum >= 3.0 ATR the OOS sample collapses to ~100
  trades and is still -0.10R on NIFTY.
- **Realistic cost kills it.** One-twentieth of an R in slippage/fees takes
  every variant deep negative.
- **BANKNIFTY never clears zero** in any configuration. Its higher tick
  volatility means the inside-bar breakout is even less predictive there.

## Verdict (unchanged): **NO-GO**

The inside-bar 2m breakout has no exploitable directional edge on NIFTY or
BANKNIFTY over 10 years, and no principled parameterisation rescues it. The
one near-break-even result (NIFTY, 0.5-ATR stop) is single-symbol, sub-bar,
and violates the strategy's own stop rule. Do not promote. `live_trading`
remains false; nothing was wired.
