# Rules-engine replay — policy 1.0.0 (DRAFT_UNVALIDATED) · strategy MANUAL · capital 200,000

- ledger: 151 closed trades over 31 days
- recorded (all):   n=151 sum=-26383 mean=-174.72 win=0.4702
- policy ALLOWS:     n=60 sum=-5592 mean=-93.2 win=0.4667
- policy BLOCKS:     n=91 sum=-20791 mean=-228.47 win=0.4725
- blocks by rule: {'R-SESSION-NSE': 37, 'R-PREMIUM-MIN': 25, 'R-COOLDOWN': 18, 'R-MAX-TRADES': 17, 'R-SESSION-MCX': 6, 'R-CALENDAR': 5, 'R-DUPLICATE': 3}
- NOT evaluable on this ledger (inputs absent, never guessed): ['R-EV', 'R-FEED-CONNECTED', 'R-FEED-FRESH', 'R-PREMIUM-MAX', 'R-RR', 'R-SPREAD']

Diagnostic only: sequential replay ignores path dependence; allowed trades' P&L is as recorded; this is not a forecast and not evidence of edge.