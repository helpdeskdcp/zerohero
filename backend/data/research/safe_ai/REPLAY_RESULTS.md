# Safe AI — 12-month NIFTY backtest (Upstox 1-minute index + expired weekly option candles)

Option expiries in data: 17 (2025-09-09 → 2025-12-30); index days 294. Costs: full arena charges; slippage = half-spread each way (0.25 / 0.5 / 1.0 %). 1 lot. Pre-registered grid, nothing tuned after seeing results.

| family | thrust | half-spread | ALL | DEV (to 31-Mar-2026) | HOLD-OUT (from 1-Apr-2026) |
|---|---|---|---|---|---|
| uturn | 0.35% | 0.25% | n=57 win 42% net ₹-18,785 (₹-330/trade, 95% [-686..-8]) PF 0.57 worst ₹-2,396 maxDD ₹22,222 | n=57 win 42% net ₹-18,785 (₹-330/trade, 95% [-686..-8]) PF 0.57 worst ₹-2,396 maxDD ₹22,222 | 0 trades |
| uturn | 0.35% | 0.50% | n=57 win 40% net ₹-21,664 (₹-380/trade, 95% [-738..-52]) PF 0.53 worst ₹-2,419 maxDD ₹24,780 | n=57 win 40% net ₹-21,664 (₹-380/trade, 95% [-738..-52]) PF 0.53 worst ₹-2,419 maxDD ₹24,780 | 0 trades |
| uturn | 0.35% | 1.00% | n=57 win 40% net ₹-23,331 (₹-409/trade, 95% [-769..-82]) PF 0.5 worst ₹-2,466 maxDD ₹26,027 | n=57 win 40% net ₹-23,331 (₹-409/trade, 95% [-769..-82]) PF 0.5 worst ₹-2,466 maxDD ₹26,027 | 0 trades |
| uturn | 0.50% | 0.25% | n=27 win 48% net ₹-7,111 (₹-263/trade, 95% [-720..161]) PF 0.62 worst ₹-2,252 maxDD ₹7,111 | n=27 win 48% net ₹-7,111 (₹-263/trade, 95% [-720..161]) PF 0.62 worst ₹-2,252 maxDD ₹7,111 | 0 trades |
| uturn | 0.50% | 0.50% | n=27 win 44% net ₹-9,460 (₹-350/trade, 95% [-797..74]) PF 0.53 worst ₹-2,274 maxDD ₹9,460 | n=27 win 44% net ₹-9,460 (₹-350/trade, 95% [-797..74]) PF 0.53 worst ₹-2,274 maxDD ₹9,460 | 0 trades |
| uturn | 0.50% | 1.00% | n=27 win 44% net ₹-10,286 (₹-381/trade, 95% [-823..43]) PF 0.5 worst ₹-2,318 maxDD ₹10,286 | n=27 win 44% net ₹-10,286 (₹-381/trade, 95% [-823..43]) PF 0.5 worst ₹-2,318 maxDD ₹10,286 | 0 trades |
| uturn | 0.70% | 0.25% | n=11 win 64% net ₹1,428 (₹130/trade, 95% [-601..774]) PF 1.26 worst ₹-1,855 maxDD ₹1,904 | n=11 win 64% net ₹1,428 (₹130/trade, 95% [-601..774]) PF 1.26 worst ₹-1,855 maxDD ₹1,904 | 0 trades |
| uturn | 0.70% | 0.50% | n=11 win 64% net ₹1,282 (₹117/trade, 95% [-615..759]) PF 1.23 worst ₹-1,873 maxDD ₹1,949 | n=11 win 64% net ₹1,282 (₹117/trade, 95% [-615..759]) PF 1.23 worst ₹-1,873 maxDD ₹1,949 | 0 trades |
| uturn | 0.70% | 1.00% | n=11 win 64% net ₹989 (₹90/trade, 95% [-644..729]) PF 1.18 worst ₹-1,909 maxDD ₹2,041 | n=11 win 64% net ₹989 (₹90/trade, 95% [-644..729]) PF 1.18 worst ₹-1,909 maxDD ₹2,041 | 0 trades |
| sweep | 0.50% | 0.25% | n=14 win 50% net ₹-3,870 (₹-276/trade, 95% [-1,005..424]) PF 0.65 worst ₹-2,480 maxDD ₹7,738 | n=14 win 50% net ₹-3,870 (₹-276/trade, 95% [-1,005..424]) PF 0.65 worst ₹-2,480 maxDD ₹7,738 | 0 trades |
| sweep | 0.50% | 0.50% | n=14 win 50% net ₹-4,072 (₹-291/trade, 95% [-1,020..410]) PF 0.63 worst ₹-2,504 maxDD ₹7,887 | n=14 win 50% net ₹-4,072 (₹-291/trade, 95% [-1,020..410]) PF 0.63 worst ₹-2,504 maxDD ₹7,887 | 0 trades |
| sweep | 0.50% | 1.00% | n=14 win 50% net ₹-4,477 (₹-320/trade, 95% [-1,049..382]) PF 0.6 worst ₹-2,553 maxDD ₹8,186 | n=14 win 50% net ₹-4,477 (₹-320/trade, 95% [-1,049..382]) PF 0.6 worst ₹-2,553 maxDD ₹8,186 | 0 trades |
