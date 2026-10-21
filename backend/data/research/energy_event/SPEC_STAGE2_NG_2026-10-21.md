# Pre-registered spec: H-EV-NG stage 2 (rule + forward test). Written BEFORE any strategy P&L was computed.

**Origin.** Stage 1 (`STAGE1_RESULT_2026-10-21.json`): the 15-minute range after the Thursday natural-gas storage release was 3.3x the control days (95% interval 2.0-5.1, p 4e-7, both sub-periods, n=14). CRUDEOIL showed no effect (1.0x) and is dropped. The NG-only hypothesis was formed after seeing both results, so it is a NEW hypothesis that only forward data can confirm.

## A. Design principle: one rule per instrument, activated only on its own days
- A strategy that works on one instrument/day is NOT generalised to other instruments. Each module = (instrument, activation calendar, entry/exit rule, own evidence). Outside its calendar the module is flat. NIFTY/BANKNIFTY/SENSEX get a module only if a separate hypothesis is pre-registered and passes its own premise test; no cross-index reuse.
- A `HYPOTHESIS_REGISTER` line is added for every (instrument, day-rule) tried, passed or failed, so the number of looks is visible (multiple-testing control).

## B. Activation calendar (module H-EV-NG)
- MCX natural-gas options, Thursdays only, on days the US storage report is released at its normal slot and MCX is trading. Release clock R = 10:30 New York: **20:00 IST until 2026-10-31, 21:00 IST from 2026-11-02** (US summer time ends 2026-11-01). Stage 1 measured only the 20:00 slot; the 21:00 slot is the same New York event but is untested, so it is recorded as "extrapolated".
- Inactive (no trade, logged): 2026-11-26 (US Thanksgiving; report moves to Wed 25 Nov, not tested), any holiday-shifted week, any day the captured option feed is stale/disconnected at R-2 minutes.
- Contest-window activations: 22 Oct, 29 Oct (20:00 IST); 5, 12, 19 Nov (21:00 IST). At most 5 (n is far too small for any conclusion).

## C. Rule (fixed now; no tuning after seeing data)
1. Observe the three 1-minute futures bars opening at R, R+1, R+2. move = close(R+2) / open(R) - 1.
2. Trigger only if |move| >= 0.30%. Else no trade (logged as NO_TRIGGER).
3. Direction = sign(move) (continuation): up -> buy CE, down -> buy PE. **The sign is never flipped after seeing results.**
4. Contract: nearest captured expiry, strike nearest to the future at R+2, 1 lot, long only.
5. Entry: arena rules (first snapshot at/after R+3 minutes + 20 s delay, ask-in). If the bid/ask spread at that snapshot exceeds 8% of mid, or no quote within 90 s: NO_TRADE (logged, counted as skipped, never as a win).
6. Exit: stop at -30% of the fill premium; first target +40%; hard time exit at R+30 minutes (bid-out). Whatever happens first. The arena SL-at-fill rule applies as written.
7. Costs: arena cost models for MCX options (CTT etc.), not the spread model if real bid/ask exist.

## D. Stage 2a: descriptive check on the underlying (futures 1-minute, stage-1 sample, event days = the 14 stage-1 Thursdays; uncertain/no-data dates excluded exactly as in stage 1)
For days that trigger: continuation = sign(price(R+30) - price(R+3)) equals sign(move). Report n_trigger, continuation rate, mean signed futures return R+3 -> R+30, no costs. **Drop rule: if the continuation rate is <= 50% the module is DROPPED (no sign flip, no re-parameterisation).** If > 50% it proceeds to forward shadow only; this check cannot validate it (n will be ~10).

## E. Forward test (shadow, additive, paper)
- Separate participant `ZEROHERO_AI_EVENT`, its own hash-chained ledger; independent of AUTOSCALP and of the frozen forward manifest (does not alter its hashes). It is DISCLOSED as a separate line in any comparison; it is not blended into the main AI score unless both referees agree in writing and the change is logged in `ARENA_RULE_CHANGES.md`.
- Information barrier unchanged: no code reads the institution's signals or channel posts.
- Claim thresholds: nothing may be called "validated" before **n >= 30 activations** (about 7 months of Thursdays), net-of-cost mean > 0 with a 95% interval above 0. Until then the label is INSUFFICIENT_SAMPLE whatever the sign.

## F. What this spec does not claim
No profitability is claimed. Stage 1 shows that volatility expands after the release; it says nothing about direction or about option pricing. Long single options pay theta and spread; the expansion may already be priced into implied volatility. Only the forward ledger can answer that.
