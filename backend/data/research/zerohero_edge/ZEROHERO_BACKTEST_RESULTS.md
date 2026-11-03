# BACKTEST RESULTS - ZHX v1 baseline (min score 6), 5-minute bars, costs included (VERIFIED computations, EXPERIMENTAL strategy)
Net per trade in Rs for one ATM weekly option lot (delta 0.5), n = trades, [90% day-cluster interval], PF = profit factor net, MaxDD in Rs.
| Index | Split | n | gross R | net/trade | interval | PF | MaxDD |
|---|---|---|---|---|---|---|---|
|NIFTY|DEV 2017-21|644|+0.073|-129|[-217,-34]|0.79|87,579|
|NIFTY|VALIDATION 22-23|316|+0.088|-52|[-201,+106]|0.93|40,536|
|NIFTY|OOS 2024-26|398|+0.026|-75|[-246,+110]|0.92|99,855|
|BANKNIFTY|DEV 2021-22|185|-0.013|-100|[-343,+155]|0.90|49,482|
|BANKNIFTY|VALIDATION 23|137|+0.053|+30|[-276,+340]|1.04|18,200|
|BANKNIFTY|OOS 2024-26|368|+0.006|-72|[-274,+134]|0.93|79,682|
|SENSEX|DEV 2021-22|210|+0.122|+10|[-177,+206]|1.01|30,414|
|SENSEX|VALIDATION 23|143|+0.236|+80|[-121,+292]|1.14|12,219|
|SENSEX|OOS 2024-26|389|+0.019|-56|[-232,+122]|0.93|77,280|
MFE/MAE (baseline, in R, from the 5m bars between entry and exit): NIFTY MFE mean 1.70 / median 1.08, MAE mean 1.29 / median 1.08, median 105 min in trade; BANKNIFTY 1.62 / 0.95, 1.28 / 1.07, 90 min; SENSEX 1.70 / 1.14, 1.33 / 1.07, 112 min. (MAE above 1R comes from gaps through the stop and bars after SL touched inside the same candle.)
CE vs PE (OOS net/trade, n): NIFTY CE -182 (216) / PE +51 (182); BANKNIFTY CE -91 (215) / PE -46 (153); SENSEX CE -144 (200) / PE +37 (189). Regime (OOS): trend vs range NIFTY -168 / +105, BANKNIFTY -103 / -19, SENSEX -45 / -77. Session (OOS): 09:15-10:30 NIFTY -126, BANKNIFTY +51, SENSEX +44; 10:30-13:00 -26 / -148 / -81; 13:00-15:30 -69 / -144 / -135.
Not available in this backtest: win-rate by exact consecutive-loss counts and per-trade slippage beyond the half-spread model; gap risk only through the stop-fill-at-open rule of the Pine script.
Verdict: gross edge about 0 before costs; after costs negative OOS on all three indices -> REJECT as a standalone money-making strategy.
