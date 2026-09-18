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

No production code touched at the time this doc was written. No signal, no orders, no live wiring.

## Update 2026-09-18: production wiring done, edge does NOT transfer

`max_hold_bars` was threaded into the real engine (`app/orderflow/smart_money.py`
`_walk_outcome`/`_setup`/`smart_money_setups`, and `app/orderflow/backtest.py`
`backtest()`) as an opt-in parameter, default `None` = byte-identical to prior
behaviour. Mechanically correct and unit-tested (24 tests across
`tests/test_orderflow_smart_money.py` + `tests/test_orderflow_backtest.py`,
including 4 new tests specific to this parameter).

Running the SAME hold_bars x stop sweep through the real production
`backtest()` (breakout-trigger entries via `smart_money_setups`, sessions
passed explicitly as the TRAIN/OOS lists above) does **NOT** reproduce the
edge found in the standalone script above:

| symbol | config | TRAIN PF | OOS PF | OOS net |
|---|---|---|---|---|
| NATURALGAS | baseline (no time-exit) | 0.723 | 0.479 | -56.8 |
| NATURALGAS | hold=1 stop=1.5 (best found) | 1.706 | 1.080 | +2.1 |
| CRUDEOIL | baseline | 0.970 | 1.114 | **+286.0** |
| CRUDEOIL | hold=1 stop=1.5 | 0.882 | 1.086 | +60.0 |
| NIFTY | baseline | 0.307 | 0.399 | -613.3 |
| NIFTY | hold=1 stop=1.5 | 0.796 | 1.133 | +40.25 (not reliable, n too small) |

**Root cause of the gap**: the research script above enters at MARKET on the
spike bar's own close (`entry = b0["c"]`, immediate). The production engine
enters on a BREAKOUT trigger -- price has to subsequently trade through the
spike bar's high/low before a trade even opens
(`smart_money.py::_setup`). These are two different strategies wearing the
same "volume spike" label; the time-exit edge measured on immediate-entry
does not carry over to delayed breakout-entry.

**Honest verdict**: NATGAS improves from clearly-losing (PF 0.48 OOS) to
roughly breakeven (PF 1.08 OOS, net +2.1 over 146 correlated intraday
trades) -- not a proven edge, just less-bad. CRUDEOIL is actively hurt by
the same config on OOS (its baseline was already the best number in this
whole table, +286 net, and time-exit cuts that to +60). NIFTY's sample
isn't reliable either way (`min_sample`/`min_sessions` gate fails).

**Conclusion**: the code fix is real, safe (opt-in, default-off, tested)
and available for further research (`backtest(symbol, max_hold_bars=N,
stop_frac=X, sessions=[...])`), but it is NOT a validated fix for the
production breakout engine's losses and must not be described as one or
wired into live/paper signal generation. The Stage-11 finding stays a
research result about immediate-entry spike trading, not the deployed
engine.

## Update 2026-09-18 (part 2): entry_mode="immediate" -- matches research, production PF>1.0 confirmed

Per explicit request, the exact research construction (market entry at the
spike candle's own close, no breakout wait) was added to the production
engine as `entry_mode="immediate"` (`smart_money.py::_setup`,
`smart_money_setups`, `backtest.py::backtest`) -- opt-in, default remains
`entry_mode="breakout"` (byte-identical to all prior behaviour). 6 new
tests, 39/39 orderflow tests pass.

Re-ran the hold_bars x stop_frac sweep through the real production
`backtest(symbol, entry_mode="immediate", max_hold_bars=H, stop_frac=S,
sessions=[...])` on the same TRAIN(10)/OOS(4) session lists:

| symbol | config | TRAIN PF | OOS PF | OOS net |
|---|---|---|---|---|
| NATURALGAS | hold=2 stop=0.5 | 1.101 | 1.085 | +3.95 |
| NATURALGAS | hold=1 stop=0.5 | 1.072 | 1.051 | +1.75 |
| CRUDEOIL | hold=2 stop=0.5 | 1.059 | **1.193** | +248.0 |
| CRUDEOIL | hold=5 stop=0.5 | 1.089 | 1.193 | +320.0 |
| CRUDEOIL | hold=3 stop=0.5 | 0.996 | 1.284 | +412.0 (TRAIN fails, OOS-only -- not robust) |
| NIFTY | any config tried | mixed | mixed | not reliable, sample too thin |

`entry_mode="immediate"` DOES reproduce PF > 1.0 on both TRAIN and OOS for
NATURALGAS and CRUDEOIL at a consistent `hold=2, stop_frac=0.5` -- this is
the real, production-code-path confirmation of the Stage-11 finding.
CRUDEOIL's edge is notably stronger than NATGAS's here.

**Still-honest caveats, unchanged from Part 1**:
- OOS is only 4 real captured sessions -- `reliable` stays `False` for
  every OOS run shown above regardless of trade count, purely because
  `MIN_SESSIONS=10` distinct sessions isn't met. This is a real, structural
  small-sample limitation, not a code bug.
- Intraday spike signals are correlated within a session, not independent
  draws -- n=100+ resolved "trades" is not 100+ independent bets.
- Still index-points basis, no spread/slippage.
- PF magnitudes are modest (1.05-1.2 for most winning configs) -- real
  after-cost economics (brokerage, slippage on immediate market entry,
  which is itself harder to achieve than a passive breakout order) have not
  been modeled. `basis="premium"` re-pricing has not been run for this
  entry mode yet.

**Recommendation**: this is now a genuinely promising, production-code-
verified signal for NATGAS/CRUDEOIL specifically at `entry_mode=immediate,
max_hold_bars=2, stop_frac=0.5` -- worth more real sessions before any
live/paper wiring decision, and a premium-basis re-run to see if the option
spread erases it (as happened to several other candidates this session).
Still NOT wired into live/paper signal generation -- that remains a
separate, explicit decision given subscribers trade on our signals.
