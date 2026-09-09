"""
Self-contained Black-Scholes (r = q = 0) -- a FALLBACK only.

Used by `analytics` / `structure` when a source has no Greeks, so the skew /
GEX blocks can still run off LTP + a solved IV. When a source ships broker
Greeks (angelone_captured, upstox_open) those are used as-is and this module is
not touched.

Scaling matches the broker convention in `option_greeks`:
  * `vega`  is per 1 volatility POINT  (d price / d sigma * 0.01)
  * `theta` is per CALENDAR DAY        (d price / d t   / 365)
  * `delta`, `gamma` are raw

Every function is pure and never raises on ordinary bad input (returns None).
"""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone

_SQRT2 = math.sqrt(2.0)
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)
MIN_T = 1.0 / (365.0 * 24.0 * 12.0)          # 5 minutes, floor so ATM gamma stays finite
_IST = timezone(timedelta(hours=5, minutes=30))


def norm_pdf(x: float) -> float:
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _num(x):
    try:
        f = float(x)
        return f if f == f and math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def year_fraction(expiry: str, now: datetime | None = None) -> float | None:
    """Calendar year-fraction to the 15:30 IST expiry close. `expiry` in
    '15SEP2026' / '15-SEP-2026' / '2026-09-15'."""
    d = None
    for f in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(str(expiry).upper(), f).date()
            break
        except ValueError:
            continue
    if d is None:
        return None
    exp_dt = datetime.combine(d, time(15, 30), tzinfo=_IST)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    secs = (exp_dt - now).total_seconds()
    return max(MIN_T, secs / (365.0 * 86400.0))


def d1_d2(S, K, T, sigma):
    S, K, T, sigma = _num(S), _num(K), _num(T), _num(sigma)
    if not (S and K and T and sigma) or S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return None
    vs = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / vs
    return d1, d1 - vs


def price(S, K, T, sigma, is_call: bool) -> float | None:
    dd = d1_d2(S, K, T, sigma)
    if dd is None:
        return None
    d1, d2 = dd
    if is_call:
        return S * norm_cdf(d1) - K * norm_cdf(d2)
    return K * norm_cdf(-d2) - S * norm_cdf(-d1)


def greeks(S, K, T, sigma, is_call: bool) -> dict | None:
    dd = d1_d2(S, K, T, sigma)
    if dd is None:
        return None
    d1, d2 = dd
    S, K, T, sigma = float(S), float(K), float(T), float(sigma)
    sqrtT = math.sqrt(T)
    pdf = norm_pdf(d1)
    delta = norm_cdf(d1) if is_call else norm_cdf(d1) - 1.0
    gamma = pdf / (S * sigma * sqrtT)
    vega_raw = S * pdf * sqrtT
    theta_raw = -(S * pdf * sigma) / (2.0 * sqrtT)          # r = 0 -> no carry term
    return {"delta": delta, "gamma": gamma,
            "vega": vega_raw * 0.01, "theta": theta_raw / 365.0}


def implied_vol(mkt_price, S, K, T, is_call: bool, *, lo=0.01, hi=5.0,
                tol=1e-5, iters=80) -> float | None:
    mp, S, K, T = _num(mkt_price), _num(S), _num(K), _num(T)
    if not (mp and S and K and T) or mp <= 0 or T <= 0:
        return None
    intrinsic = max(0.0, (S - K) if is_call else (K - S))
    if mp <= intrinsic + 1e-9 or mp >= S:                   # no arbitrage-free IV
        return None
    plo = price(S, K, T, lo, is_call)
    phi = price(S, K, T, hi, is_call)
    if plo is None or phi is None or (plo - mp) * (phi - mp) > 0:
        return None
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        pm = price(S, K, T, mid, is_call)
        if pm is None:
            return None
        if abs(pm - mp) < tol:
            return mid
        if (pm - mp) * (plo - mp) < 0:
            hi = mid
        else:
            lo, plo = mid, pm
    return 0.5 * (lo + hi)
