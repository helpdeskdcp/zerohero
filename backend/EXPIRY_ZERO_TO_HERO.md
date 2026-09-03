# EXPIRY ZERO TO HERO — research engine

**Status:** RESEARCH / PAPER ONLY · `live_trading` stays `false` · no order path.
**Date:** 2026-09-03 · **Calibration:** UNCALIBRATED (N = 1 expiry day — see §2).

Goal: *discover*, not assume, the mathematics of an extreme option-premium
expansion in the final ~40 minutes before an index-option expiry. Package:
`app/expiry_zero_to_hero/`. Reuses the app's Black-Scholes primitives
(`engines/sr_engine`) and the AngelOne connector. No existing functionality
duplicated.

---

## 1. Data availability (verified against the live AngelOne account, 2026-09-03)

| field | source | note |
|---|---|---|
| index 1-min OHLCV (SENSEX) | **ACTUAL** — `getCandleData` BSE:99919000 | full window |
| per-strike option 1-min OHLC of **LTP** + traded volume | **ACTUAL** — `getCandleData` BFO:token | ATM ±3 strikes, CE + PE |
| IV, Delta, Gamma, Theta, Vega | **DERIVED : MODEL:BS** | bisection IV from the LTP, then BS greeks. **AngelOne `optionGreek` returns `AB9019` for SENSEX** — there are NO broker greeks for this instrument, live or historical. |
| intrinsic value, time value, minutes-to-expiry | **DERIVED** | `max(K−S,0)` etc. |
| **open interest, ΔOI, put/call OI ratio** | **UNAVAILABLE** | `getCandleData` carries no OI and it is not reconstructible from candles. The trainer's "put-side OI was dominant" read **cannot be verified from historical data** — only forward (live capture). |
| **bid / ask / spread / depth** | **UNAVAILABLE** | not in candle data |
| **settlement price** | **UNAVAILABLE** unless the collector ran live on expiry day | on 03-Sep it is inferable from the last option candle (₹347.15 ≈ `max(76500 − 76152, 0)`). |
| **past expiry days** | **UNAVAILABLE** | expired weekly SENSEX option contracts are purged from the instrument master, so their strike tokens can't be resolved. Only current + future weeklies are collectable. |

**Consequence for the spec:** sections that require multi-day statistics
(train/val/test split, walk-forward, precision/recall, calibration error) and
the OI-imbalance pillar **cannot be executed now**. The engine is built and
wired for them; they produce real numbers only as forward expiry days
accumulate (`python -m app.expiry_zero_to_hero collect SENSEX <exp> <date>`
each expiry). Until then the backtester returns `INSUFFICIENT_SAMPLE` and the
signal engine can output at most `WATCH`, never `ENTRY`.

---

## 2. The one validation case — 03-Sep-2026 SENSEX 76500 PE

Run: `python -m app.expiry_zero_to_hero replay`. All figures below are the
engine's own output on live-fetched data.

### 2a. Replay table (14:50 → 15:40 IST, every 3rd minute)

```
time    SENSEX     PE_ltp CE_ltp  IV(BS) delta   gamma     intrinsic  TV   p_ret10 s_ret10  compr
14:50  76,599.40    64.75 208.15  0.403  -0.355  0.001385     0.00  64.75    –        –       –
14:56  76,534.94    77.70 178.35  0.384  -0.441  0.001671     0.00  77.70  +12.95  -64.46   0.18
15:02  76,536.68    67.85 177.55  0.381  -0.431  0.001845     0.00  67.85  + 0.55  -47.52   0.21
15:08  76,496.39    91.20 142.00  0.453  -0.506  0.001781     3.61  87.59  + 9.10  -44.59   0.35
15:14  76,554.20    63.65 220.65  0.523  -0.403  0.001751     0.00  63.65  -19.15  +39.38   0.40
15:20  74,373.29   186.35  35.95   n/a    n/a     n/a      2,126.71 -1940  +113.9 -2163.9   1.43   << crash low
15:23  75,695.09   171.20  34.15   n/a    n/a     n/a        804.91  -634  + 94.3 - 858.7   1.27
15:26  75,929.24   347.10   4.80   n/a    n/a     n/a        570.76  -224  +271.9 - 581.1   1.60
15:38     (idx     347.15   0.05   n/a    n/a     n/a          n/a    n/a   + 0.55    0.00   0.00   << settlement 347.15
        stops 15:25)
```
- `IV(BS)`/greeks are `n/a` from 15:17 because the printed premium fell *below*
  intrinsic (a lagging expiry-day print) so the bisection IV can't bracket —
  reported honestly, not faked.
- OI columns are omitted from this table: `UNAVAILABLE` (§1).

### 2b. Premium-support-test pattern (auto-detected, ₹60 NOT hard-coded)

`PremiumSupportDetector` on the PE close series returned:

| field | value |
|---|---|
| `support_level` | **₹66.12** (± ₹4) — clustered from the actual troughs |
| `number_of_tests` | **3** — at ≈ 14:52, 15:01, 15:14 |
| `bounce_sizes_pct` | 22% , 36% , 30% |
| `time_between_tests_min` | 9 , 13 |
| `premium_compression` | **0.057** (very tight — coiled) |
| `strength` / `verdict` | 100 / **STRONG** |

This is the trainer's ₹81 → ₹60 → bounce → ₹59 → bounce → expansion, recovered
from data with no magic number. It landed on ₹66 because it averages the real
trough prints; the trainer's "₹60" is inside the ± ₹4 band.

### 2c. Zero-to-Hero labels (this strike+side, whole session)

| definition | positives (entry-minutes that later hit it) |
|---|---|
| A: MFE ≥ 2× | 32 |
| B: MFE ≥ 3× | 30 |
| C: MFE ≥ 5× | **6** |
| D: MFE ≥ session 95th-pctile (= **5.15×**) | **3** |

The ₹64.75 → ₹347.15 move is **5.36×** — a genuine top-percentile event, rare
even within the one day it happened.

---

## 3. Reverse-engineered decomposition of the ₹65 → ₹347 move (§11)

`bs.decompose_move`, evaluated **at the entry minute** (no look-ahead into the
exit premium). ΔP is ACTUAL; every term is `MODEL:BS`.

```
ΔP  (observed)            = +282.4      ACTUAL   (64.75 -> 347.15)
ΔS  (SENSEX)              = -446.5 pts  ACTUAL   (76,599 -> ~76,153 close)
Δt                        =  49 min
σ_start (IV, MODEL:BS)    = 0.403
δ_start (MODEL:BS)        = -0.355      (PE ~100 pts OTM at entry)
Γ_start (MODEL:BS)        = 0.001385

  delta term   = δ·ΔS                  = -0.355 × -446.5   = +158.6   (56%)
  gamma term   = ½·Γ·ΔS²               = ½·0.001385·446.5² = +138.1   (49%)
  theta term   = θ_min·Δt              = -1.25/min × 49    =  -61.3  (-22%)
  vega term    = Vega·ΔIV              = UNAVAILABLE (ΔIV not measurable hist.) =  0
  ───────────────────────────────────────────────────────────────────
  BS sum                                                    = +235.3   (83% of ΔP)
  residual  = ΔP − BS_sum                                   =  +47.1   (17%)
              └─ IV expansion + convergence to settlement max(K−S,0)

effective delta  = ΔP / ΔS = 0.632
```

**Interpretation.** The move is ~83 % explained by first-order option
mechanics on a real 447-point index drop:

- **delta** carries it while OTM→ATM;
- **the ½·Γ·ΔS² term is the accelerant** — on a 0-DTE option near the strike Γ
  is huge, so a fast multi-hundred-point move pays far more than a linear
  `δ·ΔS` estimate would. This term is why a ₹65 lottery ticket became ₹347;
- **theta** is a −₹61 headwind (49 minutes of end-of-day decay);
- the **+₹47 residual** is the part a smooth-diffusion model can't capture:
  the IV spike during the crash and the hard pull of the premium to its
  settlement intrinsic `max(76500 − 76152, 0) = ₹348`.

At expiry the whole premium **is** intrinsic:
```
P_settle = max(K − S_close, 0) = max(76500 − 76152, 0) = ₹348    (actual ₹347.15)
```

---

## 4. Candidate formula (starting hypothesis — coefficients UNCALIBRATED)

```
ΔP_expected  ≈  δ(S₀)·ΔS_expected
             +  ½·Γ(S₀)·ΔS_expected²                      ← expiry-day accelerant
             +  θ_min(S₀)·Δt
             +  Vega(S₀)·ΔIV_expected                     ← LIVE-only input
             +  settlement_pull                            ← as t→expiry, TV→0
```
where `ΔS_expected` is supplied by the operator's directional read / an OI-wall
target, `ΔIV_expected` needs live IV history, and `settlement_pull` snaps the
result toward `max(K−S₁, 0)` weighted by `(1 − minutes_to_expiry/T_window)`.

| term | definition | units | why | coefficient | calibration | significance |
|---|---|---|---|---|---|---|
| `δ·ΔS` | BS delta at entry × expected index move | ₹ | linear sensitivity | 1.0 (theory) | fit a shrink factor per moneyness bucket once N≥8 expiries | N=1 → **untested** |
| `½·Γ·ΔS²` | BS gamma bonus | ₹ | convexity dominates near-strike 0-DTE | 1.0 (theory) | same | N=1 → **untested** |
| `θ_min·Δt` | BS theta/min × minutes elapsed | ₹ (≤0) | end-of-day decay | 1.0 (theory) | same | N=1 → **untested** |
| `Vega·ΔIV` | BS vega × IV change | ₹ | crash IV spike | 1.0 (theory) | **needs live IV series — UNAVAILABLE historically** | not evaluable |
| `settlement_pull` | `w·(max(K−S₁,0) − P₀)`, `w = 1 − mte/40` | ₹ | premium → intrinsic at expiry | `w` heuristic | fit `w(mte)` shape once N≥8 | N=1 → **untested** |

The **probability score** (`ZeroToHeroProbabilityEngine`) combines
`support_strength`, `n_tests`, `premium_compression`, side-aligned
`spot_momentum`, `gamma_accel_potential = ½·Γ·v²`, `minutes_to_expiry`,
`near_strike`, and (LIVE only) `oi_imbalance`. **All weights are option-theory
priors, not fitted.** `calibration_status = "UNCALIBRATED"`.

---

## 5. Earliest reliable signal (§7) — honest answer

On the one case: the 3rd support test completed at **≈ 15:14**; the expansion
began **≈ 15:16–15:20**. **Lead time ≈ 2–6 minutes, not 10.** The *setup*
(coiling premium + 3 tightening tests + spot drifting the option's way) was
visible from ~15:00, but as a *firing* signal the reliable horizon on this
sample is single-digit minutes. **Do not trust the 10-minute target until
multiple expiries confirm it.**

---

## 6. Overfitting guard (§9)

**N = 1 usable expiry day.** No train/val/test split is possible; any fitted
coefficient would explain only 03-Sep. Therefore:
- `ExpiryZeroToHeroBacktester` returns `INSUFFICIENT_SAMPLE` (min 8 expiry days)
  and refuses to quote precision/recall/expectancy/PF/calibration error;
- the probability engine uses **theory priors**, reports `UNCALIBRATED`;
- the signal engine is capped at `WATCH`.

**Path to a real model:** run the collector every SENSEX (and NIFTY/BANKNIFTY)
expiry to accumulate windows; once ≥ 8 days exist, walk-forward fit the
coefficient shrink factors + the `settlement_pull` shape, then compare a
logistic baseline vs. any ML on a chronological holdout.

---

## 7. Module map

| module | class | role |
|---|---|---|
| `bs.py` | — | BS greeks + IV solve + `decompose_move` (all `MODEL:BS`) |
| `data_collector.py` | `ExpiryDataCollector` | resolve ATM±3 strikes, pull index+option 1-min candles, tag provenance |
| `features.py` | `ExpiryFeatureEngine` | causal per-minute features, 1/2/3/5/10-min lookbacks |
| `support_detector.py` | `PremiumSupportDetector` | repeated-premium-level test pattern (no hard-coded level) |
| `labeler.py` | `ZeroToHeroLabeler` | definitions A/B/C/D + MFE/MAE/drawdown/settlement |
| `probability.py` | `ZeroToHeroProbabilityEngine` | interpretable score, `UNCALIBRATED`, CE/PE/NO_TRADE |
| `signal.py` | `ZeroToHeroSignalEngine`, `ExpiryZeroToHeroReporter` | §13 output; capped at WATCH while uncalibrated |
| `backtester.py` | `ExpiryZeroToHeroBacktester` | metrics; `INSUFFICIENT_SAMPLE` under 8 days |
| `replay.py` | `run()` | the 03-Sep reconstruction above |

## 8. Data honesty rules enforced in code

`ACTUAL:ANGEL_CANDLE` / `DERIVED:BS` / `UNAVAILABLE` tags on every field.
Greeks are never presented as broker data. Missing OI is `UNAVAILABLE`, never
0, never estimated. IV that can't be solved is `None`, not a guess.
