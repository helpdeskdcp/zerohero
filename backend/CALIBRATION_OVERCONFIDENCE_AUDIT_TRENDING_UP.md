# Calibration Overconfidence — TRENDING_UP subgroup attribution (re-run)

**Date:** 2026-09-07 · **Mode:** READ-ONLY. No production config, trading logic,
threshold, calibration parameter, frozen NIFTY setting, cron schedule, or
`live_trading` setting was changed. `curl /api/health` → `live_trading:false`.
Every number is a live read of `scalp_signals` / `app_settings`.

**Trigger:** `check_calibration_subgroups.sh` fired on Monday 2026-09-07 16:20
IST — the `regime=TRENDING_UP` subgroup crossed this report's own **n≥20
reliability floor** (n=23). In `CALIBRATION_OVERCONFIDENCE_AUDIT.md` (K8, 2026-09-05)
it was n=19, "one trade short of the floor, reported as a strong lead, not a
finding." This re-run does the full §3–§6 attribution now that it clears the bar.

Tracked as **K8** in `PRODUCTION_READINESS.md`.

---

## 0. Headline

| | K8 (2026-09-05) | this re-run (2026-09-07) |
|---|---|---|
| n (resolved LIVE AUTOSCALP, regime=TRENDING_UP) | 19 | **23** |
| avg predicted win | 59.8% | 59.9% |
| **actual win** | 26.3% | **30.4%** |
| **calibration gap** | **+33.4pp** | **+29.4pp** |
| clears n≥20 floor? | no (lead only) | **yes — this is now a finding** |

**Reliability (n=23):** Brier **0.315** — *worse* than the naive "always predict
the base rate" Brier of **0.212**. ECE **0.294**. Profit factor **0.50**,
expectancy **−1.31 pts/trade**. On TRENDING_UP the model is not just
overconfident, it is **actively worse than a constant base-rate guess**, and the
regime is **−EV** as traded.

The 4 new post-K8 trades (all CRUDEOIL, 2026-09-07, SUPPORT_REVERSAL) went 2W/2L,
which is why the gap eased from +33.4 → +29.4pp — not because the regime
improved, but because the new sample happened to be less bad. First-19 win-rate
26.3% (identical to K8), last-4 50.0%.

## 1. Score-bucket monotonicity — VIOLATED within TRENDING_UP

| signal_score bucket | n | actual win% | avg predicted |
|---|--:|--:|--:|
| 50–60 | 6 | 33.3% | 53.4% |
| 60–70 | **12** | **16.7%** ← worst, and the biggest bucket | 59.3% |
| 70–70 | 5 | 60.0% | 69.0% |

Same shape K8 found population-wide (60–70 bucket the worst), now concentrated in
TRENDING_UP. Higher score does **not** mean higher win probability here — the
core model assumption is empirically violated in this subgroup.

## 2. Feature-level attribution (n=23, component_scores parsed)

| component | corr vs **predicted probability** | corr vs **actual win** |
|---|--:|--:|
| retest | **+0.508** | −0.117 |
| mtf | **+0.385** | +0.211 |
| regime_conf | **+0.369** | −0.031 |
| vwap | +0.246 | −0.012 |
| option_quality | +0.239 | +0.021 |
| price_action | +0.227 | +0.024 |
| htf | +0.144 | +0.054 |
| momentum | +0.113 | +0.132 |
| atr | +0.093 | −0.157 |
| oi | +0.027 | +0.071 |
| volume | −0.126 | +0.131 |
| level_strength | **−0.338** | −0.182 |
| false_risk | constant (sd = 0) | constant |

**(A) What drives the score up in TRENDING_UP:** `retest` (+0.51), `mtf` (+0.39),
`regime_conf` (+0.37), `vwap`, `option_quality`, `price_action`.

**(B) What relates to actually winning:** essentially **nothing the model
weights**. The strongest score-driver, `retest` (+0.51 vs predicted), is
**negatively** related to winning (−0.12). `regime_conf` (+0.37 vs predicted) —
which K8 §2 Q6 already showed is *not even a scoring input* — has zero relation
to outcome (−0.03). `vwap`, `price_action`, `option_quality` all move the score
but not the outcome. The only components even weakly outcome-predictive are
`mtf` (+0.21) and `momentum` (+0.13). This is exactly K8 §4(B) — *"the components
that move the predicted probability up show essentially zero relationship with
what actually happens next"* — now **confirmed inside the TRENDING_UP subgroup at
n ≥ 20.**

**(C) `oi` is still dead here.** Non-zero on only **4 of 23** TRENDING_UP rows,
sd ≈ 0.008. K8 §3's "12 percentage points of state_score weight has contributed
zero real information" holds for this subgroup too.

**(D) `false_risk` is constant** across all 23 TRENDING_UP rows (sd = 0) — the
LIKELY_FALSE / fake-breakout penalty never fired for a single TRENDING_UP signal,
so it provides no discrimination in this regime.

## 3. Where the gap concentrates within TRENDING_UP

### by symbol
| symbol | n | avg predicted | actual win% | gap |
|---|--:|--:|--:|--:|
| NATURALGAS | 12 | 61.6% | 25.0% | **+36.6pp** |
| CRUDEOIL | 5 | 58.1% | 40.0% | +18.1pp |
| NIFTY | 4 | 56.1% | 25.0% | +31.1pp |
| BANKNIFTY | 1 | 48.7% | 100% | — |
| SENSEX | 1 | 74.4% | 0% | — |

**Every adequately-represented symbol loses in TRENDING_UP.** K8 read this as
"NATURALGAS is overconfident and TRENDING_UP is overconfident are substantially
the same signal." The 4 new non-NATURALGAS trades (NIFTY, CRUDEOIL — also
losing) **broaden it**: this is now more a **regime-wide** effect than a
NATURALGAS artifact.

### by signal_type (within TRENDING_UP)
| signal_type | n | avg predicted | actual win% | gap |
|---|--:|--:|--:|--:|
| SUPPORT_REVERSAL | **17** (74% of the regime) | 61.8% | 35.3% | +26.6pp |
| SUPPORT_BREAKDOWN | 4 | 56.4% | 25.0% | +31.4pp |
| RESISTANCE_REVERSAL | 2 | 49.8% | 0% | — |

### the K8 triples (cross-checked against `calibration_subgroups_state.json`)
| triple | K8 | now |
|---|---|---|
| NATURALGAS · TRENDING_UP · SUPPORT_REVERSAL | n=10, pred 63.7%, win 30% | **unchanged** — n=10, score 69.3, pred 63.7%, win 30% |
| NATURALGAS · TRENDING_DOWN · SUPPORT_BREAKDOWN | n=10, pred 58.2%, win 30% | still n=10 (below floor) |

Both NATURALGAS triples are still n=10 — **below the 20-floor**, so still leads,
not findings. The NATURALGAS·TRENDING_UP·SUPPORT_REVERSAL triple is ~59% of all
TRENDING_UP SUPPORT_REVERSAL and unchanged since K8.

## 4. Root cause (updated from K8 §6)

All four primary K8 causes still apply; the change is that **cause #1 is now
strongly supported instead of "supported by a sub-floor lead":**

1. **Regime/symbol blindness — ✅ strongly supported, now at n≥20.** Live
   calibration blob `live-2026-09-07-n80`: `global {k:1.0, b:0.1877, n:80}`,
   `curves: ["*|SUPPORT_BREAKDOWN"]` only. Every TRENDING_UP signal (76% of which
   are SUPPORT_REVERSAL, which has **no** fitted curve) is scored through the one
   **global** sigmoid. A per-key curve for `TRENDING_UP|SUPPORT_REVERSAL` (n=17)
   or `TRENDING_UP|*` (n=23) still **cannot be fitted** — `_MIN_ROWS = 40`. The
   regime with a +29.4pp gap is invisible to the model by construction.
2. **Correlated-feature inflation (§K8 Q5)** — ✅ still supported: `retest`,
   `mtf`, `htf` all read overlapping "clean trend" facts; in a trending-up tape
   they saturate together and stack the score without adding independent
   evidence — and `retest`, the biggest driver, is anti-correlated with the win.
3. **Scoring formula / saturation (§K8 §3, §4C)** — ✅ still supported: `oi` dead
   (4/23), `false_risk` constant (0/23), `regime_conf` co-varies with the score
   but is not a real input and not outcome-predictive.
4. **Calibration methodology (§K8 Q10)** — ✅ unchanged: the live recalibration
   still fits and grades on the same undifferentiated pool.
5. **Sample size** — the TRENDING_UP *regime* now clears the floor (n=23); the
   NATURALGAS *triples* do not (n=10). SUPPORT_BREAKDOWN got its own curve; the
   TRENDING_UP-dominant SUPPORT_REVERSAL did not.

## 5. What this re-run is / is not

**Is:** confirmation that K8's TRENDING_UP lead is now a **finding** at n≥20 — a
+29.4pp calibration gap, a Brier worse than base-rate, −EV as traded, driven by
score components (`retest`, `regime_conf`, `vwap`) that don't predict outcome,
under a global-only calibration curve that structurally cannot see the regime.

**Is not:** a fix. No weight, threshold, curve, config, cron, or `live_trading`
setting was changed. A `TRENDING_UP` per-regime curve needs `_MIN_ROWS = 40` (17
short for SUPPORT_REVERSAL); a stopgap score haircut / EV-gate tightening for
`regime == TRENDING_UP` would be an operator decision, not something this
read-only re-run implements.

**Operator note:** until a per-regime curve exists or TRENDING_UP is
down-weighted, TRENDING_UP AUTOSCALP signals are being sized off a probability
that overstates the true win-rate by ~30pp and the regime has run at PF 0.50 /
−1.31 pts/trade over 23 trades.
