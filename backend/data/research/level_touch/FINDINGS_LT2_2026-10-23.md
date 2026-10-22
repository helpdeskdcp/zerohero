# H-LT2 breakout vs reversal after a level touch — findings (2026-10-23)
Spec: SPEC_LT2_2026-10-23.md (committed before any result). Code: run_lt2.py. Data and periods as H-LT1 (NIFTY 2015-2025, BANKNIFTY 2015-2026-04, P1 2015-19, P2 2020+). ~240-300k touch events per instrument-period.

## As specified (targets anchored on the LEVEL): the headline is an artefact
Class effect (closed BEYOND the level vs REJECTED back inside) is +31..+37 points in every cell and its CI excludes 0 — it formally passes the spec's CLASS SEPARATES bar. But PLAIN price levels, which carry no information, show the same +31..+34 points. Cause (a flaw in my spec, found after the result): the race starts at the touch-bar close, which for BEYOND sits above the level and for REJECTED at/below it, so the CONT target is nearer in one case and the REV target in the other. That is geometry, not information. I do not count it.
Level effect as specified (structural minus plain, same class): BEYOND +0.6..+1.7 points, REJECTED -0.7..-2.1 points. All below the 3-point bar -> NO LEVEL EFFECT.

## Addendum (written after the above, labelled): symmetric race anchored on the touch-bar CLOSE (run with LT2_ANCHOR=CLOSE, results_lt2_close_anchor.json)
Targets +/-0.5 sigma10 around the close, so the race is fair by construction.
- Class effect: plain -5.8/-1.5 (NIFTY P1/P2), -2.5/+0.6 (BANKNIFTY P1/P2); structural -1.3/+2.2 and -2.3/+0.8. Sign flips between periods and instruments, everything within +/-6 points -> NO SEPARATION: whether the touch bar closed beyond or back inside the level does not predict continuation vs reversal.
- Level effect: BEYOND +0.1/+0.9 (NIFTY), -0.8/-0.0 (BANKNIFTY); REJECTED -4.5/-2.8 (NIFTY), -1.0/-0.2 (BANKNIFTY). Only NIFTY/REJECTED clears 3 points (structural levels reverse a bit less after a rejection than plain price), and it does not replicate on BANKNIFTY -> NO CONSISTENT LEVEL EFFECT.

## Verdict
NO SEPARATION / NO LEVEL EFFECT on a price-only basis for 10-minute-scale touches of swing, previous-day and opening-range levels in NIFTY/BANKNIFTY. A breakout-or-reversal rule built on "touch + close beyond/inside" has no price-level support in 11 years of data. Consistent with the earlier NO-GOs (breakout, reversion, sweep).
What remains usable: the H-LT1 touch-probability table as a target/stop-distance and veto input. Nothing here is an edge; option premium, spread and costs are not modelled; NG/Crude/GOLDM/SENSEX have no comparable long 1-minute history and stay shadow-only.
