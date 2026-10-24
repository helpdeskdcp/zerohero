# Option-flow extension, step 1: bid/ask-level parity (pilot) — pre-registered spec (2026-10-24, before the quote-level features are computed)
Step 4 of the same decision (a live observation-only F3 feed) is described at the end; it takes no decision from this pilot.

## Question
The replicated signal (NIFTY and SENSEX, +0.011 AUC lift) is carried by F3, the 15-minute change of the put-call-parity synthetic future minus the index, computed from 1-minute option CLOSES. Closes are stale / bid-ask-bounce noisy. Is a parity built from bid/ask MID quotes a cleaner version of the same signal?

## Data
market_history.db quote_snapshots (Angel Quote-Full, read-only): NIFTY (from 2026-09-01) and SENSEX (from 2026-09-03) options, nearest not-yet-expired weekly expiry, to 2026-10-23 (38 / 36 sessions). Index 1-minute close from market_candles. This window is INSIDE the two studies already run, so it is not independent confirmation of them; it is a measurement-quality comparison on the same decision points.

## Definitions (decision point = close of each 5-minute index bar, bar number >= 7, same as the main study)
- Quote at time x for (strike, side): the latest snapshot with x-180 s <= received_ts <= x and bid > 0 and ask > bid. Mid = (bid+ask)/2. A decision point is dropped if any of the four needed quotes (call and put at ATM(t) at t, at ATM(t-15 min) at t-15 min) is missing. ATM = strike nearest to the index close.
- F3m = [ (K + C_mid - P_mid - S)(t) - (K' + C_mid - P_mid - S)(t-15 min) ] / S(t) x 1e4 (bp); F3c = the same with the last traded prices (ltp); F3z = the numerator of F3m divided by W(t), W = (C_ask - C_bid) + (P_ask - P_bid) at t (the parity change measured in units of the quoted spread).
- Target: NIFTY / SENSEX direction over the next 30 minutes (same session).

## Analysis (descriptive; ~2,500 points per index, standard error of an AUC about 0.012, so this pilot can support or contradict but not confirm a +0.01 effect)
Q1 univariate AUC of F3m, F3c, F3z, with day-block bootstrap CIs, per index. Q2 paired difference AUC(F3m) - AUC(F3c), day-block CI. Q3 mean 30-minute return of F3m terciles.
Outcome labels fixed now: SUPPORTS if AUC(F3m) >= AUC(F3c) - 0.002 in BOTH indices and AUC(F3m) > 0.50 in both; CONTRADICTS if AUC(F3m) < 0.50 in both; else INCONCLUSIVE. No gates, no model, no trading rule. Whatever the label, the live feed (step 4) records both versions so the forward data decides.

## Step 4: live F3 feed (observation only)
Every completed 5-minute bar of NIFTY and SENSEX the autoscalp tick logs F3m, F3c, F3z, the ATM strike, call / put mids, spread width and the age of the quotes to its own SQLite file, fills the realised 30-minute index return afterwards, and exposes the running forward AUC / tercile table at GET /api/autoscalp/option-flow. It is not an input to any decision and sends no Telegram message. Evaluation rule (fixed now): no conclusion before 60 sessions or 3,000 logged points per index; the forward verdict then uses the same replication criterion as the SENSEX spec (univariate AUC of F3m > 0.510).
