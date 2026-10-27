# NXI - NIFTY Expiry Intelligence: setup and daily workflow

**Status of this deliverable (read first):** the script has been executed and rule-tested in a PineTS-compatible engine (see `NXI_VALIDATION.md`). It has **not been compiled in TradingView yet**; a TradingView compile and a Strategy-Tester run are the first things to do after you paste it. Paper / analysis only: no broker connection, no order API, no external data.

## Install
1. TradingView -> Pine Editor -> new script -> paste the contents of `nifty_expiry_intelligence.txt` (identical to the `.pine`) -> *Add to chart*.
2. Chart: **NSE:NIFTY (index), 5-minute** (the reference setup), or a **3-minute chart** for the optional entry refinement (the 5-minute engine is then read from the last completed 5-minute bar; see "3-minute refinement" below). Charts above 5 minutes are rejected (`CHART MUST BE 5-MIN OR LOWER`), 2- and 1-minute charts use the 3-minute path.
3. Keep the chart on regular session hours (09:15-15:30 IST). The script uses the exchange clock; do not change the chart timezone expectations (exchange time).
4. If TradingView reports a compile error, paste the exact message back - the known risk list is in `NXI_VALIDATION.md`.

## What you must supply every morning (Pine cannot fetch it)
| item | where | why |
|---|---|---|
| Previous-session option-chain OI strikes (highest + 2nd Put, highest + 2nd Call; optional 2 extra zones) | *Previous-session OI zones* group | Pine cannot read option-chain OI. The dashboard shows **MANUAL OI INPUT** and flags the data as stale if the OI date is not the previous session. Without them the script uses the previous-day high/low as price zones only |
| OI date + option expiry text | same group | validity display only |
| Expiry-session override | *General* group | the weekly-expiry weekday is an input (default Tuesday); on holiday-shifted weeks tick the override |
| CE and PE contract symbols | *CE / PE premium* group | Pine cannot scan the chain or choose a contract. Empty = `OPTION DATA UNAVAILABLE` and the premium component is excluded from the score |
| (optional) verified real OI/IV direction | *Evidence weights* group | set `Bullish`/`Bearish` only if you have real data; default `None` disables the component |

## Daily routine
1. Before 09:15: enter the OI strikes (and expiry), pick the CE and PE symbols, set the expiry override if needed.
2. 09:15 candle closes -> dashboard shows gap class, first-candle read, provisional scenario. **No entry is possible on the first candle.**
3. 09:20 candle closes -> the scenario is validated or withdrawn (`NO TRADE (2nd candle did not validate ...)`).
4. From then on a signal needs: a trigger (seller-stress pattern and/or validated gap scenario + structure), an evidence index beyond the threshold with enough non-zero components, and every gate open (volatility, time window, risk limits, distance to the nearest opposing level).
5. Alerts: create a TradingView alert on the strategy with *Condition = NXI, "Order fills and alert() function calls"*. Messages start with `NXI|CE ENTRY`, `NXI|PE ENTRY`, `NXI|TARGET 1 hit`, `NXI|EXIT`, `NXI|SESSION LOSS LIMIT HIT`.

## Paper-trading workflow (no live orders)
* The Strategy Tester trades the **index** in index points (qty 10 = 10 index units). It answers "was the directional idea good?", **not** "what would the option have paid".
* For the option, read the dashboard rows *CE/PE premium* and *Premium plan*: premium trend, ATR-based stop and 1R/2R premium levels from the **option chart's own data**. Index points do not translate 1:1 into premium points (delta, gamma, theta, spread, IV).
* Place nothing automatically. Costs in the declared strategy: 0.015 % per side + 10 ticks slippage (edit in the `strategy()` header; they are index-proxy assumptions, not option costs).

## Reading the dashboard
Rows: decision / why-or-blocked / entry-SL / targets / **evidence index (not a probability)** / components / probability status / HTF votes / gap / scenario / first and second candle / opening range / previous-day levels / support and resistance zone status / OI data status / structure and sweep / seller-stress / CANDLE/VOLUME PROXY / CE-PE premium / premium plan / data quality (volume, VWAP vs TWAP, timeframe, NEWS AI NOT CONNECTED, REAL ORDER BOOK NOT CONNECTED) / risk state / expiry session. `Compact mode` drops the long rows for phones. Position (9 places), text size, colours, transparency and border are inputs. The backtest panel (bottom-left) is optional.

## Rollback
Remove the script from the chart. Nothing else is installed or stored.

## 3-minute refinement (optional)
1. Put the script on a **3-minute** NSE:NIFTY chart (keep *Chart timeframe handling* on `Auto`).
2. *Entry timing* group -> *Entry refinement mode*: `Off` (default) = enter at the next 3-minute bar open after the 5-minute signal; `Micro-breakout` or `Pullback then resume` = wait up to *Bars to wait* (default 4) chart bars for a lower-timeframe trigger. Armed setups that do not trigger are dropped, not chased.
3. Dashboard rows to watch: *Data quality* (must say "5m engine read one completed bar back", not `ENGINE TIMING MISMATCH`), *Entry timing* (armed / confirmed / expired / cancelled counters).
4. Expect the first 5-minute candle (09:15) to become visible at the 09:21 bar and every later 5-minute decision to arrive up to 3 minutes after that 5-minute bar closed - this lag is the price of never reading a partial 5-minute bar.
5. Strategy Tester results on a 3-minute chart are not comparable with the 5-minute chart: different fills, different bars per day, stops measured on 3-minute bars. See `NXI_VALIDATION.md` for the measured comparison (refinement did not improve the result).
