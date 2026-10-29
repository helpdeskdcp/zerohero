# ZeroHero Safe AI — design, research and honest limits (2026-10-29)

Participant `ZEROHERO_SAFE_AI`, paper only, visible on the leaderboard, labelled "risk-first, paper, no edge claimed". Code: `app/safe_ai/` (`signals.py` rule, `live.py` runner + safety stack, `replay.py` test harness).

## 1. What this is NOT
This repository has already tested price-only rules to death (see memory notes `why-nothing-has-edge`, `candle-ai-nogo`, `inside-bar-2m-nogo`, `ssl-hybrid-nogo`, `level-touch-gap-nogo`, ...): index moves of 4–8 bp per 5 minutes against option round-trip costs mean a price-only rule needs ~55–72 % hit rate to break even, and nothing tested reached it out of sample. The Safe AI therefore **claims no edge**. Its job is to be the most *careful* participant: few trades, small size, hard stops, locks, full audit trail — and to produce forward data so the rule can be judged honestly (or switched off).

## 2. What Mohit (INSTITUTION_TRADER) actually does — measured from his 38 posts (`mohit_profile.py`, `mohit_profile.json`)
* Timing: 29 of the 38 are index options (SENSEX / NIFTY) and **20 of those 29 were posted between 09:15 and 10:15 IST**; the other 9 are MCX evening posts (NATURALGAS, one GOLDM, one CRUDEOIL).
* Strike / expiry: near the money (median |strike − spot| 0.38 % of spot, max 1.05 %), median premium ₹210; **18 of the 29 index posts are on the expiry day or the day before** (SENSEX Thursday, NIFTY Tuesday).
* Direction: of the 20 index posts where the preceding 15-minute index move is known, **17 were AGAINST that move**; 13 posts were made with the index sitting exactly at the session extreme (low or high so far), and the 8 of those that settled gave 7 wins (+₹28,268 net) — i.e. he mostly buys the turn after an opening thrust.
* Risk set-up: stop median 31 % of the premium (range 1–94 %), first target only 0.81 R; 2 of 38 without a stop; 24 of 38 at 2 lots.
* Result under the arena's rules (ask in / bid out, charges): 24 settled, 18 wins, net +₹46,438, PF 4.3, worst loss −₹6,937 (14 % of the ₹50,000 capital). Small sample, 7 sessions: the 95 % interval of the win rate is roughly 55–89 %.
The method (not the exact calls) is a contrarian *reversal at the session extreme, early in the day, near-the-money weekly options, modest target*. We do not know his selection; we only measure it.

## 3. Expert methodology consulted (web search, 2026-10-29)
* Expiry-day mechanics: gamma/theta are concentrated; an ATM option near expiry can move from delta ~0 to ~1 inside a small index range, OTM options barely respond even if the index moves your way — so ATM only, and not late in the day (broker explainers, e.g. Groww / Pocketful / Sahi / PL India; none published a tested hit rate).
* Preparation levels: previous-day high / low and the highest-OI zones as reversal areas (same sources, no backtest).
* Sweep / rejection of prior highs-lows is described as "context, not a prediction engine" by the indicators that implement it.
* Sizing and ruin: fixed-fractional risk of 1–2 % per trade, fractional Kelly only with a measured edge (we have none → we do not use Kelly), halting rules at pre-set loss thresholds, and "no sizing fixes a zero-edge system" (risk-of-ruin explainers). **No source offered evidence of an edge for any opening-reversal option strategy.**
Conclusion used: keep the *shape* Mohit shows (turn at the extreme, ATM, modest target) but add confirmation, remove the late-day theta trap, shrink size, and lock the day on losses.

## 4. The rule (`signals.detect`) — a CONFIRMED U-turn at the session extreme
DOWN-thrust → buy ATM CALL, UP-thrust → buy ATM PUT (long options only, never short):
1. thrust ≥ 0.50 % (variants 0.35 / 0.70 pre-registered) from the high before the session low to the low, the extreme is 8–90 minutes old;
2. price has recovered 35–80 % of the thrust (turn proven, room left);
3. no new extreme in the last 6 minutes (higher low);
4. price above its value 3 and 8 minutes ago (momentum).
Window 09:35–14:30 IST (expiry day of the traded option: until 13:30).

## 5. Safety stack (`live.py`) — all refusals are logged with a reason
kill switches `SAFE_AI_LIVE=0` / `SAFE_AI_SUBMIT=0` (decide + log only) · never after the contest · exchange open · fresh index price (≤ 20 s) · **one loss ends the day for that index; day net ≤ −₹6,000 ends it for all** · at most 3 trades a day (2 per index) · **one position open at a time** · 30-minute cool-down after an exit · ATM option of the nearest expiry with a quote ≤ 75 s old, ask ≥ ₹30, spread ≤ 3 % · **1 lot, stop 25 % of the premium, first target +20 % (R:R 0.8)** · no broker order path exists.

## 6. Testing and results
* Unit tests `tests/test_safe_ai.py` (12): the rule on synthetic turns, window, expiry-day cut, every refusal, loss lock, caps, in-flight guard, dry-run and control file, dte limit, sweep family.
* Pine mirror `safe_uturn.pine` run in PineTS against the Python rule on 45,000 real 1-minute NIFTY bars: 2 mismatching bars (0.004 %, float ties). `scripts/safe_uturn_parity.py`.
* Captured-session replay (`python -m app.safe_ai.replay`): only 7 of 41 sessions had enough index ticks (the others were hit by capture stalls / database-lock errors fixed on 29-Oct) -> 20 trades, net -Rs 943: inconclusive.
* **12-month NIFTY backtest** (`scripts/safe_ai_grid.py`, results `REPLAY_RESULTS.md`): default rule ~60 % win rate, +Rs 20/trade at a 0.5 % half-spread (95 % interval -321..+338), i.e. break-even, no significant edge. A stop/target geometry grid (15/25/35 % x 15-50 %) gave nothing better (exploratory, on the part of the data available at the time).
* Not tested: SENSEX (no historical expired-option source), BANKNIFTY was pulled afterwards (monthly expiry, only the 3 sessions before each month-end expiry qualify): default rule n=26, 62 % win, +Rs 40/trade at a 0.5 % half-spread (95 % interval -486..+506), again break-even (section in REPLAY_RESULTS.md), real fills (the live signal gets 1-second option capture; the test used 1-minute bars with the stop assumed first).

## 7. What would change this
**Decision taken on 29-Oct-2026:** the 12-month test shows no edge but also no significant loss, so the participant trades live-paper with the safety stack and the disclosure "risk-first, paper, no edge claimed"; SENSEX and BANKNIFTY are included without a historical test (forward data is how they get tested). Switch off (`data/safe_ai/control.json` {"submit": false}, or `SAFE_AI_LIVE=0`) or re-tune if the forward results over the first ~30 signals have a negative net with the interval below zero. The pre-registered variants are thrust 0.35 / 0.50 / 0.70 %; the exit (25 % stop, +20 % target) is fixed. Anything else is a new hypothesis and must be logged as one.
