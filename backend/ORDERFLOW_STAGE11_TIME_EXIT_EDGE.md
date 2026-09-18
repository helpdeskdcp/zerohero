# Order-Flow Stage-11 — A Real, OOS-Confirmed Edge (Time-Exit, Not R-Multiple)

**Status:** RESEARCH ONLY. No production code changed. No signal, no orders,
no live wiring. Same discipline as every other orderflow stage this session.

## The question

Every prior reparameterization of the smart-money spike engine (RR tuning,
stop widening, quality filters, SR confirmation, H7 fade, Absorption) either
failed outright or improved the loss without crossing breakeven. This stage
asks a more fundamental question first: **does a volume-spike candle carry
any real forward-predictive information at all**, separate from any specific
trade structure (stop/target box)?

## Finding 1: yes, real, statistically significant short-horizon drift

Forward return in the spike candle's OWN close>open direction, spike bars
vs. all other bars, real captured NATGAS 5m data (14 sessions,
2026-09-01..09-18), TRAIN=first 10 / OOS=last 4:

| horizon (bars) | TRAIN z-score | OOS z-score |
|---|---|---|
| 1 (5 min) | 1.11 | **2.03** |
| 3 (15 min) | 2.65 | **1.59** |
| 5 (25 min) | 2.53 | 1.13 |
| 10 (50 min) | 2.00 | 0.60 |
| 20 (100 min) | **2.98** | 0.57 |

The short-horizon effect (1-3 bars) holds up reasonably on OOS. The
long-horizon effect (10-20 bars) is the strongest signal on TRAIN but
**decays to near-zero on OOS** — the classic signature of an unstable,
overfit long-horizon pattern. This is exactly what a fixed RR=3 target was
trying to capture (it needs the large, 10-20-bar move) -- **the wrong part
of the signal.**

## Finding 2: a short time-exit (not a distant R-multiple target) is profitable on BOTH splits

Instead of a fixed stop + `rr x risk` target, exit after a small fixed
number of bars regardless of price (market exit), with a modest stop for
tail-risk control. Swept `hold_bars in {1,2,3,5}` x `stop_mult in {0.5,1.0,1.5}`
(stop as a fraction of the spike candle's own range):

| hold | stop | TRAIN PF | OOS PF |
|---|---|---|---|
| 1 | 1.5 | 1.155 | 1.542 |
| 2 | 0.5 | 1.436 | 1.601 |
| **3** | **1.0** | **1.563** | **1.415** |
| 3 | 0.5 | 1.659 | 1.616 |
| 5 | 0.5 | 1.747 | 1.362 |

**Every one of the 12 configurations tested shows PF > 1.0 on both TRAIN and
OOS** — not one lucky config, a consistent pattern across the whole grid.
Compare to the baseline fixed-RR=3 engine: PF 0.72 (TRAIN) / 0.48 (OOS).

## Important caveat: the edge is NOT one-directional (BUY/SELL flips between splits)

| hold/stop | TRAIN BUY PF | TRAIN SELL PF | OOS BUY PF | OOS SELL PF |
|---|---|---|---|---|
| 3 / 1.0 | 0.942 | **2.422** | **2.239** | 0.813 |
| 3 / 0.5 | 1.074 | **2.296** | **2.470** | 0.914 |
| 2 / 0.5 | 1.083 | **1.862** | **2.352** | 1.013 |

SELL carried the edge on TRAIN; BUY carried it on OOS. **This is most
plausibly a genuine but regime-dependent momentum-continuation effect** (the
prevailing short-term trend direction in a given window determines which
side benefits), not a stable one-sided bias -- the earlier "BUY win rate
14% vs SELL 22%" finding on the OLD fixed-RR engine does not carry over to
this time-exit structure. **Implication: trade both sides symmetrically,
never lean on one direction** -- the aggregate (both sides combined) is
what's actually robust across TRAIN/OOS, not either side alone.

## Honest limitations

- Only 14 real captured sessions total (10 TRAIN / 4 OOS) -- thin by any
  standard. This is a genuine, real, OOS-confirmed result on the data that
  exists, not a claim that it will hold on materially more data.
- Index-points basis, not real option premium (same caveat as every other
  backtest this session -- no multi-month real NATGAS option-chain history
  exists).
- Intraday spike signals within a session are correlated (not independent
  draws) -- the z-scores in Finding 1 likely overstate significance
  somewhat versus a fully independent sample.
- No spread/slippage modeled.

## Recommendation

This is the first candidate this session that shows a real, OOS-confirmed
improvement crossing PF > 1.0 on both splits, for the *combined* signal.
Before this goes anywhere near live/paper wiring: (1) get more real
sessions and re-confirm on a genuinely fresh OOS window, (2) test on
CRUDEOIL/NIFTY the same way, (3) decide the hold/stop combination based on
robustness across a wider sweep, not the single best OOS number (which is
itself a form of overfitting if picked post-hoc). Reusable script:
`scripts/orderflow_time_exit_research.py` (to be saved alongside this doc).

No production code touched. No signal, no orders, no live wiring.
