# Arena terms — ZeroHero AI vs Institution-side human trader (PAPER, accepted 2026-10-20)

**Accepted:** the paper-trading challenge in the form the other side agreed to (one month, signals compared, brokerage + slippage included, no real money). This supersedes the earlier live-money idea (declined). Terms below are fixed before day 1; any change needs both sides' written agreement.

## 1. Who and what
- **Entrants:** (A) ZeroHero AI = the frozen AUTOSCALP engine exactly as in `data/forward_test/frozen_manifest.json` (no manual intervention). A pre-declared exhibition line **ZeroHero AI-V2** = the same trades filtered by the unified policy v1.0.0 (a subset, reported separately; it cannot replace A). (B) The human trader, who submits signals manually.
- **Order types and management (amended 2026-10-20 at the institution side's request):** entry LIMIT / ABOVE / NEAR / MARKET; after entry the human may amend targets or stop-loss, set a trailing stop, emergency-exit or cancel a pending entry. Every action is a server-timestamped, hash-chained event effective after the same 20 s delay, never retroactive, max 20 per signal. The AI's own recorded management (stops, trailing, time exit) is replayed as the same event types so both sides are managed under identical mechanics.
- **Information barrier:** the AI engine is frozen and no trading module imports the arena (enforced by a test); the human side's signals are readable only by the human (token) and the two referees.
- **Instruments:** BUY of options on NIFTY, BANKNIFTY, SENSEX, NATURALGAS, CRUDEOIL (nearest expiry); long only; one lot per signal; no averaging; no re-use of a stopped signal.
- **Period:** **2026-10-20 to 2026-11-25** (start moved from the proposed 2026-10-26; see ARENA_RULE_CHANGES.md). Capital notionally Rs 50,000 each.

## 2. How signals count (anti-hindsight)
- The human submits EVERY signal BEFORE entering, with `scripts/arena_submit.py` (server timestamp, hash-chained, cannot be edited; corrections are new entries). Fields: instrument, expiry, strike, CE/PE, entry (ABOVE / NEAR / MARKET), stop-loss, targets. Unsubmitted trades do not exist.
- The AI's signals are its own paper trades (entry, SL, T1, time) read from the platform ledger; no cherry-picking.

## 2b. Evidence feed and silence of all other signals (added 2026-10-20)
The AI's own signals (the frozen AUTOSCALP entrant, and the exploratory ACTIVE arm, labelled as such) are posted to Telegram when raised; each post carries Telegram's server timestamp and is also appended to a hash-chained ledger. Every other automated signal broadcast to the signals channel is switched off (`TELEGRAM_EVIDENCE_ONLY`). Posts are paper-trading evidence records, not recommendations. The institution side may submit a single practice signal on 2026-10-21 (practice entries are never scored) and its full set of signals during the arena window.

## 3. Settlement (identical for both sides; code in `app/arena/`, open for audit)
- Execution delay 20 s after the signal. BUY fills at the ASK, exits at the BID of captured Angel quote snapshots; SL/targets checked on the BID; T1 exit policy; forced exit at session end (NSE 15:15, MCX 23:15). One stop-first rule; gaps fill at the gap price.
- SL-at-fill (from 2026-10-20, not retroactive): a stop at/above the fill-time sell price is executed at once at that bid.
- Costs: brokerage, STT, exchange charges, regulatory turnover fee, stamp duty, GST from the cost models (index options = formula estimate, MCX NG/CRUDE = contract-note-validated profiles). Slippage is the real/modelled spread inside the fills (not added twice).
- **Data limits, disclosed:** snapshots are ~1–3 minutes apart per strike, so brief spikes between snapshots are invisible to BOTH sides; SENSEX and MCX option snapshots currently carry NO bid/ask (Angel returns zeros), so those fills use a half-spread model (measured where >= 200 rows exist, else a stated proxy/fallback, labelled in every result). Fix in progress: dynamic Dhan depth subscription for arena strikes (blocked while the Dhan tick feed is silent, see status).

## 4. Scoring
- **Primary:** net return after costs on Rs 50,000 (1 lot per signal). **Secondary (reported, never decisive alone):** win rate (net > 0), profit factor, max drawdown, number of signals, share of fills on real bid/ask.
- Win = higher primary. If the gap is smaller than the day-level sampling noise (bootstrap 95% interval of the difference includes 0) the result is **DRAW**. A drawdown > 20% of capital disqualifies the side.
- Win rate alone is NOT the criterion: it can be pushed up by taking tiny wins and rare big losses.

## 5. Honesty clauses
- One month is a small sample; the report states the confidence interval. A win means "ahead in this month", not "proven"; neither side may claim more than the numbers, with their intervals, support. No comparison with "registered advisers or trainers" in general is made or implied: the contest compares two specific entrants.
- Results (including losses) are published unedited: `data/arena/RESULT_*.md/json`, human ledger, forward-test ledger (`verify_chain()`).
- Stakes are reputational only. No money changes hands. Nobody agrees to quit anything "forever": a loss means the losing engine/approach is revised or retired and re-tested.
- Referees: the project owner and a institution-side nominee, both with read access and the right to re-run the scripts.
- Channel reading (added 2026-10-20): the institution side may allow the platform's bot to read its own Telegram channels so that its signals are recorded without double entry. Posts are used only for the arena ledger and settlement; they are never supplied to the AI engine, and the AI side does not use the institution's signals or technique during or after the contest. Removing the bot from a channel ends the reading immediately.
