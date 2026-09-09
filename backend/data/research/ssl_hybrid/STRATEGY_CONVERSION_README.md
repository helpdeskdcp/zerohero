# SSL Hybrid PRO -> `strategy()` for the TradingView Strategy Tester

`SSL_Hybrid_PRO_strategy.pine` -- the indicator wired to `strategy.*` so the
tester computes a real win rate / PF / drawdown. **Signal logic is byte-for-byte
identical** to the indicator you tested; only order calls were added.

## What was added (nothing in the signal touched)

| area | indicator | strategy version |
|---|---|---|
| entry | `label.new` only | `strategy.entry("L"/"S")` on `buySignal` / `sellSignal`, `pyramiding=0` |
| exit | drawn lines | `strategy.exit` -- 1/3 at T1, 1/3 at T2, 1/3 at T3, shared ATR stop |
| SL->BE | none | optional: ratchet stop to entry after the first partial (`useBreakEven`) |
| opposite signal | none | optional `strategy.close` (`closeOnOpp`) |
| session flat | none | optional `strategy.close_all` at HH:MM (`flattenEOD`) |
| window | none | optional `fromDate`/`toDate` filter |
| costs | none | `commission 0.03%` + `slippage 2` in the header |

Toggle `useBreakEven` / `closeOnOpp` / `flattenEOD` OFF to test the **pure drawn
levels** (matches your own conversion); ON to match the Python backtest's
management.

## Two correctness notes for any `strategy.exit` thirds split

1. **`qty_percent` is a share of the position *at fill time*, not of the
   original.** Three calls at 33 / 33 / 34 do NOT cleanly partition -- after T1
   fills, "33%" of the *remaining* 67% is ~22%, leaving residual. Use the
   compounding idiom instead:
   ```
   strategy.exit("T1", qty_percent = 33.33, ...)   // 1/3 of full
   strategy.exit("T2", qty_percent = 50,    ...)   // 1/2 of the remaining 2/3 = 1/3
   strategy.exit("T3",                       ...)   // the rest
   ```
   This file already uses that form.
2. **Repaint-proofing.** Keep `calc_on_every_tick = false` (default for
   `strategy`). Turn ON **Bar Magnifier** in Strategy Properties for honest
   intrabar SL-vs-TP fills, otherwise the tester resolves ambiguous bars
   optimistically and inflates the win rate.

## Apples-to-apples with the Python backtest

Set: date range 2018-01-01 .. 2025-12-31, commission 0.03%, slippage 2,
Bar Magnifier ON, and pick the same timeframe. Expected: win rate ~40-55%,
PF < 1 -- i.e. the Python NO-GO, not 99%.

## Architecture note (from the user, agreed)

This strategy trades the **underlying index**. 100 index points != 100 option
premium points. The Python engine keeps them as separate layers:
index signal -> CE/PE strike selection -> option-premium entry / SL / target.
The index backtest measures whether the *signal* has an edge; the premium layer
is sized and tested on top only if it does.
