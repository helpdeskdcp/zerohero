# SSL Hybrid PRO — FINAL VERDICT (research thread closed 2026-09-11)

_Research/measurement only. No broker wiring, no live/paper order path, no
strategy layer. Engine file (`app/engines/ssl_hybrid_pro_engine.py`) was
built once and never modified afterward — every result below comes from
standalone, read-only analysis scripts run against it._

## Verdict: **NO-GO.** No stable, out-of-sample, direction-consistent edge
found for the SSL Hybrid PRO Pine indicator, on either NIFTY or BANKNIFTY
futures, across two independent 3-month periods, under any variant tried
(raw signal, target/stop adjustments, the Pine confidence score as a filter,
or a custom-built composite quality score). **Recommendation: do not deploy,
do not wire to paper/live trading, do not rebuild without a genuinely new
hypothesis.**

---

## 1. What was built (deliverable inventory)

| # | deliverable | location |
|---|---|---|
| 1 | Core Pine→Python engine (formula-exact, stateful, no look-ahead) | `app/engines/ssl_hybrid_pro_engine.py` |
| 2 | Hand-check verification (EMA/RMA/WMA/HMA/RSI/ATR vs manual calc + 400-bar determinism/no-lookahead check) | `/root/.claude/jobs/ba6f2282/tmp/verify_ssl_engine.py` |
| 3 | TradingView-export parity harness (never run — no real TV export ever obtained) | `scripts/ssl_hybrid_pro_parity_harness.py` |
| 4 | Real-data validation (Kaggle 10yr index, Upstox 3mo index, app-DB futures, Upstox 3mo futures) | `scripts/ssl_hybrid_pro_upstox_validation.py`, `ssl_hybrid_pro_app_db_validation.py`, `ssl_hybrid_pro_upstox_futures_validation.py` |
| 5 | Outcome backtest engine (SL/T1/T2/T3 first-touch, disclosed same-candle tie-break rule) | `scripts/ssl_hybrid_pro_outcome_backtest.py` |
| 6 | What-if sensitivity scripts (T1/SL adjustment, score threshold, ADX/score2 composite) | `scripts/ssl_hybrid_pro_t1_sensitivity_test.py` |
| 7 | Second-period, second-instrument confirmation scripts | `scripts/ssl_hybrid_pro_period2_confirm.py`, `ssl_hybrid_pro_banknifty_test.py`, `ssl_hybrid_pro_banknifty_period2.py` |
| 8 | Per-signal outcome CSVs | `data/ssl_hybrid_pro_outcome_backtest_nifty_fut_sep26.csv`, `data/ssl_hybrid_pro_outcome_backtest_banknifty.csv` |
| 9 | Raw provenance (every Upstox API response, gzip, unmutated) | `data/historical/upstox_v3_validation/raw_*.json.gz` |

Engine build quality: **solid.** Faithful to every formula/weight/rounding
rule in the 30-section spec; verified deterministic (bit-identical across
independent reruns, every field, every candle, on datasets up to 1.07M
candles); verified causal (zero look-ahead by construction — one candle at a
time, no batch shortcut exists); zero exceptions across ~4M candle-updates
run this session.

## 2. Data reality discovered along the way

- The raw **NIFTY 50 / BANKNIFTY index** has **zero volume on every real feed
  checked** (Kaggle AND Upstox) — an index is never itself traded, only its
  derivatives are. `ta.vwap` is therefore permanently undefined (`0/0`) on
  the index, and the engine's VWAP-gated `strongBull`/`strongBear` condition
  can never fire — confirmed not to be an engine bug (synthetic non-zero
  volume on the same real prices produced signals immediately).
- Switching to the **futures contract** (real traded volume + OI) makes the
  full signal path fire normally. All outcome testing below uses futures.
- A single futures instrument_key only has data from its own NSE listing
  date (~3 months before its own expiry) — a full continuous multi-contract
  history requires splicing across a roll, which was deliberately **not**
  done (no invented rollover price adjustment). Two independent, non-
  overlapping single-contract windows were used instead.

## 3. Outcome backtest — consolidated results (all periods, both instruments)

Methodology, identical every time: real Upstox 1-minute OHLCV+OI, unmodified
engine run sequentially and deterministically, then a signal-by-signal walk
forward evaluating SL vs T1 vs T2 vs T3 first-touch (SL wins any same-candle
tie; among targets only, the farthest reached is credited — disclosed,
conservative, applied identically everywhere).

| Instrument | Period | Signals | Win% | PF (ALL) | PF (BUY) | PF (SELL) | Total pts |
|---|---|---:|---:|---:|---:|---:|---:|
| NIFTY | P1 2026-07-01→09-10 | 906 | 39.0% | 0.796 | 0.736 | 0.849 | −1,225.36 |
| NIFTY | P2 2026-04-01→06-30 (indep.) | 1,086 | 44.3% | 0.898 | — | — | −1,109.95 |
| BANKNIFTY | P1 2026-07-01→09-10 | 904 | 40.7% | 0.909 | 0.800 | 1.005 | −1,171.64 |
| BANKNIFTY | P2 2026-04-01→06-30 (indep.) | 1,075 | 47.3% | 0.979 | **1.136** | 0.838 | −572.42 |
| NATURALGAS (MCX) | single: 2026-05-20→09-10 | 2,465 | 43.3% | 0.933 | 0.951 | 0.917 | −24.72 |

NATURALGAS is closest to breakeven of the three instruments (PF 0.933) but
**only one period was available** — Upstox's Expired Futures Contract API
(`/v2/expired-instruments/future/contract`) does not resolve MCX commodity
underlyings (tried `MCX_FO|<token>` and `MCX_COM|<token>` formats; empty or
400 for every date tried), so a second, independent-period confirmation —
the exact check that broke NIFTY's and BANKNIFTY's better-looking numbers —
could not be run for this instrument. Its result is reported as unconfirmed,
not as a third clean pass.

**Profit factor never durably exceeds 1.0.** The closest approaches
(BANKNIFTY SELL P1 = 1.005, BANKNIFTY BUY P2 = 1.136) are each near-breakeven
in exactly one period and the *opposite* side loses in the *other* period —
the profitable side flips between periods on both instruments, which is the
signature of regime-dependence, not a real directional edge.

## 4. Filters/adjustments tried (all standalone, engine never touched)

| variant | in-sample-looking result | held up out-of-sample? |
|---|---|---|
| T1 −1% / −2% / −3% (SL, T2, T3 unchanged) | win rate +0.1 to +0.7pp | **No net improvement** — total points flat-to-worse every time |
| T1 −3% + SL +2% | win rate +0.7pp | **Worse** — wider SL costs more per stop than the extra wins recover |
| Pine confidence score (73/77/82/86/91) as a filter | score≥86/91 looked profitable (PF 1.06/1.76, n=30/17) | **Failed** — flips sign on a chronological train/test split (PF 1.15→0.99 at score≥86); formal calibration ECE 38.1pp, Brier 0.385 (worse than naive base rate); score is non-monotonic with actual win rate |
| ADX-magnitude filter (external, not in engine) | top-10% by ADX: PF 1.52 train → 1.81 test (same period) | **Failed on 2nd period** — PF *fell* with higher ADX threshold on the independent Apr–Jun window (opposite direction) |
| score2 composite (z-ADX + z-RSI-dist + z-baseline-dist, TRAIN-fit) | top-20%: PF 1.17 train → 1.50 test (same period) | **Failed on 2nd period** — never exceeded PF 0.96 on the independent window |

Every lever that looked promising on one dataset failed the moment it was
checked against a second, non-overlapping period. None survived.

## 5. Why this matters / how it fits the rest of the codebase

This is now the **N-th** independently-built pattern/indicator strategy in
this codebase to reach the same conclusion on real Indian index/futures
data: [[inside-bar-2m-nogo]], [[trend-swing-nogo]], [[tri-compare-nogo]],
[[math-scalper-engine]], the mean-reversion/afternoon-fade sweep, and now
SSL Hybrid PRO twice over (deleted-then-rebuilt) — all NO-GO, all for
essentially the same underlying reasons: technical-pattern signals on NIFTY/
BANKNIFTY show weak-to-moderate structure at the index level, but that
structure does not survive (a) real option/futures transaction mechanics,
(b) an honest walk-forward split, or (c) a second independent test window.
[[calibration-overconfidence-k8]] additionally shows the live AutoScalp
engine's own confidence score has the identical miscalibration signature
found here.

## 6. What would be needed before revisiting this

Not a smaller tweak to this engine — every reasonable one was tried. A
future attempt would need a **new hypothesis** (different underlying market
mechanism, not another parameter/threshold on the same signal), and any
apparent edge would need to clear, at minimum: (a) a walk-forward split on
the discovery dataset, AND (b) a fully independent second period, AND
(c) ideally a second instrument — the exact three-gate process this thread
just ran, which is what caught every false lead here before it could be
mistaken for a real one.

**Status: CLOSED.** No application/engine will be built or deployed from
this research. `app/engines/ssl_hybrid_pro_engine.py` remains in the
codebase as a validated, reusable Pine-clone reference implementation (in
case a different signal built on the same primitives — EMA/RMA/HMA/VWAP/DMI
— is worth trying later), but nothing calls it, and it should stay that way
absent a new, distinct hypothesis.
