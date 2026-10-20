# Institution-side trader guide — ZeroHero Arena (paper trading, 22 trading days)

You and the ZeroHero AI trade the **same market on paper**, and both are scored by the **same program on the same recorded quotes**, costs included. No real money is involved.

## 1. What you need
- The arena page: **/static/arena.html** on the ZeroHero server, and your **arena access token** (given to you privately). Paste it once on the page; it stays only in your browser session.
- Nothing else. You do not need the dashboard login.

## 1b. The AI's signals are posted as evidence
Every signal of the ZeroHero AI is posted to Telegram at the moment it is raised, with its ID and time (Telegram's own server time is on every message), and recorded in a hash-chained list (`data/evidence_feed/posts.jsonl`) that referees can compare with the chat. All other automated signal posts are switched off. These posts are PAPER-trading evidence records, not recommendations.

## 2. Dates
- **Arena window:** **20 Oct 2026 to 25 Nov 2026** (start moved earlier on 2026-10-20 by the project owner; see `ARENA_RULE_CHANGES.md`). Every signal you submit inside this window is scored. Entries outside it are never scored.
- Your first signal H00001 (crude, 27-Oct expiry) was a test and is **excluded**; H00002 counts.

## 3. What you may trade
- BUY of an **option** on **NIFTY, BANKNIFTY, SENSEX, NATURALGAS, CRUDEOIL**, nearest expiry. Long only (no selling/writing). **One lot per signal.** No averaging down. A signal that hit its stop is finished; a new idea is a new signal.
- Scored signals can be submitted only while that exchange is trading.

## 4. How to submit a signal — BEFORE you take the trade
Open the **Submit** tab and fill:

| field | meaning | example |
|---|---|---|
| Underlying | one of the five; selecting it loads the live price and the chain | NIFTY |
| Expiry | **auto**: the current (nearest) expiry is pre-selected, the next ones are listed | current |
| Strike / CE or PE | **auto**: the strike list is built around the live price with **ATM / ITM1.. / OTM1..** labels and each strike's last price; ATM is pre-selected. For a call, ITM = below the price; for a put, ITM = above it | ATM CE |
| Entry mode | **LIMIT**: a buy limit — you are filled as soon as the ask comes down to your entry or lower (valid until session end); **ABOVE**: buy only when the option trades up to your price; **NEAR / MARKET**: buy at the next available ask | LIMIT |
| Entry | option premium (not the index level) | 100 |
| Stop-loss | premium; must be below entry | 85 |
| Targets | premium(s) above entry, comma separated | 120, 140 |
| Note | optional (your reason) | "breakout over VWAP" |

**Check the hint line under the form**: it lists the expiries and strikes for which quotes are captured, and the last price of the contract you typed. A contract with no captured quotes cannot be scored; during the arena window such a submission is **rejected** (in practice mode it is accepted with a warning). Premiums are the OPTION price (e.g. crude ATM is about 500, not the futures level).

Tick **"I have not entered this trade yet"** and press Submit. The server stamps the time. You get an id (H00001 …) and a hash. **You cannot edit or delete** a submitted signal; if you changed your mind, submit a new one (the old one still counts).
A trade you did not submit before entering **does not exist** for scoring.

**Speed-ups on the form:** the entry price is pre-filled from the quote of the strike you pick (bid for LIMIT, ask otherwise) and can be edited; "Refresh prices" reloads the chain. Prices come from the captured quotes (~1-3 minutes old).

## 4b. Managing the trade after entry (amend, trail, emergency exit)
In **My signals → Manage a signal** you can, at any time while the exchange is open:
- **Change targets** (e.g. move the target further if it is not being reached) — **Change stop-loss** — **Trailing stop** (by points or %, follows the highest bid since entry) — **Emergency exit** (sell at the next bid) — **Cancel** a still-pending entry.
Each action is recorded with the **server time** and takes effect **20 seconds later** (the same delay everything has). It is never applied retroactively and cannot be undone; a signal can have at most 20 actions. The same rules are applied to the AI: its own real-time management (stops, trailing, time exit) is replayed as the same kind of timed events, so both sides are managed under the same mechanics.

## 4c. Information barrier (the AI cannot see your signals)
The AI engine is frozen (code hashes in the forward-test manifest) and **no trading module imports the arena code**: a test fails if any engine file refers to it. Your signals are readable only with your token and by the two referees. The AI side's trades are timestamped by the platform before you can compare them.

## 5. How every signal is settled (same for you and the AI)
1. Execution starts **20 seconds after** your submission time (first recorded quote after that).
2. **SL-at-fill rule (from 2026-10-20, not retroactive):** if your stop-loss is at or above the sell price at the moment you are filled (for example a limit buy fills far below your limit and the stop sits above the fill), the trade is stopped at once at that bid. This mirrors a real stop order placed above the market. H00002 (settled before the rule) keeps its result.
2b. You **buy at the ask**; you **sell at the bid**. Where a recorded quote has no bid/ask, a half-spread is used (measured where we have data, otherwise a stated assumption; shown in the Standings tab).
3. LIMIT fills at the ask once the ask is at or below your entry. ABOVE waits until the traded price reaches your entry, then fills at the higher of the ask and your entry. NEAR/MARKET fills at the next ask, unless it is already more than 5% above your entry (then it is MISSED).
4. Exit at the **first** recorded quote where the sell price is at or below your (current or trailing) stop (gap = that price), or at/above your **current first target**, or on your emergency exit; otherwise at the session end (NSE/BSE 15:15, MCX 23:15).
5. **Costs:** brokerage, STT, exchange, regulatory turnover fee, stamp duty and GST are deducted; slippage is already inside the bid/ask fills.
6. If there is not enough recorded data to settle a signal, it is shown as NO_DATA with the reason and is reported for both sides.

## 6. Scoring
- **Primary:** net return after costs on Rs 50,000 notional (1 lot per signal).
- **Also shown, never decisive alone:** win rate, profit factor, maximum drawdown, number of signals, share of fills on real bid/ask.
- The winner is the higher primary result. If the gap is within normal day-to-day noise (95% bootstrap interval of the difference contains zero) the result is a **DRAW**. Maximum drawdown above 20% of capital disqualifies a side.

## 7. Fairness and limits you should know
- Recorded quotes are about 1–3 minutes apart per strike, so very short spikes between two snapshots are invisible — for you and for the AI alike.
- SENSEX and MCX option quotes sometimes carry no bid/ask; the model spread is then applied to both sides.
- Results, including losses, are published as they are. One month is a small sample; the report shows the uncertainty. A win means "ahead in this period", not "proven".
- Nobody puts money at stake and nobody agrees to quit anything. A loss means the losing approach is revised and re-tested.

## 8. Questions
- *Can I submit after the move?* No — entries are time-stamped by the server before settlement starts.
- *Can I use a limit order idea?* Use ABOVE (breakout) or NEAR; other order types are not modelled.
- *What if I make a typo?* Submit a corrected new signal; the original stays on the record.
- *Command line instead of the page?* `python scripts/arena_submit.py --participant NAME --underlying NIFTY --expiry 27OCT2026 --strike 23000 --type CE --entry 100 --sl 85 --targets 120,140 --mode ABOVE` (run on the server).
