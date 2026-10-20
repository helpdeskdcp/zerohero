# Residual Edge Analysis

**Status:** RESIDUAL_FRAGILE
**Decision eligibility:** REVIEW_REQUIRED
**Period:** 2021-01-04 to 2026-10-16 (1445 observations)
**Primary model:** market

## Primary Model

| Metric | Value |
|---|---:|
| R² | 0.0022 |
| Adjusted R² | 0.0016 |
| Annualized alpha | -7.5069% |
| Alpha HAC t-stat | -7.952 |
| Annualized residual volatility | 2.4860% |
| Residual edge ratio | -3.020 |

## Baseline Loadings

| Baseline | Loading | HAC t-stat | VIF |
|---|---:|---:|---:|
| market_return | 0.0077 | 1.199 | 1.000 |

## Model Sensitivity

| Model | Role | R² | Annualized alpha | Residual edge ratio | Status |
|---|---|---:|---:|---:|---|
| market | primary | 0.0022 | -7.5069% | -3.020 | RESIDUAL_FRAGILE |

## Evidence Warnings

- **HIGH — NOT_OUT_OF_SAMPLE:** Treat in-sample residual edge as exploratory until confirmed out of sample.
- **HIGH — NO_SENSITIVITY_MODELS:** No alternate baseline model was supplied; baseline-choice sensitivity is unknown.

## Regime Breakdown

### vol_regime

| Regime | N | Evidence | Annualized active return | Active Sharpe |
|---|---:|---|---:|---:|
| high | 420 | ADEQUATE | -11.3502% | -4.018 |
| low | 945 | ADEQUATE | -5.1651% | -2.457 |
| warmup | 80 | ADEQUATE | -14.9917% | -3.632 |

## Interpretation

- Residual evidence does not clear all robustness thresholds.
- Rolling alpha is positive in too few windows.
- No alternate baseline model was supplied.
- This report does not authorize a trade or an automatic exposure change.
- Confirm exploratory findings on untouched out-of-sample or live data.
