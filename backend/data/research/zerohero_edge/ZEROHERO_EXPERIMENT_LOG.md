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

## Exit-rule experiments 2026-11-04 (pre-registered, same gate, `variants/`, `zhx/compare_variants.py`)
OOS net per trade Rs (n) [gross R]: baseline NIFTY -75 (398) [0.026] / BANKNIFTY -72 (368) [0.006] / SENSEX -56 (389) [0.019].
X1 SL to entry after T1: -96 (405) [0.010] / -71 (374) [0.007] / -61 (402) [0.021] -> REJECT (0/3).  X2 time stop 12 bars: -90 (433) [0.021] / -59 (393) [0.023] / -60 (427) [0.024] -> REJECT (0/3; SENSEX DEV +93 vs VALIDATION -102).
X3 T1 minimum 0.8R: -69 (398) [0.031] / -100 (368) [-0.003] / -72 (389) [0.010] -> REJECT (0/3).  X4 X1+X2: -92 (434) [0.021] / -66 (393) [0.014] / -58 (427) [0.029] -> REJECT (0/3).
Reading: exits move gross R by at most +-0.01; the lack of edge is in the ENTRY, not the exit. Nothing added to the script (dev v3 keeps the optional time stop OFF; v1 unchanged).
