"""Executable NIFTY CE/PE modelling for index signals (research only). An INDEX stop in points is never used as an option-premium stop:
the premium stop is derived from the contract's delta and gamma (second-order), and size comes from contract metadata, premium, lot size,
estimated costs and capital/risk limits.

Everything here is an ASSUMPTION unless fed with measured data: constant implied volatility, flat rate, spread/slippage in rupees per unit,
approximate Indian options charges. Tests pin the arithmetic; the evaluation script prints every assumption it used."""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


# ------------------------------------------------------------------ Black-Scholes (European, continuous dividend q)
def _n(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(S: float, K: float, T: float, sigma: float, is_call: bool, r: float = 0.065, q: float = 0.0) -> float:
    if T <= 0:
        return max(S - K, 0.0) if is_call else max(K - S, 0.0)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if is_call:
        return S * math.exp(-q * T) * _n(d1) - K * math.exp(-r * T) * _n(d2)
    return K * math.exp(-r * T) * _n(-d2) - S * math.exp(-q * T) * _n(-d1)


def bs_greeks(S: float, K: float, T: float, sigma: float, is_call: bool, r: float = 0.065, q: float = 0.0) -> dict:
    if T <= 0:
        itm = (S > K) if is_call else (S < K)
        return dict(delta=(1.0 if itm else 0.0) * (1 if is_call else -1), gamma=0.0, theta_per_day=0.0, vega=0.0)
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sq
    d2 = d1 - sq
    delta = math.exp(-q * T) * (_n(d1) if is_call else _n(d1) - 1.0)
    gamma = math.exp(-q * T) * _pdf(d1) / (S * sq)
    theta = (-S * math.exp(-q * T) * _pdf(d1) * sigma / (2 * math.sqrt(T))
             - (r * K * math.exp(-r * T) * _n(d2) if is_call else -r * K * math.exp(-r * T) * _n(-d2))
             + (q * S * math.exp(-q * T) * _n(d1) if is_call else -q * S * math.exp(-q * T) * _n(-d1)))
    return dict(delta=delta, gamma=gamma, theta_per_day=theta / 365.0, vega=S * math.exp(-q * T) * _pdf(d1) * math.sqrt(T) / 100.0)


# ------------------------------------------------------------------ weekly expiry rule (NIFTY): Thursday until 2025-08-31, Tuesday after
def nifty_weekly_expiry(d: dt.date, after_close: bool = False) -> dt.date:
    """Nearest weekly expiry on/after `d` (if `after_close` and d is the expiry day, the next one). Exchange holidays are NOT modelled."""
    wd_target = 3 if d < dt.date(2025, 9, 1) else 1
    delta = (wd_target - d.weekday()) % 7
    exp = d + dt.timedelta(days=delta)
    if delta == 0 and after_close:
        exp += dt.timedelta(days=7)
    return exp


def years_to_expiry(now: dt.datetime, expiry: dt.date) -> float:
    close = dt.datetime(expiry.year, expiry.month, expiry.day, 15, 30, tzinfo=IST)
    return max((close - now).total_seconds(), 0.0) / (365.0 * 86400.0)


# ------------------------------------------------------------------ costs (approximate Indian index options, per order; ASSUMPTIONS)
@dataclass
class Costs:
    half_spread: float = 0.10          # rupees per unit, crossed once per side
    slippage: float = 0.05             # rupees per unit per side
    brokerage_per_order: float = 20.0
    stt_sell_pct: float = 0.10         # % of sell premium value
    exchange_txn_pct: float = 0.0503   # % of premium turnover (both sides)
    sebi_per_crore: float = 10.0
    stamp_buy_pct: float = 0.003
    gst_pct: float = 18.0              # on brokerage + exchange + sebi


def round_trip_cost_rupees(entry_price: float, exit_price: float, lots: int, lot_size: int, c: Costs) -> float:
    """Explicit charges (not the spread/slippage, which are applied to the prices): brokerage, STT on the sell leg, exchange, SEBI, stamp, GST."""
    qty = lots * lot_size
    buy_val, sell_val = entry_price * qty, exit_price * qty
    turnover = buy_val + sell_val
    exch = turnover * c.exchange_txn_pct / 100.0
    sebi = turnover / 1e7 * c.sebi_per_crore
    gst = (2 * c.brokerage_per_order + exch + sebi) * c.gst_pct / 100.0
    return 2 * c.brokerage_per_order + sell_val * c.stt_sell_pct / 100.0 + exch + sebi + buy_val * c.stamp_buy_pct / 100.0 + gst


# ------------------------------------------------------------------ sizing from contract metadata (never from index points)
@dataclass
class OptionPlan:
    feasible: bool
    reason: str
    lots: int = 0
    qty: int = 0
    entry_ask: float = 0.0
    premium_stop: float = 0.0          # price at which the position is stopped, derived from delta/gamma, not from the index stop
    loss_per_unit: float = 0.0
    max_loss: float = 0.0              # lots * lot_size * loss_per_unit + estimated charges
    capital_used: float = 0.0
    est_charges: float = 0.0
    notes: list = field(default_factory=list)


def premium_stop_from_index_stop(entry_ask: float, delta: float, gamma: float, index_risk_points: float, side: int) -> tuple[float, float]:
    """Premium loss for an adverse index move of `index_risk_points`: |delta|*R - 0.5*gamma*R^2 (second-order, ignores theta/vega over the move).
    Returns (stop_price, loss_per_unit). `side`: +1 long CE (index falls), -1 long PE (index rises). Never below 0."""
    R = abs(index_risk_points)
    loss = max(abs(delta) * R - 0.5 * gamma * R * R, 0.0)
    loss = min(loss, entry_ask)
    return entry_ask - loss, loss


def plan_option_trade(capital: float, risk_pct: float, entry_ask: float, lot_size: int, delta: float, gamma: float, index_risk_points: float,
                      side: int, tick: float = 0.05, costs: Costs | None = None, margin_buffer: float = 1.0) -> OptionPlan:
    c = costs or Costs()
    if not (entry_ask > 0 and lot_size > 0):
        return OptionPlan(False, "INVALID_CONTRACT_INPUT")
    if abs(delta) < 1e-6:
        return OptionPlan(False, "DELTA_UNAVAILABLE")
    stop, loss = premium_stop_from_index_stop(entry_ask, delta, gamma, index_risk_points, side)
    stop = math.floor(stop / tick) * tick                                  # exchange tick: round the stop DOWN (more conservative)
    loss = entry_ask - stop
    if loss <= 0:
        return OptionPlan(False, "NO_PREMIUM_RISK_BASIS", entry_ask=entry_ask)
    risk_amt = capital * risk_pct / 100.0
    per_lot_loss = loss * lot_size + round_trip_cost_rupees(entry_ask, stop, 1, lot_size, c) + 2 * (c.half_spread + c.slippage) * lot_size
    per_lot_capital = entry_ask * lot_size * margin_buffer
    by_risk = math.floor(risk_amt / per_lot_loss) if per_lot_loss > 0 else 0
    by_cap = math.floor(capital / per_lot_capital)
    lots = min(by_risk, by_cap)
    notes = []
    if by_cap < 1:
        return OptionPlan(False, "ONE_LOT_EXCEEDS_CAPITAL", entry_ask=entry_ask, premium_stop=stop, loss_per_unit=loss,
                          capital_used=per_lot_capital, notes=[f"1 lot needs {per_lot_capital:,.0f} > capital {capital:,.0f}"])
    if by_risk < 1:
        return OptionPlan(False, "ONE_LOT_EXCEEDS_RISK_BUDGET", entry_ask=entry_ask, premium_stop=stop, loss_per_unit=loss,
                          max_loss=per_lot_loss, capital_used=per_lot_capital,
                          notes=[f"1 lot risks {per_lot_loss:,.0f} > planned {risk_amt:,.0f}"])
    if lots == by_cap and by_cap < by_risk:
        notes.append("capital-limited")
    return OptionPlan(True, "OK", lots=lots, qty=lots * lot_size, entry_ask=entry_ask, premium_stop=stop, loss_per_unit=loss,
                      max_loss=lots * per_lot_loss, capital_used=lots * per_lot_capital,
                      est_charges=lots * round_trip_cost_rupees(entry_ask, stop, 1, lot_size, c), notes=notes)
