# ZEROHERO BASELINE AUDIT (2026-11-03)
Source: `backend/app/smart_chart/pine_scripts/zhx_v1.pine` (554 lines, Pine v6), git `2cc07a7` on `main`; development branch `dev/zerohero-edge-engine`; sha256 of the baseline backup `577b7172...f5ba` (`baseline_backup/`). Live broker execution: none exists in this path (ZHX writes recommendations only; Safe AI monitor = SHADOW).

## What ZHX v1 is
Stop-hunt engine: liquidity pools (PDH/PDL, previous-session H/L, equal highs/lows within 0.15 ATR, 15-min opening range, last swing) -> wick pierces a pool >= 0.05 ATR and CLOSES back -> displacement candle (body >= 0.7 ATR, close in outer 35%) within 3 bars -> score 0-10 (pool weight + body + FVG + BOS + premium/discount + HTF 15m/1h EMA bias + volume + EMA20) >= 6 -> BUY CE / BUY PE. SL beyond the sweep extreme +0.15 ATR (risk 0.4-3.0 ATR); T1/T2/T3 at the next pools (>= 1.0/1.8/3.0 R); legs 40/30/30; SL first on a shared candle; EOD square-off; cooldown 6 bars; max 3 signals/day; 5-minute chart only. Option-chart mode (only the bullish setup of the premium is a BUY).
Known limitations: no T4; no trailing SL (only optional break-even after T1); no daily loss limit; index volume is 0 on NSE/BSE indices so the volume term is always 0; no OI/IV/Greeks inside Pine (Pine cannot read them; Safe AI adds them outside); regime is not an explicit engine.
Known bugs found earlier and fixed: PineTS for-loop over empty arrays iterates descending (size guards added).

## Baseline performance (validated, `validate_v1*.json`, 5m bars from stored 1m index candles, option conversion via monitored cost model)
Gross R about 0 (NIFTY +0.02-0.07, BANKNIFTY about 0, SENSEX +0.02-0.24 by split); net per trade negative out of sample on all three indices. Verdict in `data/research/zhx/ZHX_REPORT.md`: NO DEMONSTRATED EDGE.

## 12-engine feature matrix (E=EXISTING P=PARTIAL M=MISSING B=BROKEN N=NOT AVAILABLE)
| # | Engine | Feature | Status | Note |
|---|---|---|---|---|
|1|Regime|trending/sideways, ADX|M|not in ZHX (exists in other engines of the repo)|
|1|Regime|ATR regime/high-low vol|P|ATR used for sizing only|
|1|Regime|reversal detection|P|the sweep+displacement IS a reversal trigger|
|2|SMC|BOS|E|bullBOS/bearBOS (score +1)|
|2|SMC|CHoCH, HH/HL/LH/LL labels|M|swings kept, not labelled|
|2|SMC|Order blocks|M||
|2|SMC|FVG|E|3-candle gap before displacement|
|2|SMC|Premium/discount|E|mid of last swing range|
|2|SMC|breakout/retest|M||
|3|Liquidity|buy/sell-side pools, EQH/EQL, sweeps, stop hunts|E|core of the script|
|3|Liquidity|sweep confirmation|E|close back + displacement|
|4|Price action|displacement candle, body strength, close location|E||
|4|Price action|engulfing, pin bar/rejection wick, failed breakout|M|only close-location is used|
|5|Trend|EMA20/50, HTF 15m+1h EMA bias|E|bias only adds +-1|
|5|Trend|VWAP, ADX/DI, RSI, Supertrend|M||
|6|Volume|relative volume|P|coded; index volume = 0 -> always off on indices|
|6|Volume|spikes/divergence/accum-dist|N|no volume on NSE/BSE index series|
|7|Momentum|RSI divergence, MACD, exhaustion|M||
|8|Options|CE/PE mapping, option premium chart mode|E||
|8|Options|Greeks, IV, OI, option volume|N in Pine (Pine cannot read them) / E in Safe AI monitor (captured chain)||
|9|Traps|bull/bear trap, fake breakout, failed retest|P|the sweep is the trap trigger; no separate failed-retest logic|
|10|Confluence|weighted score, thresholds, quality class|E|score 0-10, minScore input|
|10|Confluence|regime-dependent weights, duplicate protection|M||
|11|Risk|entry, SL, T1-T3, ATR risk bounds, partial exits, BE after T1, max trades, cooldown, EOD|E||
|11|Risk|T4, trailing SL, daily loss limit|M||
|12|Validation|historical trade labels, dashboard chart test|E|dashboard trades/win/PF|
|12|Validation|MFE/MAE, regime/session breakdown, walk-forward|M in Pine / done offline (this study)||
