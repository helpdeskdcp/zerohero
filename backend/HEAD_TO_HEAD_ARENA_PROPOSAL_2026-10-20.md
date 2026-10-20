# Head-to-head arena: ZeroHero (AI) vs a human trader — fair-terms proposal (2026-10-20)

**Status of the original offer (live, Rs 50,000 each, one month, loser quits / wins Rs 10 lakh):** NOT accepted as framed. Reasons are in the covering message: (1) our own pre-registered evidence shows no edge before costs; (2) one month is a coin flip for either side; (3) live trading with third-party money needs registered structure and is outside what this system is configured or permitted to do (live gate stays OFF); (4) the "loser quits forever" stake rewards luck, not skill.

## Proposed arena (what would be a fair, informative contest)
- **Duration / sample:** at least 60 trading days AND at least 100 trades per side (month-long contests cannot separate skill from luck).
- **Capital and instruments:** identical notional (Rs 50,000), same instruments (NIFTY weekly options), same trading window, same cost model (strategy_lab / institutional_edge, with measured spreads where available).
- **Paper first:** both sides trade on the platform's paper ledger with timestamps taken by the system (no after-the-fact entries). Human entries go through a dedicated `ARENA_HUMAN` ticket; ZeroHero runs frozen (see FORWARD_TEST_PREREGISTRATION_2026-10-20.md).
- **Scoring (pre-agreed, written before day 1):** net return after costs; max drawdown <= 20% of capital (else disqualified); tie / overlapping 95% intervals = "inconclusive", not a win.
- **Referee and ledger:** an independent referee (not either camp) holds read access to the hash-chained ledger (`data/forward_test/ledger.jsonl`, `verify_chain()`); results published unedited, including losses.
- **Real money (optional, later):** only each side's OWN money, small size, own risk, own broker statement as the source of truth. No pooled or third-party capital; no managing someone else's account without the required regulatory registration (RA/IA/PMS) and the broker's algo-trading approvals.
- **Public statements:** both sides may only quote measured results from the ledger. No "AI beats registered trainers" or "human beats AI" claims beyond what the numbers support, with their confidence intervals.
- **What a result means:** a win in 60 days justifies a longer, larger stage, never "proven". A loss means the system, not AI as a whole, failed this test, and the engine is changed or retired.
