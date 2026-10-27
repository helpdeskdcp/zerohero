# NXI - NIFTY Expiry Intelligence: setup and daily workflow

**Status of this deliverable (read first):** the script has been executed and rule-tested in a PineTS-compatible engine (see `NXI_VALIDATION.md`). It has **not been compiled in TradingView yet**; a TradingView compile and a Strategy-Tester run are the first things to do after you paste it. Paper / analysis only: no broker connection, no order API, no external data.

## Install
1. TradingView -> Pine Editor -> new script -> paste the contents of `nifty_expiry_intelligence.txt` (identical to the `.pine`) -> *Add to chart*.
2. Chart: **NSE:NIFTY (index), 5-minute**. On any other timeframe the script shows `RUN ON 5-MIN CHART` and takes no trade (its first-candle rules are defined on 5-minute bars).
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
