# Winners Scalper (15m trailing) on real option premiums — FINDINGS 2026-10-25 (spec 66f715c; scripts/winners_scalper_options.py; results_options.json; trade lists options_trades_*.csv)
ATM weekly CE (BUY) / PE (SELL), 1 lot, entries/exits at 15-minute boundaries, slippage 0.5 / 2.0 pt per side, real regulatory costs. 2024-10 .. 2026-10.

## Verdict: NO-GO in all 4 cells
| cell | n | mean net Rs/trade | gross | cost | 95 %-style CI (0.0125) | halves | win | by year (2024/25/26) |
|---|---|---|---|---|---|---|---|---|
| NIFTY DTE>=1 | 361 | +5 | +73 | 68 | [-503, +523] | -132 / +142 | 36 % | +470 / -218 / +144 |
| NIFTY all days | 452 | -44 | +21 | 65 | [-461, +416] | -227 / +139 | 37 % | +542 / -357 / +189 |
| SENSEX DTE>=1 | 364 | +19 | +87 | 68 | [-407, +496] | -183 / +222 | 38 % | -416 / -159 / +359 |
| SENSEX all days | 467 | +61 | +126 | 65 | [-374, +522] | -146 / +267 | 36 % | +343 / -324 / +441 |
- The mean is about zero, the interval is +/- Rs 500, the first half is negative and the second positive in every cell: no stable edge. Costs (about Rs 65 per trade) take most of a small gross.
- Control: the same trades at index level with bar-close exits earn only +2.4 bp (NIFTY) / +1.2 bp (SENSEX) after cost in this 2024-26 window - consistent with the index-level fade seen since 2022 (the +0.10 R full-history figure leaned on 2015-2021, 2020 above all).
- Worst single trade about -Rs 7,500 per lot (premium collapse on a stopped-out expiry-week trade).
Conclusion: the Pine signal, in the form the arena trades it (option premiums), shows no edge over the last two years. Nothing is wired for automatic arena submission. The owner can still use the TradingView alerts for manual paper trades on the AVINASH profile; the numbers above are the honest expectation (about zero).
