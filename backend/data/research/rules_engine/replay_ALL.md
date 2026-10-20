# Rules-engine replay — policy 1.0.0 (DRAFT_UNVALIDATED) · strategy ALL · capital 200,000

- ledger: 357 closed trades over 39 days
- recorded (all):   n=357 sum=-27821 mean=-77.93 win=0.4426
- policy ALLOWS:     n=136 sum=-3893 mean=-28.62 win=0.4632
- policy BLOCKS:     n=221 sum=-23928 mean=-108.27 win=0.4299
- blocks by rule: {'R-MAX-TRADES': 95, 'R-COOLDOWN': 56, 'R-SESSION-NSE': 37, 'R-PREMIUM-MIN': 27, 'R-CONSEC-LOSSES': 13, 'R-RR': 12, 'R-DUPLICATE': 6, 'R-SESSION-MCX': 6, 'R-CALENDAR': 5, 'R-CONCURRENT': 2}
- NOT evaluable on this ledger (inputs absent, never guessed): ['R-EV', 'R-FEED-CONNECTED', 'R-FEED-FRESH', 'R-PREMIUM-MAX', 'R-RR', 'R-SPREAD']

Diagnostic only: sequential replay ignores path dependence; allowed trades' P&L is as recorded; this is not a forecast and not evidence of edge.