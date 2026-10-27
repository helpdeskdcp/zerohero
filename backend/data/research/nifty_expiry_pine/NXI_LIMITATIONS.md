# NXI known limitations and unavailable-data report

## Not possible in Pine Script alone (so not implemented, and shown as such on the dashboard)
| requirement | status | what the script does instead |
|---|---|---|
| Real option-chain OI / IV / greeks history | **UNAVAILABLE in Pine** | manual strike/OI inputs, labelled `MANUAL OI INPUT`; never derived from price or volume; OI alone is never read as direction |
| Automatic CE/PE contract discovery / chain scanning | **UNAVAILABLE** | you pick the two symbols; empty = `OPTION DATA UNAVAILABLE` and the premium component is excluded |
| Real order book / bid-ask depth | **NOT CONNECTED** | stated on the dashboard; candle/volume proxies are labelled `CANDLE/VOLUME PROXY` |
| News / sentiment AI | **NOT CONNECTED** | stated on the dashboard; no component uses it |
| Index volume | usually **zero** on NSE:NIFTY charts | volume features (relative volume, volume confirmation, VWAP) switch off and the VWAP row becomes `TWAP (no volume)`. They are excluded from the evidence denominator, never estimated |
| Calibrated probability | **NOT AVAILABLE in-script** | `PROBABILITY NOT CALIBRATED` until 50 resolved virtual signals per side, then a running in-sample hit rate with a Wilson interval (explicitly not a forecast). Brier/reliability exist only in the offline report |
| 3-minute entry refinement | **NOT IMPLEMENTED** | the 5-minute engine cannot be re-derived on a 3-minute chart without re-building every 5-minute rule on aggregated bars; a lower-timeframe request from a 5-minute chart would not align (3 does not divide 5). Listed as future work, not faked |
| Out-of-sample testing inside the Strategy Tester | **NOT POSSIBLE** | the statistics panel splits entries by a date input (before / from); a real walk-forward needs the offline report |
| Historical option P&L | **NOT POSSIBLE** | the Strategy Tester trades the index in points. Option P&L depends on strike, expiry, IV and spread and is not simulated |

## Model assumptions you should know
* **Expiry weekday is an input** (default Tuesday = current NIFTY weekly expiry). The script's *logic* does not depend on it (it only tags and labels). History before Sep-2025 was Thursday; the offline report applies the correct rule by date.
* **Historical backtests contain no OI zones** (there is no historical OI in the chart): history uses the previous-day high/low as price zones only. The manual OI mode is therefore **live-only and untestable on history**.
* **Costs**: 0.015 % per side + 10 ticks slippage are index-proxy assumptions in the `strategy()` header, not NSE option costs. A stop can be gapped through; the simulated loss is not a guaranteed maximum.
* **Fills**: signals evaluate at bar close, orders fill at the next open. A stop/target pair touched inside one bar is resolved by the Strategy Tester's own intrabar assumption (and by "stop first" in the in-script probability tracker).
* **Daily ATR** is derived from the sessions the chart has loaded (needs >= 5 sessions). The first week after loading is `UNKNOWN`.
* **The first and second candle are fixed events** (09:15 and 09:20 bars). Late data, a missing first bar or a half-day session degrade the gap logic to `NO TRADE`.
* **Seller-stress is a pattern label.** The script does not know anyone's positions, stop-losses or intentions; the largest OI strike is not assumed to be the strongest level.
* **HTF context** uses the last completed HTF bar. It is therefore always at least one HTF bar old by design (that is what makes it non-repainting).
* **Pine limits** to check in TradingView: 64 plots (48 used), 40 `request.security` calls (6 used), label/box/line counts set in the header, script size (84 KB source). TradingView may also complain about things the PineTS engine accepts - see the compile risk list in `NXI_VALIDATION.md`.

## Engine differences (why PineTS results are not TradingView results)
PineTS is an independent re-implementation of Pine semantics. Differences that can change numbers: higher-timeframe bar construction at session edges, `strategy.exit` intrabar ordering, commission/slippage rounding, partial-exit quantity rounding, `ta.*` warm-up. Treat every number in `NXI_VALIDATION.md` as "PineTS engine, stored NIFTY 1-minute data re-aggregated to 5-minute", to be re-checked in TradingView.
