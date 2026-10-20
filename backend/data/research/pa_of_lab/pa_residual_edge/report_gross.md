# Residual Edge Analysis

**Status:** RESIDUAL_FRAGILE
**Decision eligibility:** REVIEW_REQUIRED
**Period:** 2021-01-04 to 2026-10-16 (1445 observations)
**Primary model:** market

## Primary Model

| Metric | Value |
|---|---:|
| R² | 0.0003 |
| Adjusted R² | -0.0004 |
| Annualized alpha | 0.3537% |
| Alpha HAC t-stat | 0.404 |
| Annualized residual volatility | 2.3526% |
| Residual edge ratio | 0.150 |

## Baseline Loadings

| Baseline | Loading | HAC t-stat | VIF |
|---|---:|---:|---:|
| market_return | 0.0027 | 0.412 | 1.000 |

## Model Sensitivity

| Model | Role | R² | Annualized alpha | Residual edge ratio | Status |
|---|---|---:|---:|---:|---|
| market | primary | 0.0003 | 0.3537% | 0.150 | RESIDUAL_FRAGILE |

## Evidence Warnings

- **HIGH — NOT_OUT_OF_SAMPLE:** Treat in-sample residual edge as exploratory until confirmed out of sample.
- **HIGH — NO_SENSITIVITY_MODELS:** No alternate baseline model was supplied; baseline-choice sensitivity is unknown.

## Regime Breakdown

### vol_regime

| Regime | N | Evidence | Annualized active return | Active Sharpe |
|---|---:|---|---:|---:|
| high | 420 | ADEQUATE | -1.5563% | -0.586 |
| low | 945 | ADEQUATE | 1.2013% | 0.609 |
| warmup | 80 | ADEQUATE | 0.3682% | 0.089 |

## Interpretation

- Residual evidence does not clear all robustness thresholds.
- Rolling alpha is positive in too few windows.
- No alternate baseline model was supplied.
- This report does not authorize a trade or an automatic exposure change.
- Confirm exploratory findings on untouched out-of-sample or live data.
