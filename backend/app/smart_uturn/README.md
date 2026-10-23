# Smart U-Turn Quant Engine (Python port of Pine v6 "Smart U-Turn Decision Engine v2.1 | Calibrated")

Research, historical replay and paper only. **No order placement exists in this package** (live execution stays disabled).
Version string: `smart_u_turn_py 2.1.0` (`app/smart_uturn/config.py`). Original script copy: `pine/smart_u_turn_v2_1.pine` (the chat transcript lost the Pine indentation; the owner's TradingView account holds the authoritative original). Nothing outside `app/smart_uturn/` was modified.

## Commands (run from `backend/`, project venv)
```
venv/bin/python -m app.smart_uturn.cli validate --input one_minute.csv --out out/            # quality report + feature availability
venv/bin/python -m app.smart_uturn.cli resample --input one_minute.csv --out out/ --tfs 1,3,5,15,30,45,D,W
venv/bin/python -m app.smart_uturn.cli signals  --input one_minute.csv --out out/ --chart-tf 5   # BUY/SELL counts + dashboard data
venv/bin/python -m app.smart_uturn.cli backtest --input one_minute.csv --out out/ --chart-tf 5   # trades/equity/summary/report
venv/bin/python -m app.smart_uturn.cli features --input one_minute.csv                            # which fields are really available
./scripts/run_tests.sh tests/test_smart_uturn.py -q                                               # 47 synthetic tests
venv/bin/python data/research/smart_uturn/run_replay.py one_minute.csv LABEL [P1|P2|ALL]        # the pre-registered replay
```
Input: ONE 1-minute file, columns `datetime,open,high,low,close[,volume,oi,iv,bid,ask,bid_qty,ask_qty]` (`sample/sample_one_minute_SYNTHETIC.csv`, `sample/config.sample.json`). `--ts-is close` shifts close-stamped candles to open time; `--session nse|mcx1130|mcx1155`.
Chart timeframes supported by the Pine mapping: 1, 3, 5, 15, 30, 45, 60, 120, 240, D, W (HTFs 3/5/15/30/45/60/120/240/D/W/M/3M/6M are all built from the same 1-minute data by one `CandleStore`).

## Pine rule -> Python
| Pine | Python | Notes |
|---|---|---|
| s02 TF map | `config.PINE_TF_MAP` | verbatim, tested |
| s03 `f_turn` (wick, sweep, momentum, break, trend, score, setup, pulse) | `strategy.turn_components` | `ta.lowest(low[1],n)` = min of the n bars before the bar; `na` compares are false; first-bar pulse as in Pine |
| s03 `f_trend` | `strategy.trend_series` | |
| s03 `request.security(tf, f()[1], lookahead_on)` | `strategy.mtf_values` | value of the previous COMPLETED HTF bar; truncation-invariance test proves no look-ahead |
| s04 votes, `mtfDirection`, `evA/B/C` | `strategy.run` | |
| s06 strict trend, independent cooldowns, blocked warnings | `strategy.gate_signals` | `lastBuyBar/lastSellBar` move only on emitted signals |
| s07 entry/stop/T1-T3/support/resistance | `strategy.run` (levels) | `entry := close`; `minDist = max(atr*0.5, 2*mintick)`. `ta.lowest/highest(.., swingLen)` sit inside the signal `if`, so Pine evaluates them over the last swingLen SIGNAL bars, not candles: default `swing_window="PINE_CONDITIONAL"` reproduces TradingView, `"BARS"` is the intent |
| s08 breakout/retest | `strategy.run` | uses the levels frozen at the last signal |
| s09 pivots, `valuewhen` hunts | `indicators.pivot_*`, `valuewhen_recent` | pivot reported only `swing` bars after the peak |
| s10 dashboard, s11 alerts | `strategy.dashboard`, signal columns (`buy_signal`, `blocked_buy`, `bull_breakout`...) | data only |
Pine's `ta.ema` seeds with the first value, `ta.rma/rsi/atr` seed with an SMA of the first `n` values (documented in `indicators.py`). A flat series gives RSI 100 (Pine's own formula).

## Data fields (what is real)
OHLC always; `volume` is treated as NOT supplied when all zero (index data), and `use_volume=True` then raises instead of reading zeros. OI is a snapshot series (never summed; derived bars keep the last snapshot). IV, bid/ask, quantities exist only if the source file has them: otherwise `UNAVAILABLE` in `feature_availability.json`; `features_options.py` refuses to compute anything from missing fields, mixes of expiry/strike, or zero bids. Nothing is derived from OHLCV to fake IV or order-book data.

## Backtest assumptions (the Pine script has none; all configurable in `BacktestParams`)
Signal known at its bar close -> fill at the NEXT bar's open; stops fill at the stop or, on a gap, at the worse open; targets are limit orders filled AT the target; a candle touching the stop and any target counts the STOP first; entries that gap through the stop or past T1 are refused and counted; flat at the session's last bar; one position at a time; THIRDS exit (1/3 each at T1/T2/T3, rest stopped at the original stop); cost 2 index points per round trip (an assumption, not a measurement); 1% risk of 500,000 sizing, no compounding. `SIGNAL_CLOSE` (Pine's own `entry := close`) is available only as a labelled optimistic variant.

## Status
| item | status |
|---|---|
| Unit/integration/anti-look-ahead tests (synthetic) | PASS: 47/47 (`tests/test_smart_uturn.py`) |
| TradingView signal parity | **PARTIAL** (2026-10-23, screenshots + complete Upstox 1m data): dashboard states, SL-hunt pivots and 5 of 9 SELL labels match exactly; TradingView shows 4 extra SELL labels the port does not (unresolved). See `data/research/smart_uturn/FINDINGS_PARITY_2026-10-23.md` |
| Pivot tie rule, `ta.lowest` early-bar behaviour | NOT VERIFIED against TradingView (documented in `indicators.py`) |
| Historical replay | see `data/research/smart_uturn/` (spec first, results after) |
| Provider adapters (Dhan/Angel One/Upstox) | interface + CSV adapter + fallback/backoff only; broker SDKs are NOT called from this package |
| Production readiness | **NOT claimed** |

## Known limitations
Index-level replay only: the owner trades options, whose premium, spread and theta are not modelled. Costs are assumed. Weekly/monthly HTFs need long history. 30m/45m/60m bars end with a 15-minute bar at 15:15 (NSE session of 375 minutes), as in TradingView for NSE. MCX presets exist (`mcx1130`, `mcx1155`) but are untested against real MCX data. No tick-level ordering: same-candle collisions are resolved against the trade.
