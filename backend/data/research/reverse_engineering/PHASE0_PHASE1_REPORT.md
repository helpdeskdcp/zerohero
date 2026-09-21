# External Signal Reverse-Engineering — Phase 0 + Phase 1 (Scoped)

Research-only. No code/engine changes. LIVE_TRADING stays false; paper_mode true.
Scope was cut down from the original 23-phase / 10-report spec after two verified
data constraints made phases 2-14 statistically unsupportable (see Verdict below).

## Phase 0 — Data Integrity Classification

| Category | What it covers here | Status |
|---|---|---|
| A. VERIFIED_MARKET_DATA | NIFTY `market_history.db` 2026-09-01→09-18; SENSEX 2026-09-03→09-18 | Present, but option-level density is ~2-3 snapshots/strike/day (ATM-window periodic capture, not continuous) |
| B. OBSERVED_PUBLIC_SIGNAL | External provider's published trade calls (screenshots) | Only entries the user has restated this session are usable (5 below); the full original screenshot batch could not be recovered from session history this turn and was not re-supplied |
| C. SCREENSHOT_DERIVED_DATA | Same 5 entries — no separate OCR pass done | n/a |
| D. INSUFFICIENT_SAMPLE | All stock-option calls (SBILIFE/ICICIPRULI/BAJAJ-AUTO/TVSMOTOR/EICHERMOT), everything pre-2026-09-03 for SENSEX | Zero rows ever captured for stock options; no SENSEX data before 09-03 |

## Phase 1 — Observed Signal Dataset (known entries only)

| signal_date | instrument | expiry | strike | type | action | entry | exit/target | remark | verification_status |
|---|---|---|---|---|---|---|---|---|---|
| 2026-08-31 | NIFTY | 01SEP26 | 24050 | PE | BUY | 64 | 80 | PARTIAL_BOOKED | OBSERVED_PUBLIC_SIGNAL, market data not cross-checked |
| ~2026-09-01 | SENSEX | 03SEP26 | 77000 | CE | BUY | 320 | 490 | TARGET_ACHIEVED | OBSERVED_PUBLIC_SIGNAL, market data not cross-checked |
| 2026-09-03 | SENSEX | 03SEP26 | 76500 | PE | BUY | 80 | 350 | (outlier, flagged EVENT A) | Cross-checked against real captures — see below |
| 2026-09-03 | SENSEX | 03SEP26 | 85500 | PE | BUY | 55 | 370 | (outlier, flagged EVENT B) | INSUFFICIENT_SAMPLE — no capture rows checked for this strike |
| — | SENSEX | 10SEP26 | 76500 | PE | — | — | — | "CHART EVENT", ~15:26 IST | INSUFFICIENT_SAMPLE — outside this DB's captured range for that intraday minute |

**Cross-check done (EVENT A, SENSEX 03SEP26 76500 PE):** real `quote_snapshots` rows for
that day/strike = 2 total (14:16:58 UTC = 347.15, next-day 03:30:12 UTC = 393.30). Both
timestamps fall outside/after the ~15:26 IST window the signal describes. Whole-session
density check: 122 total SENSEX option snapshots across 43 distinct strikes that day (~3
snapshots/strike). **Conclusion: the premium move itself is directionally plausible (347→
in the neighborhood of the reported 350 exit) but the entry-side state (T-30m…T0) cannot be
reconstructed — DATA_UNAVAILABLE, not fabricated.**

## Phase 2 (descriptive only, no statistics)

n=5 known signals. Per this session's own established pattern (calibration work needed
n=122 just to detect miscalibration; every edge-search this session needed 2+ independent
periods to avoid overfitting), n=5 cannot support success-rate, correlation, or
hypothesis-confidence claims — doing so would violate the spec's own Phase 10
anti-overfitting rule. Observations only, `confidence: LOW`, `production_status: RESEARCH_ONLY`:

- All 5 known signals are options BUY (CE or PE), index-only (NIFTY/SENSEX), no stock names.
- The two flagged outliers (EVENT A/B) are both SENSEX PE, both entries in the 55-80 range,
  both exits 3.5-6.7x entry — consistent with a large single-session directional move, but
  2 samples cannot distinguish "repeatable setup" from "two lucky/large moves."
- No order-flow, OI, or VWAP feature can be attached to any of these 5 signals — the
  underlying market data doesn't exist at the needed resolution (Phase 0 finding above).

## Verdict / Phases not attempted

Phases 3-14 (regime mining, event study, confirmation engine stages, orderflow+Groq
payload for these historical signals, XGBoost benchmark, engine version changes) are
**not attempted**: they require either (a) minute-level historical market data around
each signal, which doesn't exist at that density, or (b) a signal sample large enough
to avoid overfitting (n≥50-100 by this project's own track record), which isn't
available. Building any of those phases on this input would produce a confident-looking
but statistically meaningless result — the exact failure mode the spec's Phase 10
prohibits.

## Next research action (if more data becomes available)

1. Re-supply the full screenshot dataset (all entries, not just the 5 above) → widens n,
   still won't fix market-data sparsity.
2. To fix market-data sparsity: would need continuous (not ATM-window-periodic) option
   snapshot capture going forward — a data-collection change, not a research task, and
   out of scope here without separate approval.
3. Until then, this file is the honest ceiling of what Phase 0/1 can produce from data
   already in hand.
