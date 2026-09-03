"""
Black-Scholes greeks for the Expiry Zero-to-Hero research engine.

Every value produced here is a MODEL output, not broker data. AngelOne's
optionGreek endpoint returns AB9019 for SENSEX (BSE) — there are no broker
greeks for this instrument, live or historical — so Delta/Gamma/Theta/Vega/IV
are derived from the option premium + spot + strike + time-to-expiry.

Reuses the primitives already in app.engines.sr_engine (_norm_cdf, _bs_d1,
_bs_price, _solve_iv) and adds delta/theta/vega on top. Tag: source="MODEL:BS".
"""
from __future__ import annotations

import math

from ..engines.sr_engine import _bs_d1, _bs_price, _norm_cdf, _solve_iv

SOURCE = "MODEL:BS"
_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def year_fraction(minutes_to_expiry: float) -> float:
    """Calendar-time year fraction. Intraday options decay on wall-clock, so we
    use minutes/ (365.25*24*60). Below ~1 minute we floor at 30s to keep the
    greeks finite (they blow up exactly at T=0)."""
    m = max(0.5, float(minutes_to_expiry))
    return m / (365.25 * 24.0 * 60.0)


def implied_vol(spot, strike, minutes_to_expiry, premium, is_call, *, lo=0.02, hi=5.0):
    """Bisection IV from a mid/last premium. None if not bracketable (e.g. the
    premium is below intrinsic — common on a lagging expiry-day print)."""
    T = year_fraction(minutes_to_expiry)
    if spot is None or premium is None or T <= 0:
        return None
    return _solve_iv(float(spot), float(strike), T, float(premium), bool(is_call), lo=lo, hi=hi)


def greeks(spot, strike, minutes_to_expiry, sigma, is_call):
    """Return {delta, gamma, theta_per_day, theta_per_min, vega_per_volpt, d1}.
    theta is negative (decay). vega is per 1.00 change in sigma; divide by 100
    for 'per 1 IV point'. All MODEL:BS."""
    T = year_fraction(minutes_to_expiry)
    if spot is None or sigma is None or sigma <= 0 or T <= 0:
        return {k: None for k in ("delta", "gamma", "theta_per_day", "theta_per_min",
                                  "vega_per_volpt", "d1")}
    S, K = float(spot), float(strike)
    d1 = _bs_d1(S, K, T, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    nd1 = _npdf(d1)
    gamma = nd1 / (S * sigma * math.sqrt(T))
    vega = S * nd1 * math.sqrt(T)                       # per 1.00 sigma
    if is_call:
        delta = _norm_cdf(d1)
        theta_yr = -(S * nd1 * sigma) / (2.0 * math.sqrt(T))          # no-rate BS
    else:
        delta = _norm_cdf(d1) - 1.0
        theta_yr = -(S * nd1 * sigma) / (2.0 * math.sqrt(T))
    return {
        "delta": round(delta, 6),
        "gamma": round(gamma, 9),
        "theta_per_day": round(theta_yr / 365.25, 4),
        "theta_per_min": round(theta_yr / (365.25 * 24.0 * 60.0), 6),
        "vega_per_volpt": round(vega / 100.0, 4),
        "d1": round(d1, 4),
    }


def price(spot, strike, minutes_to_expiry, sigma, is_call):
    T = year_fraction(minutes_to_expiry)
    if spot is None or sigma is None or T <= 0:
        return None
    return round(_bs_price(float(spot), float(strike), T, float(sigma), bool(is_call)), 4)


def decompose_move(*, S0, S1, K, is_call, mins0, mins1, prem0, prem1,
                   sigma0=None, sigma1=None, d_iv=None):
    """PHASE 11 — empirical decomposition of an observed premium move ΔP into
    interpretable BS components, evaluated at the START of the move (so it is a
    genuine *predictor*, no look-ahead into prem1). Returns a dict of MODEL
    contributions + the residual vs the ACTUAL ΔP.

        ΔP_obs      = prem1 - prem0                       (ACTUAL)
        ΔS          = S1 - S0                             (ACTUAL)
        Δt_min      = mins0 - mins1                       (elapsed minutes)
        delta_term  = delta(S0) * ΔS
        gamma_term  = 0.5 * gamma(S0) * ΔS**2
        theta_term  = theta_per_min(S0) * Δt_min          (<=0)
        vega_term   = vega_per_volpt(S0) * d_iv           (d_iv in vol points)
        intrinsic_conv = max(K-S1,0) - max(K-S0,0)   for a put (settlement pull)
        residual    = ΔP_obs - (delta+gamma+theta+vega)
    """
    dP = prem1 - prem0
    dS = S1 - S0
    dt = max(0.0, mins0 - mins1)
    if sigma0 is None:
        sigma0 = implied_vol(S0, K, mins0, prem0, is_call)
    g0 = greeks(S0, K, mins0, sigma0, is_call)
    delta_term = (g0["delta"] or 0.0) * dS
    gamma_term = 0.5 * (g0["gamma"] or 0.0) * dS * dS
    theta_term = (g0["theta_per_min"] or 0.0) * dt
    vega_term = (g0["vega_per_volpt"] or 0.0) * (d_iv or 0.0)
    bs_sum = delta_term + gamma_term + theta_term + vega_term
    intr0 = max(0.0, (K - S0) if not is_call else (S0 - K))
    intr1 = max(0.0, (K - S1) if not is_call else (S1 - K))
    return {
        "dP_observed": round(dP, 2),
        "dS": round(dS, 2),
        "dt_min": round(dt, 2),
        "sigma_start": round(sigma0, 4) if sigma0 else None,
        "delta_start": g0["delta"],
        "gamma_start": g0["gamma"],
        "delta_term": round(delta_term, 2),
        "gamma_term": round(gamma_term, 2),
        "theta_term": round(theta_term, 2),
        "vega_term": round(vega_term, 2),
        "bs_sum": round(bs_sum, 2),
        "intrinsic_conversion": round(intr1 - intr0, 2),
        "residual_vs_observed": round(dP - bs_sum, 2),
        "effective_delta": round(dP / dS, 4) if dS else None,
        "source": SOURCE,
    }
