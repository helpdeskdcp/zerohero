ZEROHERO — PHASE F: INSTRUMENT PROFILE & CALIBRATION ENGINE
=============================================================
(includes the OpenRouter AI intelligence layer built as the same
continuous piece of work, since both explicitly build on the same
profile abstraction)

Date: 2026-09-19. Paper-only throughout. No live broker order was ever
placed or attempted. No API key is hardcoded anywhere.

---

## Architecture

```
Market Data
    v
Instrument Resolver          (unchanged -- app.instruments / app.market_hub)
    v
Instrument Profile Selector  (NEW -- app.instrument_profiles)
    v
Regime Detector               (unchanged -- app.engines.regime_mtf)
    v
Profile + Regime Configuration (NEW -- app.effective_profile, pure/deterministic)
    v
Common Signal Engine          (unchanged -- app.engines.scalp_strategy)
    v
Confirmation Engine           (unchanged -- app.engines.option_engine)
    v
EV / Cost Gate                (EXTENDED -- Phase C's empirical avg_win/avg_loss,
                                now labeled VALIDATED/EMPIRICAL/INSUFFICIENT_SAMPLE)
    v
Risk Engine                   (unchanged -- app.autoscalp.safeguards)
    v
Entry / NO TRADE               (unchanged -- app.engines.paper_trading)
    v
Position Management            (unchanged)

     [in parallel, shadow-mode only, opt-in, default OFF]
Behavior Engine (NEW -- app.behavior_engine)
    v
OpenRouter AI (NEW -- app.ai.openrouter_client / behavior_ai)
    v
AI + Deterministic Fusion (NEW -- app.ai.fusion)
    v
shadow_decisions table (observation only -- never read by the live path)
```

**What changed vs. what didn't.** No signal-generation math, ATR
multiple, EV threshold, Greek weight, or entry/exit rule was altered.
`app.autoscalp.runner.DEFAULT_CONFIG["symbol_profiles"]` is now
*generated* from `app.instrument_profiles.REGISTRY` instead of being a
second, independently-hardcoded dict — a single-source-of-truth change,
verified byte-identical to the prior literal
(`tests/test_instrument_profiles.py::test_build_symbol_profiles_config_matches_prior_literal`).
The EV-gate mode label gained richer terminology (see below) but the
underlying avg_win/avg_loss selection logic from Phase C is unchanged.
The AI/behavior-engine layer is entirely new and, by design, cannot
influence a real paper trade unless `ai_shadow_mode.enabled` is
explicitly turned on, and even then only writes an observation row.

## Instrument Profiles

| Symbol | Lot size (VERIFIED) | Overrides | Cost model | Validation status |
|---|---|---|---|---|
| NIFTY | 65 | none (runs on P6-validated base defaults) | UNCALIBRATED | DEFAULT |
| BANKNIFTY | 30 | none (n too thin when last reviewed) | UNCALIBRATED | UNVALIDATED |
| SENSEX | 20 | none | UNCALIBRATED | UNVALIDATED (contaminated sample) |
| NATURALGAS | 1250 | max_hold_sec, min_ev_r, rr_min, est_cost_r, trail_atr | **OK** (₹113.50/lot round-trip) | DERIVED |
| CRUDEOIL | 100 | max_hold_sec, sl_atr, t1_atr, min_ev_r, rr_min, est_cost_r, trail_atr | **OK** (₹184.50/lot round-trip) | DERIVED |

Every parameter carries `{value, status, source, rationale}` — see
`app/instrument_profiles.py::REGISTRY` for the full citation trail. Lot
sizes were verified directly against the live AngelOne instrument master
this session (`app.instruments.master_rows()`) — one BANKNIFTY value
(35) was initially assumed and caught as wrong during this same pass;
the actual value is 30. This is exactly the kind of error the
VERIFIED/DERIVED/DEFAULT/UNVALIDATED/UNKNOWN discipline exists to catch
— nothing here should be trusted without checking the `source` field.

**NIFTY vs BANKNIFTY**: per `ZEROHERO_TRADING_EDGE_VALIDATION_2026-09-19.md`,
real data differences exist (BANKNIFTY: win_rate 0.571, expectancy
+1.2371 gross, n=35; NIFTY: win_rate 0.469, expectancy -1.4016 gross,
n=32) but this was explicitly found to be **statistically indistinguishable
from noise** by the Phase E adversarial review (BANKNIFTY's confidence
interval overlaps the pooled losing population). No profile parameter
was tuned from this difference — both run on identical base defaults.
This is the honest, current state: a real behavioral difference may
exist, but the evidence to calibrate one is not yet strong enough.

## Regime Engine

Regimes supported: `TRENDING_UP`, `TRENDING_DOWN`, `RANGE`,
`HIGH_VOLATILITY`, `LOW_VOLATILITY`, `EXPIRY_DAY`, `NORMAL_DAY`. Only
`EXPIRY_DAY` has real overrides — the already-live `expiry_day_profile`
from `runner.py`, restated as governed Params (same numbers, same
rationale). Every other regime has an **empty** override set, explicitly
marked DEFAULT: no regime-conditional threshold has ever been validated
against real trade outcomes, and Phase E found TRENDING_UP is actually
the *worst*-performing regime — attributed to a structural entry-timing
issue (continuation setups blocked by default), not a threshold a
regime override could fix. Inventing per-regime numbers here would be
exactly the overfitting risk this phase exists to prevent.

Selection is deterministic and pure: `select_effective_profile(symbol,
regime, is_expiry_day)` takes only values already known to the caller
at decision time — no DB read, no network call, no look-ahead is
possible by construction. On an expiry day, the regime override wins
over the instrument's own override for the same parameter (tested).

## Cost Model

Unchanged methodology, `app/institutional_edge/costs.py`: two real,
AngelOne-contract-note-validated profiles (NATURALGAS, CRUDEOIL). No
cost model exists or was invented for NIFTY/BANKNIFTY/SENSEX — see
`ZEROHERO_PHASE_E` findings for exactly what evidence (a real executed-
order contract note, or a manually cross-checked broker calculator run)
would be required, since these are paper trades and no such evidence can
be produced by this system's normal operation.

`app.engines.paper_trading.close_trade()` (Phase C) already computes
`gross_pnl` / `trading_cost` / `slippage_cost` / `net_pnl` /
`cost_model_status` per real closed trade, using this same module.

## EV Gate

Terminology (Phase F extends Phase C's EMPIRICAL/IDEALIZED into 4
states, `app/autoscalp/runner.py`'s tick loop):

- `INSUFFICIENT_SAMPLE(n=N,fallback=IDEALIZED)` — fewer than 30 real
  closed trades for this symbol; falls back to the original idealized
  ~1.55R geometry (`avg_win=None, avg_loss=None` passed to the gate).
- `EMPIRICAL(n=N,cost=UNCALIBRATED)` — ≥30 real closed trades, real
  avg_win/avg_loss used, but this symbol's cost model is not validated
  (currently: NIFTY, BANKNIFTY, SENSEX).
- `VALIDATED(n=N)` — ≥30 real closed trades AND a validated cost model,
  so `db.get_recent_win_loss_stats()`'s net_pnl preference is actually
  backing the numbers (currently: NATURALGAS, CRUDEOIL).

**n≥30 is a sample-size floor, not proof of profitability** — confirmed
in `tests/test_instrument_profiles.py`: the floor doesn't discriminate
on outcome (BANKNIFTY, the only positive-expectancy symbol, and
NATURALGAS, roughly flat, both reach EMPIRICAL/VALIDATED identically).

## Data Quality — SENSEX

`app/autoscalp/trade_contamination.py` classifies every CLOSED trade
row for known contamination patterns (`TIME_NODATA`, `entry==exit_price`,
missing exit price, invalid timestamp ordering, BSE-routing
contamination) and never silently drops a row — every exclusion is
reported with its reason (`data_quality_report()`). All 15 real SENSEX
trades in the current sample are contaminated (100%), caused by two
sequential exchange-routing bugs (`8023607`, then `a910e1c` on
2026-09-18 22:49 IST) that sent SENSEX/BANKEX option legs to NFO(2)
instead of BFO(4), so the WS feed never marked them and they were force-
closed FLAT at entry price after 2x max-hold.

Both bugs are confirmed fixed in code (re-verified this phase, both
regression tests pass). A `PRE_FIX`/`POST_FIX` epoch split
(`BSE_ROUTING_FIX_TS`) is built into the classifier so future SENSEX
data is never pooled with the contaminated pre-fix sample. **No
POST_FIX SENSEX trade exists yet** — the fix is validated in code, not
yet validated against fresh live data.

## Validation (real numbers, not invented — from `ZEROHERO_TRADING_EDGE_VALIDATION_2026-09-19.md` / Phase D/E)

| Symbol | n | Win rate | Expectancy (gross, pts) | Cost model | Net |
|---|---|---|---|---|---|
| NATURALGAS | 57 | 0.386 | -0.0289 | OK | -6.83 pts total |
| CRUDEOIL | 40 | 0.450 | -3.1575 | OK | -200.10 pts total |
| BANKNIFTY | 35 | 0.571 | +1.2371 | UNCALIBRATED | not computed |
| NIFTY | 32 | 0.469 | -1.4016 | UNCALIBRATED | not computed |
| SENSEX | 15 | n/a | n/a | UNCALIBRATED | contaminated, excluded |

No result here is called "profitable" or "validated" beyond what the
evidence supports. BANKNIFTY's positive gross expectancy is explicitly
**not** confirmed distinguishable from noise (Phase E adversarial
review, n=35, confidence interval overlaps the losing population).

## Walk-Forward

**Not run this phase, explicitly.** A genuine train/validation/test
split requires a chronologically earlier, uncontaminated window that
was never touched by any bug-fix or parameter-tuning commit — the
`ZEROHERO_TRADING_EDGE_VALIDATION_2026-09-19.md` investigation already
established that the entire captured option-chain history (2026-09-02
to 2026-09-18) overlaps almost exactly with the contaminated live-trade
window and the tuning commits, so no such window currently exists.
`app/backtest/runner.py`'s existing chronological TRAIN/TEST harness
(confirmed real, tested, with genuine no-look-ahead bar-slicing) is the
correct tool to use once enough fresh, frozen-parameter data accumulates
going forward — fabricating a walk-forward result from the contaminated
window would be worse than not running one.

## OpenRouter AI Layer

`app/ai/openrouter_client.py` — the only OpenRouter client in this
codebase. Config via environment only (`OPENROUTER_API_KEY`,
`OPENROUTER_BASE_URL`, `OPENROUTER_FAST_MODEL`,
`OPENROUTER_REASONING_MODEL`, `OPENROUTER_FALLBACK_MODELS` [comma list],
`OPENROUTER_TIMEOUT_SEC`, `OPENROUTER_MAX_RETRIES`) — **none of these are
set in this environment's `.env`**, so `is_available()` is `False` and
every AI call this phase resolved to `UNAVAILABLE` without ever hitting
the network. This is the correct, safe default state, not a limitation
to fix.

Never raises: every failure mode (no key, network error, timeout,
non-200, malformed body, non-JSON content) returns a structured
`AIResult` with `status != "OK"` — tested explicitly in
`tests/test_openrouter_client.py` (12 tests, including one proving the
API key never appears in any result or log-adjacent string).

`app/behavior_engine.py` interprets (never recomputes) the indicators
already produced by `sr_engine`/`regime_mtf`/`state_classifier` into the
requested `{regime, trend, volatility, market_state, behavior_confidence,
signal_quality, risk_state, ai_required}` shape — a pure function, no
new indicator math.

`app/ai/behavior_ai.py` sends a compact, bounded JSON context (never a
raw DB dump) and strictly validates the model's JSON response against
the required schema — a response missing a key, using an invalid enum
value, or a non-numeric confidence is rejected (`SCHEMA_INVALID`), not
trusted.

`app/ai/fusion.py` enforces the authority rules: the deterministic
engine is authoritative; AI can only downgrade a BUY/SELL toward
NO_TRADE (or force NO_TRADE outright on `signal_validation=FAIL`), never
upgrade, never flip direction, never touch a deterministic NO_TRADE. A
real direction-handling bug (sell-side "downgrade" was moving toward
`STRONG_SELL` instead of `NO_TRADE`) was caught by
`tests/test_ai_behavior_and_fusion.py` and fixed before this phase
completed — evidence the test suite is doing real work, not padding.

`app/ai/shadow.py` + the new `shadow_decisions` table (Phase 12) run
this whole pipeline in shadow mode, opt-in, default OFF
(`ai_shadow_mode.enabled` in `AutoScalpRunner`'s config) — confirmed by
test to write zero rows when off, and to open the *exact same* paper
trade when on (purely observational).

`app/ai/metrics.py` — in-process counters (`ai_requests`, `ai_success`,
`ai_failures`, `ai_fallback_count`, average latency, `profile_usage`,
`regime_distribution`, `ai_rejections`,
`deterministic_vs_ai_disagreement`, `final_no_trade_count`). Resets on
process restart — this is a live-process gauge, not a persisted audit
trail (the persisted trail is `shadow_decisions`).

## Diagnostics API (all read-only, no secrets, no broker calls)

- `GET /api/profiles` — all 5 watchlist profiles
- `GET /api/profiles/{symbol}` — one profile, 404 for a symbol with no registry entry
- `GET /api/profiles/{symbol}/effective?regime=...&is_expiry_day=...` — deterministic merged config
- `GET /api/regimes` — all regime profiles
- `GET /api/calibration/status` — cost-model status per symbol
- `GET /api/data-quality/{symbol}` — contamination report (total/valid/contaminated/excluded/reasons)
- `GET /api/ai/status` — whether an AI key is configured + in-process metrics (never the key itself)

## Parameter Governance

Every configurable value used by the live decision path (that this
phase touched) now has an explicit `source` and `status` in
`app.instrument_profiles`/`app.regime_profiles` — no bare `if NIFTY:
threshold = 73` pattern was introduced. The one place a bare magic
number still lives is inside `scalp_strategy.py`/`option_engine.py`'s
own function-default arguments (e.g. `cfg.get("sl_atr", 1.1)`) — those
defaults are now *cited* as `_BASE` in this new module rather than
duplicated, but the numbers themselves were not moved (moving them would
have been a larger, riskier refactor of already-tested code, out of
scope for an additive governance layer).

## Fallback Behavior

Unknown symbol → `get_instrument_profile()` returns the common `_BASE`
defaults verbatim, status `UNKNOWN`, never a guessed or interpolated
value (tested). Unknown regime → falls through to `NORMAL_DAY` (no
override). AI unavailable/error/malformed → fusion treats it as neutral,
identical to "no AI layer exists at all" — never as permission to trade.

## How to Add a Future Instrument Safely

1. Add it to `app.instrument_profiles.REGISTRY` with `params={}` (no
   overrides) and `lot_size` VERIFIED against the live instrument master
   — never assumed.
2. Leave `cost_model_status` to resolve from
   `institutional_edge.costs.estimate_cost()` — it will correctly report
   UNCALIBRATED until a real contract-note-validated profile is added
   there.
3. Do **not** add instrument-specific ATR/EV overrides until a real
   closed-trade sample (cite the exact count and date range) justifies
   it, following the NATURALGAS/CRUDEOIL precedent — and even then,
   only after the resulting change is verified against an
   uncontaminated, frozen-parameter forward sample, not the same data
   used to derive it.

## Remaining Limitations (explicit, not hidden)

- No genuine walk-forward validation has been run (see above) — the
  data to run one honestly doesn't exist yet.
- NIFTY vs BANKNIFTY's apparent behavioral difference is unconfirmed
  (thin sample, overlapping confidence interval).
- SENSEX's routing fix is unvalidated against real post-fix data.
- No cost model exists for NIFTY/BANKNIFTY/SENSEX, and none was
  invented.
- The AI layer has never made a real API call in this environment (no
  key configured) — every test exercises it via mocks. Its real-world
  behavior against an actual OpenRouter model is unverified.
- `app.ai.metrics` is in-process only; it does not survive a restart.
