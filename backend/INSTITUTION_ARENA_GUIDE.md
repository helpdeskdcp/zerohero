# Institution-side trader guide — ZeroHero Arena (paper trading, 22 trading days)

You and the ZeroHero AI trade the **same market on paper**, and both are scored by the **same program on the same recorded quotes**, costs included. No real money is involved.

## 1. What you need
- The arena page: **/static/arena.html** on the ZeroHero server, and your **arena access token** (given to you privately). Paste it once on the page; it stays only in your browser session.
- Nothing else. You do not need the dashboard login.

## 1b. The AI's signals are posted as evidence
Every signal of the ZeroHero AI is posted to Telegram at the moment it is raised, with its ID and time (Telegram's own server time is on every message), and recorded in a hash-chained list (`data/evidence_feed/posts.jsonl`) that referees can compare with the chat. All other automated signal posts are switched off. These posts are PAPER-trading evidence records, not recommendations.

## 2. Dates
- **Rehearsal: 22 Oct to 1 Nov 2026** (the arena has been running since 20 Oct). Signals in this period are scored on the leaderboard for practice, so everyone can test the system; they are **not part of the official result**.
- **Official contest: from Monday 2 Nov 2026** (1 Nov is a Sunday) to **Thursday 3 Dec 2026** (about 22 trading days, holidays excluded). Only signals from the official start count. At the cut-over the rehearsal standings are saved (`REHEARSAL_FINAL.md`) and the scoreboard starts again from zero.
- Signals agreed by the parties as tests are **excluded** from scoring and listed in `ARENA_RULE_CHANGES.md` (currently H00001 and H00007).

## 3. What you may trade
- BUY of an **option** on **NIFTY, BANKNIFTY, SENSEX, NATURALGAS, CRUDEOIL, GOLDM** (MCX gold mini), nearest expiry. Long only (no selling/writing). **Quantity is in lots (default 1; your profile may have another default, shown on the form), 1 to 10.** No averaging down. A signal that hit its stop is finished; a new idea is a new signal.
- **Equity (NSE cash, intraday)** — **BUY or SELL (short)** of any NSE-listed share by its trading symbol (e.g. RELIANCE), quantity in **shares** (up to ₹25 lakh order value). Squared off at 15:15 like every other trade; no overnight. A BUY has its stop below the entry and targets above; a SELL (short) has its stop **above** the entry and targets **below**. The same entry modes apply (NEAR / MARKET / LIMIT / ABOVE: for a SELL, LIMIT = sell when the bid rises to your price, ABOVE = sell when the price trades *down* to it). Use the **Equity** card on the Submit tab. A BUY fills at the ask and exits at the bid; a SELL fills at the bid and covers at the ask. Charges are the intraday equity schedule (brokerage ₹20 or 0.1% per order, STT 0.025% on the sell side, exchange fee, regulatory turnover fee, stamp duty on the buy side, GST) — a published-schedule estimate, not checked against a real contract note.
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
| Stop-loss | premium; **optional** (leave blank for none); if given it must be below entry | 85 |
| Targets | premium(s) above entry, comma separated; **optional** (leave blank for none) | 120, 140 |
| Note | optional (your reason) | "breakout over VWAP" |

**Check the hint line under the form**: it lists the expiries and strikes for which quotes are captured, and the last price of the contract you typed. A contract with no captured quotes cannot be scored; during the arena window such a submission is **rejected** (in practice mode it is accepted with a warning). Premiums are the OPTION price (e.g. crude ATM is about 500, not the futures level).

Tick **"I have not entered this trade yet"** and press Submit. The server stamps the time. You get an id (H00001 …) and a hash. **You cannot edit or delete** a submitted signal; if you changed your mind, submit a new one (the old one still counts).
A trade you did not submit before entering **does not exist** for scoring.

**Speed-ups on the form:** the entry price is pre-filled from the quote of the strike you pick (bid for LIMIT, ask otherwise) and can be edited; "Refresh prices" reloads the chain. Prices come from the captured quotes (~1-3 minutes old).

## 4b. Managing the trade after entry (amend, trail, emergency exit)
In the **Terminal** tab, click a waiting order or an open position to **modify** it (entry price of a waiting LIMIT/ABOVE order, stop-loss, targets, quantity of a waiting order, trailing stop, cancel a waiting order, "SL → cost"); or use **My signals → Manage a signal**. At any time while the exchange is open you can:
- **Change targets** (e.g. move the target further if it is not being reached) — **Change stop-loss** — **Trailing stop** (by points or %, follows the highest bid since entry) — **Emergency exit** (sell at the next bid) — **Cancel** a still-pending entry.
Each action is recorded with the **server time** and takes effect **3 seconds later** (the same delay everything has; **20 seconds** for anything stamped before 26 Oct 2026 13:13 IST) It is never applied retroactively and cannot be undone; a signal can have at most 20 actions. The same rules are applied to the AI: its own real-time management (stops, trailing, time exit) is replayed as the same kind of timed events, so both sides are managed under the same mechanics.

## 4c. Information barrier (the AI cannot see your signals)
The AI engine is frozen (code hashes in the forward-test manifest) and **no trading module imports the arena code**: a test fails if any engine file refers to it. Your signals are readable only with your token and by the two referees. The AI side's trades are timestamped by the platform before you can compare them.

**Your Telegram channels (4d).** If you let the platform's bot read your channels (consent-based; the bot must be added as admin by you, and only the channel ids you give are read): (1) the posts are used **only** to record your signal in the arena ledger, with the platform's receive time, and for settlement/comparison; (2) they are **never given to the AI engine** and the AI will not use your signals or technique, now or after the contest: the same import-barrier test above covers the channel-reading code; (3) channel claims such as "target hit" or "points achieved" are stored but never scored; (4) you can stop it at any moment by removing the bot from the channel (or asking us to clear the channel list); (5) the post text is stored only in the private audit log on the server, readable by the two referees, and is not published.

## 4e. Several participants, separate profiles, and the leaderboard
Each institution-side participant has **their own access token and their own profile**: signals and results are never mixed, and nobody can see another participant's signals. Every participant is scored against the AI separately. The **leaderboard** (`/static/leaderboard.html`, same token) ranks everyone, the AI included, by **net profit after costs** on identical settlement rules, for "today" and "overall" (top 10). It shows totals only, never signals. Ranking on a few trades is noise, not skill: the page says so and no profitability claim is made. New participants can be added during the contest without a restart.

**Entry-only channels (Neeraj Joshi).** A channel that publishes only the signal (contract + entry) and no stop or targets is settled with a stop and targets set by ZeroHero's **rule-based risk module** (stop from the contract's own recent swing low, clipped to 55%-85% of the entry; targets at 1R, 2R, 3R of that risk, "1:1, 1:2, 1:3"). Such a profile is labelled "entry + ZeroHero stop/targets" on the leaderboard: it measures the trader's entries combined with the module's risk rules, not the trader alone. Official scoring uses the standard first target (1:1); the 1:2 and 1:3 exits are shown as descriptive variants only. This module is not one of the AI trading engines, and no AI engine sees these signals.

**Fill window and proof.** A signal read from a Telegram channel is a *range / limit / above* entry that stays valid for **15 minutes** after the post: if the ask is not at or inside your range within that time, the signal expires unfilled (it is never filled hours later). Forms you submit yourself stay valid until the session end unless you set a validity. Every day an **evidence pack** is written for every signal (your row, the source post and its Telegram time, the captured bid/ask around the trade, the settlement, the rules and the code version) with a sha256 manifest, so any number on the leaderboard can be re-checked.

**Entry-only profile and preview.** A profile can be set up as *entry-only*: you give the contract and the entry, and ZeroHero's rule-based risk module sets the stop-loss and the targets (1:1 / 1:2 / 1:3). On that profile you may leave both fields blank in the form; whatever you do fill in is kept. Press **Preview stop & targets** to see what the module would set before you submit (nothing is recorded by the preview). Entries of such a profile are valid for 15 minutes, like channel entries.

**Gold (GOLDM).** MCX gold-mini options are in the contest (traded 09:00-23:30, scored like the other MCX contracts, session-end exit 23:15). Strikes and premiums are in rupees per 10 g; one lot is 100 g, so **one rupee of premium is worth Rs 10 per lot**. The current monthly expiry (for example 29 Oct 2026) is the one quotes are captured for. **The gold cost model is PROVISIONAL**: it uses the public charge schedule (brokerage, CTT, exchange, GST, stamp) and has not yet been validated against a real contract note, so every gold result carries that label. A real contract note for a gold option trade will let us validate it.

**The stop-loss and the targets are optional.** You may submit a signal with no stop-loss (and/or no targets) and add one after the trade is on ("Change stop-loss" in *My signals*), or never. Without a stop and without a target the trade ends only at an "exit now" you send or at the session end; with only a target it ends at that target, an "exit now" or the session end. Targets can be added later too ("Change targets") (23:15 MCX, 15:15 NSE/BSE); a stop you add later works only from the moment it takes effect (same 20-second delay as every change, never retroactive). Risk is yours: a trade without a stop can lose the whole premium, and that result counts exactly as it falls. The settlement rules are the same for everybody.

**Quantity (lots).** Every signal has a quantity in whole lots (1 to 10). The default is 1 lot unless your profile has another default (Mohit Agrawal's profile: 2 lots, also for the signals read from his channels). You can change the quantity of a signal **only while its trade is not active yet** (use *Change quantity* in *My signals*; it takes effect after the usual 20-second delay); **once the trade is active (filled) the quantity cannot change any more**. P&L is shown in rupees for your lots, brokerage is paid once per order pair and the other charges scale with the quantity. **The ranking and the comparison with the AI use the net result PER LOT (as if every participant had traded 1 lot)**, so a bigger quantity never decides who is ahead; the leaderboard shows both the per-lot figure (the rank) and the rupees for the lots traded.

**Entry zones and result posts (Telegram channels).** A post such as "Entry: Above 570 - 575" is read as an ABOVE entry with a zone: the trade triggers when the price trades at or above 570 and is bought only while the ask is at or below 575 (no chasing above the zone; if the price runs past it, the signal waits for it to come back inside, within its 15 minutes). "570 - 75" is read as 570-575. "CALL" and "PUT" mean CE and PE. Posts that report results ("Result", "Achieved", "Stop-Loss Hit", "Combined trade report", "After confirmation" updates) are claims, never entries.

## 5. How every signal is settled (same for you and the AI)
1. Execution starts **3 seconds after** your submission time (first recorded quote after that; quotes are about 4 seconds apart). Signals stamped before 26 Oct 2026 13:13 IST keep their original 20-second delay.
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
