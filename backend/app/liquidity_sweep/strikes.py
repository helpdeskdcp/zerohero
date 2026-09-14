"""
ITM strike selection + ranking -- sections 5, 6, 11, 20.

A new module, not a reuse of app.engines.option_engine.select_option: that
function is ATM-centric (its own delta_fit rewards |delta| in [0.35,0.62],
picks by proximity to spot) and is on the LIVE autoscalp decision path --
this brief specifically wants an ITM-constrained, configurable-delta-band
selector, a materially different rule, so a fresh module avoids either
duplicating logic under a different rule or editing a live-path file's
existing, tuned selection criteria.

StrikeScore, per section 6, every component normalized to [0,1] before
weighting so the weights are genuinely comparable:
  StrikeScore = w1*ITM_FIT + w2*DELTA_FIT + w3*LIQUIDITY_SCORE
              + w4*SPREAD_SCORE + w5*VOLUME_SCORE + w6*OI_SCORE
              - w7*SLIPPAGE_RISK - w8*PREMIUM_RISK
Default weights are equal (1/8 each on the positive terms, matching this
session's own "don't invent unjustified coefficients" practice elsewhere --
see app.structural_break.break_score's identical reasoning) -- ALL weights
are constructor/call parameters, per the brief's own "weights must be
configurable."
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

DEFAULT_WEIGHTS = {
    "itm_fit": 1.0, "delta_fit": 1.0, "liquidity": 1.0, "spread": 1.0,
    "volume": 1.0, "oi": 1.0, "slippage_risk": 1.0, "premium_risk": 1.0,
}

# Hard filters (section 5) -- plain, documented, round-number defaults.
MAX_SPREAD_PCT = 3.0
MIN_VOLUME = 100
MIN_OI = 500
MIN_PREMIUM = 1.0


def _num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


@dataclass
class StrikeCandidate:
    strike: float
    option_type: str
    status: str                # "ELIGIBLE" | "REJECTED"
    score: float | None = None
    reject_reason: str | None = None
    components: dict | None = None
    raw: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _eligible(leg: dict, *, spot: float, option_type: str, delta_band: tuple[float, float],
              max_spread_pct: float, min_volume: float, min_oi: float, min_premium: float) -> str | None:
    """Returns a rejection reason string, or None if eligible."""
    strike = _num(leg.get("strike"))
    ltp = _num(leg.get("ltp"))
    delta = _num(leg.get("delta"))
    bid, ask = _num(leg.get("bid")), _num(leg.get("ask"))
    volume, oi = _num(leg.get("volume")), _num(leg.get("oi"))

    if strike is None:
        return "missing strike"
    if option_type == "CE" and not (strike < spot):
        return "not ITM (CE strike must be below spot)"
    if option_type == "PE" and not (strike > spot):
        return "not ITM (PE strike must be above spot)"
    if ltp is None or ltp < min_premium:
        return "invalid or too-small premium"
    if delta is None:
        return "missing delta -- cannot verify ITM depth"
    lo, hi = delta_band
    if not (lo <= abs(delta) <= hi):
        return f"delta {abs(delta):.2f} outside configured band [{lo},{hi}]"
    if bid is None or ask is None or bid <= 0:
        return "missing/invalid bid-ask -- cannot assess spread"
    spread_pct = (ask - bid) / ((ask + bid) / 2) * 100 if (ask + bid) else None
    if spread_pct is None or spread_pct > max_spread_pct:
        return f"spread {spread_pct}% > {max_spread_pct}%" if spread_pct is not None else "invalid spread"
    if volume is None or volume < min_volume:
        return f"volume {volume} < minimum {min_volume}"
    if oi is None or oi < min_oi:
        return f"OI {oi} < minimum {min_oi}"
    return None


def _score(leg: dict, *, spot: float, option_type: str, delta_band: tuple[float, float],
           weights: dict, candidates_ref: dict) -> tuple[float, dict]:
    strike = _num(leg.get("strike"))
    delta = abs(_num(leg.get("delta")) or 0)
    ltp = _num(leg.get("ltp")) or 0
    bid, ask = _num(leg.get("bid")) or 0, _num(leg.get("ask")) or 0
    volume, oi = _num(leg.get("volume")) or 0, _num(leg.get("oi")) or 0

    step = candidates_ref.get("strike_step", 50.0)
    itm_depth = abs(spot - strike)
    itm_fit = max(0.0, 1.0 - itm_depth / (candidates_ref.get("max_itm_depth", 10 * step)))

    lo, hi = delta_band
    mid = (lo + hi) / 2
    delta_fit = max(0.0, 1.0 - abs(delta - mid) / max(1e-6, (hi - lo) / 2))

    max_vol, max_oi = candidates_ref.get("max_volume", 1.0), candidates_ref.get("max_oi", 1.0)
    volume_score = min(1.0, volume / max_vol) if max_vol else 0.0
    oi_score = min(1.0, oi / max_oi) if max_oi else 0.0
    liquidity = (volume_score + oi_score) / 2

    spread_pct = (ask - bid) / ((ask + bid) / 2) * 100 if (ask + bid) else 100.0
    spread_score = max(0.0, 1.0 - spread_pct / MAX_SPREAD_PCT)
    slippage_risk = min(1.0, spread_pct / (2 * MAX_SPREAD_PCT))
    premium_risk = 0.0 if ltp > 0 else 1.0

    components = {"itm_fit": itm_fit, "delta_fit": delta_fit, "liquidity": liquidity,
                 "spread": spread_score, "volume": volume_score, "oi": oi_score,
                 "slippage_risk": slippage_risk, "premium_risk": premium_risk}
    raw = (weights["itm_fit"] * itm_fit + weights["delta_fit"] * delta_fit
           + weights["liquidity"] * liquidity + weights["spread"] * spread_score
           + weights["volume"] * volume_score + weights["oi"] * oi_score
           - weights["slippage_risk"] * slippage_risk - weights["premium_risk"] * premium_risk)
    positive_weight_sum = sum(v for k, v in weights.items() if k not in ("slippage_risk", "premium_risk"))
    normalized = max(0.0, min(100.0, 100.0 * raw / positive_weight_sum)) if positive_weight_sum else 0.0
    return round(normalized, 2), {k: round(v, 3) for k, v in components.items()}


def rank_strikes(candidates: list[dict], *, spot: float, option_type: str,
                 delta_band: tuple[float, float] = (0.60, 0.70),
                 weights: dict | None = None, max_spread_pct: float = MAX_SPREAD_PCT,
                 min_volume: float = MIN_VOLUME, min_oi: float = MIN_OI,
                 min_premium: float = MIN_PREMIUM, strike_step: float = 50.0) -> list[StrikeCandidate]:
    """`candidates`: raw option-chain legs (dicts with strike/ltp/bid/ask/
    delta/oi/volume/...) for ONE option_type. Returns ALL candidates
    (eligible ranked first by score desc, then rejected with reasons) --
    section 6's own "return... reason selected / reason rejected" for
    every candidate, not just the winner."""
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    eligible_raw = [c for c in candidates
                    if _eligible(c, spot=spot, option_type=option_type, delta_band=delta_band,
                                max_spread_pct=max_spread_pct, min_volume=min_volume,
                                min_oi=min_oi, min_premium=min_premium) is None]
    ref = {
        "strike_step": strike_step,
        "max_itm_depth": max((abs(spot - (_num(c.get("strike")) or spot)) for c in eligible_raw), default=10 * strike_step) or 10 * strike_step,
        "max_volume": max((_num(c.get("volume")) or 0 for c in eligible_raw), default=1.0) or 1.0,
        "max_oi": max((_num(c.get("oi")) or 0 for c in eligible_raw), default=1.0) or 1.0,
    }

    out = []
    for c in candidates:
        reject = _eligible(c, spot=spot, option_type=option_type, delta_band=delta_band,
                           max_spread_pct=max_spread_pct, min_volume=min_volume,
                           min_oi=min_oi, min_premium=min_premium)
        strike = _num(c.get("strike")) or 0.0
        if reject:
            out.append(StrikeCandidate(strike=strike, option_type=option_type, status="REJECTED",
                                       reject_reason=reject, raw=c))
            continue
        score, components = _score(c, spot=spot, option_type=option_type, delta_band=delta_band,
                                   weights=weights, candidates_ref=ref)
        out.append(StrikeCandidate(strike=strike, option_type=option_type, status="ELIGIBLE",
                                   score=score, components=components, raw=c))

    out.sort(key=lambda sc: (sc.status != "ELIGIBLE", -(sc.score or -1)))
    return out
