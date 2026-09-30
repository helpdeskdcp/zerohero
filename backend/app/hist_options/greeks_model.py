"""Historical Greek reconstruction with explicit provenance (spec section 4).

Never labels a computed value as a broker value: every result carries
greek_source in {DIRECT, RECONSTRUCTED, UNAVAILABLE}, the model name, the exact
inputs used and the calculation timestamp. Missing/invalid inputs yield
UNAVAILABLE with a reason -- never a guess.

Models
  BS_CARRY  European Black-Scholes-Merton with continuous rate r and yield q,
            for cash-index options (spot underlying).
  BLACK76   European option on a FUTURE (commodity options); underlying is the
            futures price, carry is embedded in it.
Assumptions (documented, not verified against exchange rules): European
exercise; constant r and q over the option life; IV taken as given (direct
from the broker's historical series where available). MCX options on futures
are treated as European -- unverified; if American-style features matter, these
values are approximations and must not be used for exercise-sensitive logic.

Scaling matches the broker convention already used in option_greeks:
vega per 1 vol POINT, theta per CALENDAR DAY, delta/gamma raw.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

DIRECT, RECONSTRUCTED, UNAVAILABLE = "DIRECT", "RECONSTRUCTED", "UNAVAILABLE"
MODELS = ("BS_CARRY", "BLACK76")
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


def _pdf(x):
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


def _cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pos(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if f == f and f > 0 else None


def _unavailable(reason, inputs):
    return {"greek_source": UNAVAILABLE, "reason": reason, "delta": None, "gamma": None,
            "theta": None, "vega": None, "model": None, "inputs": inputs,
            "calculation_timestamp": datetime.now(timezone.utc).isoformat()}


def reconstruct(S, K, T, iv, is_call: bool, *, model: str, r: float = 0.0, q: float = 0.0,
                iv_source: str = "UNKNOWN") -> dict:
    """S = spot (BS_CARRY) or futures price (BLACK76); T in years; iv decimal
    (0.18 = 18%). Returns a dict with delta/gamma/theta/vega + provenance."""
    inputs = {"S": S, "K": K, "T": T, "iv": iv, "is_call": bool(is_call), "model": model,
              "r": r, "q": q if model == "BS_CARRY" else None, "iv_source": iv_source}
    if model not in MODELS:
        return _unavailable("UNKNOWN_MODEL", inputs)
    S_, K_, T_, v = _pos(S), _pos(K), _pos(T), _pos(iv)
    if S_ is None or K_ is None or T_ is None or v is None:
        return _unavailable("MISSING_OR_NONPOSITIVE_INPUT", inputs)
    if v > 5.0:
        return _unavailable("IV_OUT_OF_RANGE", inputs)
    sq = math.sqrt(T_)
    vs = v * sq
    disc = math.exp(-r * T_)
    if model == "BS_CARRY":
        eq = math.exp(-q * T_)
        d1 = (math.log(S_ / K_) + (r - q + 0.5 * v * v) * T_) / vs
        d2 = d1 - vs
        pdf = _pdf(d1)
        delta = eq * _cdf(d1) if is_call else -eq * _cdf(-d1)
        gamma = eq * pdf / (S_ * vs)
        vega = S_ * eq * pdf * sq
        common = -S_ * eq * pdf * v / (2.0 * sq)
        theta = (common - r * K_ * disc * _cdf(d2) + q * S_ * eq * _cdf(d1)) if is_call else \
                (common + r * K_ * disc * _cdf(-d2) - q * S_ * eq * _cdf(-d1))
    else:  # BLACK76
        d1 = (math.log(S_ / K_) + 0.5 * v * v * T_) / vs
        d2 = d1 - vs
        pdf = _pdf(d1)
        delta = disc * _cdf(d1) if is_call else -disc * _cdf(-d1)
        gamma = disc * pdf / (S_ * vs)
        vega = disc * S_ * pdf * sq
        val = disc * (S_ * _cdf(d1) - K_ * _cdf(d2)) if is_call else disc * (K_ * _cdf(-d2) - S_ * _cdf(-d1))
        theta = r * val - disc * S_ * pdf * v / (2.0 * sq)
    return {"greek_source": RECONSTRUCTED, "reason": None, "model": model,
            "delta": delta, "gamma": gamma, "vega": vega * 0.01, "theta": theta / 365.0,
            "inputs": inputs, "calculation_timestamp": datetime.now(timezone.utc).isoformat()}


def price(S, K, T, iv, is_call: bool, *, model: str, r: float = 0.0, q: float = 0.0):
    """Model price (test/IV-solver helper). None on invalid input."""
    S_, K_, T_, v = _pos(S), _pos(K), _pos(T), _pos(iv)
    if None in (S_, K_, T_, v):
        return None
    vs = v * math.sqrt(T_)
    disc = math.exp(-r * T_)
    if model == "BS_CARRY":
        eq = math.exp(-q * T_)
        d1 = (math.log(S_ / K_) + (r - q + 0.5 * v * v) * T_) / vs
        d2 = d1 - vs
        return S_ * eq * _cdf(d1) - K_ * disc * _cdf(d2) if is_call else K_ * disc * _cdf(-d2) - S_ * eq * _cdf(-d1)
    d1 = (math.log(S_ / K_) + 0.5 * v * v * T_) / vs
    d2 = d1 - vs
    return disc * (S_ * _cdf(d1) - K_ * _cdf(d2)) if is_call else disc * (K_ * _cdf(-d2) - S_ * _cdf(-d1))


def direct(delta=None, gamma=None, theta=None, vega=None, *, source: str) -> dict:
    """Wrap broker-supplied Greeks (live chain) with DIRECT provenance."""
    ok = any(x is not None for x in (delta, gamma, theta, vega))
    return {"greek_source": DIRECT if ok else UNAVAILABLE, "reason": None if ok else "NOT_SUPPLIED",
            "delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "model": None,
            "inputs": {"source": source}, "calculation_timestamp": None}
