# EXPERIMENT LOG (all experiments, successful and not; rules fixed before running; OOS looked at after selection)
Data: 5-minute bars built from stored 1-minute index candles (NIFTY 2017-06..2026-10, BANKNIFTY/SENSEX 2021-09..2026-10). Trades = the baseline ZHX v1 trades (identical set for every ablation). Option conversion: delta 0.5, one lot, monitored cost model (brokerage, STT, GST, exchange, half-spread) + 2 extra brokerage orders. Theta/gamma not modelled.
| id | hypothesis | result | status |
|---|---|---|---|
|B0|ZHX v1 baseline|DEV+VAL pooled -Rs61/trade (n=1635); OOS negative on all 3 indices (gross R 0.006-0.026)|REJECT as a standalone strategy|
|F1|15m trend must not be against|OOS worse on NIFTY (-178) and SENSEX (-180)|REJECT|
|A_trend|ADX14>=20|DEV+VAL -89 (worse)|REJECT|
|A_range|ADX14<20|DEV+VAL -16 (kept by rule) but OOS by index NIFTY +105 / BANKNIFTY -19 / SENSEX -77: not consistent|INCONCLUSIVE|
|V_hi|ATR above 100-bar median|-23 (kept by rule); OOS hi_vol: -74 / -84 / -64 (no gain over baseline)|INCONCLUSIVE (selection effect)|
|V_lo|ATR below median|-119|REJECT|
|P_body|signal body >= 1.0 ATR|-25 (kept by rule)|INCONCLUSIVE|
|P_close|close in outer 25%|-61 (no change)|REJECT|
|T_ema|close on trade side of 5m EMA50|-62 (no change)|REJECT|
|T_h1|1h stack not against|-79 (worse)|REJECT|
|M_rsi|RSI14 on trade side of 50|-44 (+17, below the +30 bar)|REJECT|
|M_ex|RSI14 not exhausted|-73 (worse)|REJECT|
|S_score|score >= 7|-166 (much worse; higher score does NOT mean better)|REJECT|
|S_win|entry 09:35-10:30 IST|DEV+VAL +83 (n=269, kept by rule); OOS pooled +41, 90% interval -198..+290 (n=284), 42% of trades positive; by year: NIFTY negative in 2019, 2020, 2025, 2026, SENSEX negative in 2021, 2025, 2026|INCONCLUSIVE|
|COMBINED|AND of the 4 kept variants|only 3-8 trades per index in DEV/VALIDATION, 13-18 in OOS (NIFTY +898, BANKNIFTY +846, SENSEX -13, all intervals include zero)|REJECT (insufficient sample; textbook over-fit by stacking)|
Multiple-testing: 12 single variants + 1 combination + F1-F3 (earlier) -> several 'positive' cells are expected by chance. The selection rule (pooled DEV+VAL improvement >= Rs30 and n >= 150) is what was fixed in advance; nothing was re-tuned after seeing OOS.
Not tested (not available): volume engine (index volume = 0), OI/IV/Greeks inside Pine.
Files: `data/research/zhx/{ablation.py,ablation.json,swin_check.py,filters.py,filters.json,validate_e.py,validate_v1d.json}`.
