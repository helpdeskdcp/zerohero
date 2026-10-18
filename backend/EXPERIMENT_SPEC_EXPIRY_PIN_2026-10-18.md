# Experiment specification H_PIN — written and committed BEFORE any wall/pin statistic was computed

**Disclosure.** I had already seen (a) the unconditional expiry-day straddle results for the 59 expiries 2025-08-28..2026-10-13 and (b) that the option bars carry open interest. I have NOT looked at any OI-wall, distance-to-wall or closing-drift number. The thresholds below (14:30 entry, 15:15 exit, 15 / 150 bps band, 35 bps stop, 1.25x wall dominance) were chosen on mechanical grounds (last 45 minutes of expiry day) and are not tuned. The spec is committed before the script runs; git history is the timestamp.

## Hypothesis (falsifiable)
On NIFTY weekly-expiry days, between 14:30 and 15:15 IST the index drifts TOWARD the strike with the largest total open interest (CE+PE) ("pin"), by more than the 6.3 bps round-trip cost of trading it in futures. Mechanism: delta-hedging and expiry arbitrage unwinds concentrate around the strike where most contracts settle; the drift is proportional to the distance to that strike. Counter-hypothesis (not tradable here): short-gamma dealers amplify moves away from strikes; if the measured pull coefficient is negative this is reported as "anti-pin" and is NOT accepted (it would need its own pre-registered test).

## Rules (fixed)
- Instrument/timeframe: NIFTY index 5m (`market_history.db`, `load_1m(prefer=ANGELONE_CANDLES)` + `resample`), traded as index futures (basis ignored over 45 minutes). Events: sessions that are NIFTY weekly expiry days.
- Data per event: `open_interest` from the Upstox expired-option 5m bars (`normalized_option_bars`), strikes available = ATM +/-12 around the previous close (about +/-600 points). OI used = the value of the **14:15 bar** (ends 14:20; 10 minutes before the decision, so a one-bar timestamp convention error cannot leak).
- Wall K\*: strike maximising OI_CE+OI_PE among available strikes; ties -> nearer to spot. Require OI(K\*) >= 1.25 x median total OI of the strikes, else NO TRADE.
- D = (K\* - S) / S x 1e4 bps, S = open of the 14:30 bar.
- Entry: |D| between 15 and 150 bps -> at the 14:30 bar open, long if D > 0, short if D < 0. Otherwise NO TRADE.
- Stop: 35 bps adverse from entry, tested on the 5m high/low, stop-first on touch, gap fills at the open. Target: none. Exit: close of the 15:10 bar (15:15) otherwise.
- Cost: 6.3 bps round trip (`app/strategy_lab/costs.py`, STT 0.05% sell side); stress +2 bps. One position per event; no re-entry.
- Missing 14:30 / 15:10 bar or no OI -> event skipped and COUNTED (reported).

## Samples and decision
- **Stage A (existing data):** the 59 expiries 2025-08-28..2026-10-13 already in the database. **Stage B (independent, untouched until Stage A passes):** the ~47 older expiries 2024-10-03..2025-08-27, which must be pulled from the Upstox expired-instruments API (about 1 hour) and are never examined before Stage A is decided.
- Statistic: net bps per trade = side x (S_exit - S_entry)/S_entry x 1e4 - 6.3 (stop applied). One-sided t-test of the mean > 0 (one observation per expiry, so no clustering needed).
- Placebo: the same rule with the wall replaced by a random strike from the available set (2,000 draws); p = share of placebo mean nets >= the real one.
- **Stage A PASS (-> go to Stage B)** requires ALL: n_trades >= 25; mean net > 0 with one-sided p <= 0.07; placebo p <= 0.10; pull coefficient beta > 0 where beta = slope of the signed 14:30->15:15 return on D over all events with a wall; mean net still > 0 with +2 bps extra cost; not carried by one trade (mean excluding the best trade > 0). **Stage A REJECT** if mean net <= 0, or placebo p > 0.25, or beta <= 0.
- **Stage B PASS (-> forward paper validation only)** requires: n_trades >= 25; mean net > 0 with one-sided p <= 0.10; same sign of beta; placebo p <= 0.10. Anything else = REJECT / UNVERIFIED (if n < 25 -> UNVERIFIED, not PASS).
- No profitability claim at any stage; a pass means "eligible for 3+ months of forward paper validation with measured fills".
- Minimum detectable effect is reported with every result (80% power, one-sided 0.05: 2.49 x SE); a non-significant result with a large MDE is UNVERIFIED, not "no edge".
