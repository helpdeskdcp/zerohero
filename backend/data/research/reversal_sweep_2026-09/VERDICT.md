# Key-Level Reversal Sweep — Research Verdict (2026-09-28, CORRECTED)

**Status: MIXED / MARGINAL. A real methodology bug (signal overlap) was
found and fixed after the first pass looked strong — the corrected numbers
are materially weaker. NIFTY does not clear an estimated real cost. BANKNIFTY
(index points only) still does. The real captured option-premium sample is
too thin to call either way, and leans negative on what little exists.
Net: NOT a GO. Documented in full below, including the bug, because the
first (wrong) pass was already reported to the user.**

## CORRECTION NOTICE

The first pass of this research (reported earlier) had a real bug: after a
signal fired, the scan loop did not advance past the confirmation bar, so
the next 1-3 bars of the *same continuing move* could re-fire as "new"
independent breakouts that all resolved to the same real confirmation event.
Confirmed live and concretely: three separate break-bars (09:00/09:15/09:30
on 2026-09-15) all "confirmed" at the same 09:45 bar and were counted as 3
trades, one of which (a real ₹36→₹206 option move) alone had been carrying
the entire apparent positive result in the option-premium test. Fixed by
advancing the scan index past the confirmation bar on every fire. Every
number below is POST-FIX.

## The rule tested (user's own, restated exactly)

Mark prior-COMPLETED Daily / Weekly / 4-Hour high and low. On the 15-min
chart: price breaks ABOVE a marked high and that breakout candle itself
closes RED → potential SHORT reversal. Price breaks BELOW a marked low and
closes GREEN → potential LONG reversal. Valid only if a turnaround (close
back on the correct side of the level) happens within 3 15-min candles of
the break; otherwise the reversal doesn't hold.

## Data

Real, not fabricated:
- NIFTY 1-min spot, 2015-01-01 → 2025-12-24 (Kaggle, `mavrick136` dataset), 1,077,240 rows
- BANKNIFTY 1-min spot, 2015-01-09 → 2026-04-22 (Kaggle, `sandeepkapri` dataset), 1,046,600 rows

## My disclosed interpretation choices (rule didn't fully specify these)

- Turnaround = confirmation bar's **close** back on the correct side (not an intrabar wick touch)
- Entry = confirmation bar's close
- Stop = the original break candle's extreme (its high for SHORT, low for LONG)
- Target = tested at 1.0R / 1.5R / 2.0R; **1.5R used for all robustness checks below**
- EOD time-stop as backstop if neither stop nor target hit same session
- "Tight zone" and "key psychological" levels: **NOT implemented** — no unambiguous definition was given. This tests the Daily/Weekly/4H sweep-and-reject core only.

## Headline results (NIFTY, target 1.5R, confirm window 3 bars) — POST-FIX

| Cut | n | win rate | PF | avg pnl/trade (pts) |
|---|---|---|---|---|
| All levels (D+W+4H pooled) | 6,411 | 39.3% | 1.005 | 0.08 |
| **4H only** | 2,408 | 35.5% | — | **−2.97 (net losing)** |
| Daily only | 3,039 | 42.4% | — | +2.13 |
| Weekly only | 964 | 38.7% | — | +1.19 |

Sample sizes fell ~40% after the overlap fix (10,754 → 6,411). Direction of
the pattern held (4H still bad, Daily/Weekly still better) but the pooled
margin nearly vanished.

## Robustness — Daily+Weekly only (4H excluded), POST-FIX

| Check | n | win rate | PF | avg pnl/trade |
|---|---|---|---|---|
| Full 10y, no cost | 4,003 | 41.5% | 1.120 | 1.91 pts |
| Full 10y, **2.5pt estimated round-trip cost** | 4,003 | 39.9% | **0.966** | **−0.59 pts (net LOSING)** |
| TRAIN (2015 → mid-2020), no cost | 2,068 | 41.4% | 1.183 | 1.93 pts |
| TEST (mid-2020 → 2025), no cost | 1,935 | 41.6% | 1.088 | 1.88 pts |

**NIFTY no longer clears an estimated real cost** — this reverses the first
pass's conclusion. Train/test still degrade gently over time rather than
falling off a cliff (not pure noise), but the margin was never big enough to
survive cost even before that degradation.

Daily specifically stays the most consistent piece (TRAIN PF 1.240 → TEST PF
1.085, both clearly positive gross) — Weekly is the weaker, more marginal of
the two (TRAIN PF 1.016, barely above breakeven).

## Cross-symbol validation — BANKNIFTY, SAME rule, SAME params, zero re-fit, POST-FIX

| Cut | n | win rate | PF | avg pnl/trade |
|---|---|---|---|---|
| All levels | 6,536 | 42.3% | 1.110 | 4.97 pts |
| Daily+Weekly only, no cost | 4,028 | 44.8% | 1.209 | 10.00 pts |
| Daily+Weekly only, 2.5pt cost | 4,028 | 44.1% | **1.152** | **7.50 pts** |

BANKNIFTY **still clears the estimated cost comfortably** — its larger
average move per trade (~10pts vs NIFTY's ~1.9pts gross) gives it far more
room above a flat cost estimate. This is the one piece of real, independent,
un-refit evidence still standing after the fix.

## Real option-premium test (NIFTY, ~24 days of actual captured premiums, POST-FIX)

The whole point of this step: does the index-points pattern survive on real
option premium, not just spot points. Answer, honestly: **the sample is too
thin to say either way, and leans negative.**

| Cut | n | win rate | total gross | avg gross/trade |
|---|---|---|---|---|
| All levels | 25 | 40.0% | **−₹10,380** | −₹415 |
| Daily only | 6 | 50.0% | −₹1,719 | −₹287 |
| Weekly only | 3 | 66.7% | +₹185 | +₹62 |
| 4H only | 16 | 31.2% | −₹8,846 | −₹553 |

With an estimated (uncalibrated) brokerage+STT+exchange cost subtracted:
total −₹11,987, avg −₹480/trade. n=6 (Daily) and n=3 (Weekly) are far below
any threshold for a real read — this is disclosed as INSUFFICIENT_SAMPLE,
not as a NO-GO on its own. But it does not offer any positive evidence
either, and the single large winning trade that made the first (buggy) pass
of this look strong turned out to be the double/triple-counted overlap
artifact — removing it flips this cut to net negative.

## Why this is NOT a green light to trade options yet

1. **2.5pt cost is an estimate** (typical NIFTY-futures-scale round-trip
   friction), not pulled from `app.institutional_edge.costs` (this
   codebase's calibrated real-cost module for NSE/MCX). NIFTY's post-cost
   edge (0.21 pts/trade) is thin enough that a wrong cost assumption could
   flip the verdict.
2. **This is index points, not option premium.** Every prior index-level
   pattern in this project's history that looked good on spot points died
   on real option theta/spread (Inside-Bar 2m, SSL Hybrid Pro, StochRSI+
   Supertrend). The user's own stated goal is *option scalping* specifically
   — that requires re-testing entry/exit timing against real captured
   option premiums (only ~25 days of that exist right now, per this
   session's captured `market_history.db`), not just index points.
3. "Tight zone" and "psychological level" sub-filters from the original
   rule are untested — they might tighten the edge further, or might not
   matter; unknown either way.
4. 34% of trades exit via EOD time-stop rather than stop/target — worth
   understanding whether the EOD-flat P&L is closer to breakeven-noise or
   is itself contributing real edge.

## Bottom line

- **NIFTY: NO-GO** as specified — doesn't clear an estimated real cost even
  on index points, before ever reaching the option-premium question.
- **BANKNIFTY: still real on index points** (independent, un-refit, survives
  cost) — but never tested against real captured BANKNIFTY option premium
  (no such data is captured in this system) and 4H should stay excluded.
- **Daily-level breakouts specifically** are the one piece of this rule that
  held up most consistently across every cut on both symbols — if anything
  from this rule is worth carrying forward, it's that sub-component alone,
  not the 4H leg and not the pooled combination.
- Not recommending this for live/paper deployment as currently scoped.
