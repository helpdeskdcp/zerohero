# Hybrid Market Profile v13.0 — final status per acceptance criterion (2026-10-14)

Tests: full suite `./scripts/run_tests.sh -q` → **3208 passed, 0 failed, exit 0** (391 s). New/extended hybrid_mp suites: `test_hybrid_mp.py` (22), `test_hybrid_mp_audit.py` (22), `test_hybrid_mp_spec.py` (25), `test_hybrid_mp_fixtures.py` (16), `test_hybrid_mp_option_model.py` (5), `test_hybrid_mp_bias_world.py` (2), `test_hybrid_mp_api.py` (4), `test_weekday_regression.py` (7).

**Strategy verdict: FAIL (no tradable edge).** NIFTY 5m fixed version: gross +0.027 / +0.033 / +0.036 R, net (2 bps) −0.128 / −0.142 / −0.145 R (TRAIN / VALID / OOS); literal script 1m net −0.29 … −0.41 R. Gross is inside the sign-flip null; every year and every split is net negative. Nothing here is profitable or production-ready.

| # | criterion | status | evidence |
|---|---|---|---|
| 1 | Every input, calculation, state, filter, signal, risk level, dashboard field and alert explained | **PASS** | `PINE_LOGIC_SPEC.md` |
| 2 | Faithful Python port | **PASS (internal) / UNVERIFIED (TradingView)** | 56/56 event-for-event vs an independent literal transliteration (27,617 signals); no TradingView export available |
| 3 | Deterministic fixtures for every signal condition | **PASS** | engulfing, marubozu, hammer, star, pattern filter; SL by pattern (long/short); BoS/CHoCH both directions; retest, continuation, expiry boundary, precedence, opposite pending; T1–T4, SL-first collision; invalid risk; VWAP; MTF; global mirror-symmetry over 6 configurations |
| 4 | Pine vs Python compared candle by candle; discrepancy CSV | **PASS (internal)** | `PARITY_DISCREPANCIES*.csv`; pre-fix defects reproduced and fixed |
| 5 | Repainting / look-ahead / future data | **PASS** | prefix-invariance (chart, MTF, SMC, fixed config); D3 next-bar peek removed |
| 6 | Session handling | **PASS with gaps** | weekday (D1), session masks, special Saturday sessions, MCX rollover tested; **holidays not modelled; MCX per-product hours/US-DST shift UNVERIFIED** |
| 7 | Gaps, repeated signals, SL/target ordering, missing data | **PASS** | EOD_MISSED, gap fills, one-position property tests, 2% injected holes, conservative SL-first |
| 8 | Profile detection reproduced; defects reported; backward-compatible fix | **PASS** | `profile.py`, 13 cases; false positives RELIANCE/SPICEJET/PERSISTENT/GOLDBEES/SILVERBEES; `profileKnown` does not gate signals. **Validation against real TradingView `syminfo` metadata: UNVERIFIED** |
| 9 | MTF timing / compatibility | **PASS (chart TF ≤ requested) / UNVERIFIED (requested TF < chart TF)** | flagged via `mtf_incompatible_tfs` |
| 10 | Opening-gap audit and session-aware correction | **PASS** | Pine formula wrong with extended-hours bars / missing candles (0.114 vs 0.077 pct-pt, worst −6.75% vs +0.44%); corrected version + 5 tests |
| 11 | VWAP missing-volume behaviour | **PASS (documented, option added)** | filter is a silent no-op on index data; `vwap_missing_blocks` |
| 12 | Alerts without duplicates | **PASS** | `alerts.py`, deterministic ids, replay/overlap tests |
| 13 | Restart / replay state consistency | **PASS (fixed variant)** | state from last 3 / 8 / 20 sessions = full history in 50/50 random end bars; **not tested for the no-EOD as-plotted mode** |
| 14 | Underlying-index results separated from CE/PE results | **PASS (separation) / INSUFFICIENT_SAMPLE (CE/PE)** | `INDEX_VS_OPTION_RESULTS.md`: MODEL sign depends on assumed IV; real option bars exist for 11 sessions (13 priced signals) |
| 15 | Option sizing from contract metadata (not index points) | **PASS (model) / PENDING (live contract metadata wiring)** | `option_model.plan_option_trade`; at ₹12,000 / 1% only 0.5% of signals are sizeable |
| 16 | Backtest protocol: splits, costs, drawdown, long/short, null benchmark, sensitivity | **PASS** | `BACKTEST_CORRECTNESS_REPORT.md`; null/bias explained (OHLC sampling); SMC 9-variant comparison; cost sensitivity; nothing tuned on OOS |
| 17 | Backtest correctness defects | **FAILED → FIXED** | weekday off-by-one, pivot tie rule, next-bar peek, bool inversion |
| 18 | Integration read-only first; flag; no schema change; paper default; no live orders | **PASS** | dashboard API + page; `HYBRID_MP_ENABLED`; no execution path touched; `live_trading=false, paper_mode=true` |
| 19 | Frontend/API integration claim | **PASS (read-only dashboard only)** | `/static/hybrid_mp.html` ↔ `/api/hybrid_mp/state` verified live; nothing else is connected |
| 20 | Production deployment of fixes | **PENDING (needs approval)** | platform candle-engine weekday fix + flag are committed, not deployed; **first affected day Fri 2026-10-16 09:00 IST** |
| 21 | Zero missed ticks claim | **NOT CLAIMED** | the platform poller samples the feed; durable boundary is candles, not raw ticks |
| 22 | Strategy profitable | **FAIL** | see verdict above |

## Remaining blockers
1. Restart approval to deploy the Friday fix (and the flag).
2. TradingView chart-data export (see `PINE_PYTHON_PARITY_REPORT.md` §7) for external parity, and the real `syminfo` metadata for the profile detector.
3. Option premium history (only 11 sessions) and measured costs for any CE/PE conclusion.
4. A genuinely different signal source — the evidence says price-pattern entries on the index (BoS/CHoCH, SMC, Renko, Pine indicators tested so far) have no edge after costs.
