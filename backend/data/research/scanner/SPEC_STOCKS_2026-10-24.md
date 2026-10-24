# Daily scan library on a large-cap STOCK universe — pre-registered spec (2026-10-24, before any stock is scanned on history)

## Why
The owner's Chartink scans are stock scans ({cash}). On six instruments (3 indices with 1,300-5,000 bars, 3 futures) the 36 scans have too little data to separate edge from noise (most are INSUFFICIENT_SAMPLE or fail the halves test). A 53-stock universe over 2019-2026 gives about 100,000 stock-days and hundreds of signals per scan.

## Universe and data
53 liquid NSE large caps: ADANIENT ADANIPORTS APOLLOHOSP ASIANPAINT AXISBANK BAJAJ-AUTO BAJFINANCE BAJAJFINSV BEL BHARTIARTL BPCL BRITANNIA CIPLA COALINDIA DRREDDY EICHERMOT ETERNAL GRASIM HCLTECH HDFCBANK HDFCLIFE HEROMOTOCO HINDALCO HINDUNILVR ICICIBANK INDUSINDBK INFY ITC JIOFIN JSWSTEEL KOTAKBANK LT M&M MARUTI NESTLEIND NTPC ONGC POWERGRID RELIANCE SBILIFE SBIN SHRIRAMFIN SUNPHARMA TATACONSUM TATASTEEL TCS TECHM TITAN TRENT ULTRACEMCO WIPRO INDIGO MAXHEALTH (TATAMOTORS has no single instrument after its demerger). This is a list of today's large caps, NOT a claim about index membership on any past date. Daily bars from Upstox historical candles (adjusted for splits / bonus by the source), 2019-01-01 to 2026-10-23; a stock enters when it has >= 130 bars (JIOFIN, ETERNAL, INDIGO, MAXHEALTH, TRENT etc. start later).
Known bias, stated up front: SURVIVORSHIP / SELECTION. Today's large caps are, by construction, stocks that did well enough to be large; long-side results on this list are biased upward. The market-neutral control below removes the common drift but not this selection effect; a pass is therefore weaker evidence than a fail.

## Test (same engine and scans as the index test: the 36 library scans, sides fixed)
Signal on bar D; entry = open of D+1; exit = close of D+h, h in {1, 3, 5, 10}; a signal within h bars of the last counted one of the same scan on the same stock is skipped. Return r = C(D+h)/O(D+1) - 1 in bp.
PRIMARY metric = market-neutral excess net of cost: side x (r_stock - r_universe) - cost, where r_universe is the equal-weight mean of r over all universe stocks with valid bars for the same entry / exit dates (the trade is "stock vs the basket"). Round-trip cost 15 bp (cash delivery STT + charges + slippage, assumed); 5 bp (futures-like) and 25 bp reported as sensitivity, not gated. Descriptive only: raw side-adjusted net return, to show how much the drift matters.

## Verdict per scan and horizon (candidate horizons 3, 5, 10; 1 descriptive); K = 36
INSUFFICIENT_SAMPLE if fewer than 500 counted signals. A horizon PASSES only if ALL hold: mean excess net > 0; month-block bootstrap (blocks = calendar months of the signal date, all stocks together because same-day signals are correlated) 95 % CI lower bound > 0 at the Bonferroni level 0.05 / 36; the mean is positive in both halves of the sample (by date); the mean is positive in at least 5 of the 8 calendar years 2019-2026 that have signals; and it stays positive after dropping the 5 stocks with the largest total contribution. GO-SHADOW if >= 2 of the 3 candidate horizons pass, else NO-GO.

## Live (decided now, independent of the result)
The daily run adds the stock universe after the NSE close (a separate STOCKS group) and logs hits to the same hits table; one Telegram digest per run (counts per scan, at most 25 lines) that states the back-test verdict. Observation only; no order path.

## Not claimed
Costs are assumptions; execution at the next open for 53 names is not modelled; a scan hit is a candidate, not a trade.
