# Rules-engine replay — policy 1.0.0 (DRAFT_UNVALIDATED) · strategy AUTOSCALP · capital 200,000

- ledger: 199 closed trades over 21 days
- recorded (all):   n=199 sum=+9 mean=0.04 win=0.4322
- policy ALLOWS:     n=84 sum=+178 mean=2.12 win=0.4524
- policy BLOCKS:     n=115 sum=-169 mean=-1.47 win=0.4174
- blocks by rule: {'R-MAX-TRADES': 66, 'R-COOLDOWN': 37, 'R-CONSEC-LOSSES': 12, 'R-RR': 10}
- NOT evaluable on this ledger (inputs absent, never guessed): ['R-EV', 'R-FEED-CONNECTED', 'R-FEED-FRESH', 'R-PREMIUM-MAX', 'R-SPREAD']

Diagnostic only: sequential replay ignores path dependence; allowed trades' P&L is as recorded; this is not a forecast and not evidence of edge.