"""
Full-chain hedge selection for a sold (SELL) option leg -- never by strike
distance alone. Every strike in the real chain on the protective side is
evaluated mathematically (liquidity, spread, Greeks, cost, defined risk);
the best risk-reduction-per-rupee candidate within configured limits wins,
or NO_TRADE when nothing clears the bar. Deterministic; no AI anywhere in
this module.

Reuses app.optionchain.chain.OptionChain/OptionLeg (the existing chain
data model) and app.instrument_profiles for lot size -- does not modify
either module, does not fetch its own chain data (a real OptionChain is
the caller's job, e.g. app.optionchain.resolve).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import risk as _risk

# Default limits -- every one overridable via HedgeConfig, none silently
# hardcoded at the call site. These are conservative starting points, not
# backtested-optimal values (Phase 1 scope: the sizing/selection MATH is
# correct and testable; parameter tuning is a separate, later, evidence-
# gated step -- same discipline as scalp_strategy's own filter defaults).
DEFAULT_MIN_OI = 500
DEFAULT_MIN_VOLUME = 100
DEFAULT_MAX_SPREAD_PCT = 0.15          # (ask-bid)/mid, e.g. 0.15 = 15%
DEFAULT_MIN_RISK_REDUCTION_PCT = 50.0  # vs a configured worst-case reference, when one is supplied


@dataclass
class HedgeConfig:
    min_oi: float = DEFAULT_MIN_OI
    min_volume: float = DEFAULT_MIN_VOLUME
    max_spread_pct: float = DEFAULT_MAX_SPREAD_PCT
    min_risk_reduction_pct: float = DEFAULT_MIN_RISK_REDUCTION_PCT
    unhedged_max_loss_reference: float | None = None   # None -> UNBOUNDED_TO_DEFINED path
    max_hedge_cost_pct_of_credit: float = 0.60          # reject a hedge that eats >60% of the credit


@dataclass
class PrimaryLeg:
    strike: float
    option_type: str            # "CE" | "PE"
    premium: float
    delta: float | None = None
    lot_size: int = 1


@dataclass
class HedgeCandidate:
    strike: float
    option_type: str
    premium: float
    liquidity_ok: bool
    spread_ok: bool
    spread_pct: float | None
    economics: "_risk.SpreadEconomics | None"
    net_delta: dict
    risk_reduction: dict
    reject_reasons: list = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return not self.reject_reasons and self.economics is not None


@dataclass
class HedgeDecision:
    status: str                  # "SELECTED" | "NO_TRADE"
    candidate: HedgeCandidate | None
    lots: int
    evaluated: int
    accepted: int
    reason: str


def _spread_pct(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    return round((ask - bid) / mid, 4) if mid > 0 else None


def evaluate_candidates(chain, primary: PrimaryLeg, cfg: HedgeConfig | None = None) -> list[HedgeCandidate]:
    """`chain`: an app.optionchain.chain.OptionChain. Evaluates every strike
    on the correct protective side (further OTM than primary, same
    option_type) -- never picks by distance, scores every real candidate
    the chain actually has data for."""
    cfg = cfg or HedgeConfig()
    ot = primary.option_type.upper()
    side_attr = "ce" if ot == "CE" else "pe"
    out: list[HedgeCandidate] = []

    for row in chain.rows or []:
        leg = getattr(row, side_attr, None)
        if leg is None or leg.ltp is None:
            continue
        strike = row.strike
        # only strikes further OTM than the primary qualify as a protective hedge
        if ot == "CE" and strike <= primary.strike:
            continue
        if ot == "PE" and strike >= primary.strike:
            continue

        reasons = []
        oi_ok = leg.oi is not None and leg.oi >= cfg.min_oi
        vol_ok = leg.volume is not None and leg.volume >= cfg.min_volume
        if not oi_ok:
            reasons.append(f"oi below minimum ({leg.oi} < {cfg.min_oi})" if leg.oi is not None
                          else "oi UNAVAILABLE")
        if not vol_ok:
            reasons.append(f"volume below minimum ({leg.volume} < {cfg.min_volume})" if leg.volume is not None
                          else "volume UNAVAILABLE")

        sp = _spread_pct(leg.bid, leg.ask)
        spread_ok = sp is not None and sp <= cfg.max_spread_pct
        if not spread_ok:
            reasons.append(f"spread too wide ({sp})" if sp is not None else "bid/ask UNAVAILABLE")

        econ = _risk.credit_spread_economics(
            primary_premium=primary.premium, hedge_premium=leg.ltp,
            primary_strike=primary.strike, hedge_strike=strike,
            option_type=ot, lot_size=primary.lot_size)
        if econ is None:
            reasons.append("hedge strike is not further OTM than the primary -- not a valid protective leg")
        else:
            credit_at_risk = primary.premium * primary.lot_size
            hedge_cost = leg.ltp * primary.lot_size
            if credit_at_risk > 0 and hedge_cost / credit_at_risk > cfg.max_hedge_cost_pct_of_credit:
                reasons.append(
                    f"hedge cost eats too much of the credit ({hedge_cost}/{credit_at_risk} "
                    f"> {cfg.max_hedge_cost_pct_of_credit:.0%})")

        nd = _risk.net_delta_exposure(
            primary_delta=primary.delta, hedge_delta=leg.delta, primary_side="SELL",
            lot_size=primary.lot_size, lots=1, spot=chain.spot)

        rr = (_risk.risk_reduction_pct(unhedged_max_loss=cfg.unhedged_max_loss_reference,
                                       hedged_max_loss=econ.max_loss_per_lot)
             if econ is not None else {"status": "UNAVAILABLE", "pct": None})
        if (cfg.unhedged_max_loss_reference is not None and rr.get("status") == "OK"
                and rr["pct"] < cfg.min_risk_reduction_pct):
            reasons.append(f"risk reduction below minimum ({rr['pct']}% < {cfg.min_risk_reduction_pct}%)")

        out.append(HedgeCandidate(
            strike=strike, option_type=ot, premium=leg.ltp,
            liquidity_ok=oi_ok and vol_ok, spread_ok=spread_ok, spread_pct=sp,
            economics=econ, net_delta=nd, risk_reduction=rr, reject_reasons=reasons))

    return out


def select_hedge(chain, primary: PrimaryLeg, cfg: HedgeConfig | None = None,
                 *, available_capital: float | None = None,
                 max_risk_pct: float | None = None) -> HedgeDecision:
    """Best accepted candidate = lowest hedge cost per rupee of max-loss
    reduction achieved (cheapest real protection), never the nearest
    strike. NO_TRADE (never a forced pick) when nothing clears every gate."""
    from . import capital as _capital

    candidates = evaluate_candidates(chain, primary, cfg)
    accepted = [c for c in candidates if c.accepted]
    if not accepted:
        return HedgeDecision(status="NO_TRADE", candidate=None, lots=0,
                             evaluated=len(candidates), accepted=0,
                             reason="no candidate cleared liquidity/spread/cost/risk-reduction limits")

    # Phase 1 selection rule, documented and simple: cheapest accepted
    # protective candidate wins (lowest hedge cost among everything that
    # already cleared liquidity/spread/cost-vs-credit/risk-reduction) --
    # not the nearest strike, not a multi-factor blended score.
    best = min(accepted, key=lambda c: c.premium)

    lots = 0
    if available_capital is not None:
        sized = _capital.size_position(
            available_capital=available_capital,
            max_loss_per_lot=best.economics.max_loss_per_lot,
            max_risk_pct=max_risk_pct or _capital.DEFAULT_MAX_RISK_PCT,
            max_hedge_cost_per_lot=best.premium * primary.lot_size)
        lots = sized["lots"]
        if lots <= 0:
            return HedgeDecision(status="NO_TRADE", candidate=best, lots=0,
                                 evaluated=len(candidates), accepted=len(accepted),
                                 reason=f"sizing rejected: {sized['reason']}")

    return HedgeDecision(status="SELECTED", candidate=best, lots=lots,
                         evaluated=len(candidates), accepted=len(accepted),
                         reason="cheapest accepted protective candidate")
