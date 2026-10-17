"""Paper trade lifecycle + independent risk manager (option BUYING). Pure, deterministic, serialisable; NO broker, NO orders.

Plan -> size -> PaperPosition state machine (SL / T1-T3 partials / break-even / trail / time exit) with pessimistic fills (stop first when a
bar touches both stop and target, slippage on every exit, approximate statutory charges). RiskManager is independent of signal scoring and
fails closed. Stops are never widened and positions are never averaged down. All parameters come from strategy_policy.json."""
from __future__ import annotations

import math
from dataclasses import dataclass, field


# ------------------------------------------------------------------ costs
def charges_inr(buy_premium: float, sell_premium: float, qty: int, policy: dict) -> float:
    """Approximate options-buying round-trip charges on the executed turnover (verify against a contract note)."""
    c = policy["trade"]["charges"]
    buy_t, sell_t = buy_premium * qty, sell_premium * qty
    brokerage = 2 * c["brokerage_per_order_inr"]
    txn = (buy_t + sell_t) * c["exchange_txn_pct"] / 100
    gst = (brokerage + txn) * c["gst_pct"] / 100
    return brokerage + sell_t * c["stt_sell_pct"] / 100 + txn + (buy_t + sell_t) * c["sebi_pct"] / 100 + buy_t * c["stamp_buy_pct"] / 100 + gst


# ------------------------------------------------------------------ planning / sizing
@dataclass
class Plan:
    status: str                       # PLANNED | NO_TRADE
    reasons: list
    side: str | None = None           # CE | PE
    symbol: str | None = None
    entry: float | None = None        # option premium, ask + slippage
    stop: float | None = None
    targets: list = field(default_factory=list)
    lots: int = 0
    lot_size: int = 0
    tick: float = 0.05
    risk_inr: float = 0.0
    detail: dict = field(default_factory=dict)


def _round_tick(x: float, tick: float, up: bool) -> float:
    q = x / tick
    return round((math.ceil(q - 1e-9) if up else math.floor(q + 1e-9)) * tick, 6)


def build_plan(*, side: str, symbol: str, bid: float, ask: float, tick: float, lot_size: int, delta: float | None,
               underlying_entry: float, underlying_stop: float, equity: float, policy: dict) -> Plan:
    """Premium risk is derived from the STRUCTURAL underlying stop through |delta| (first order; gamma ignored, which slightly overstates the
    loss for a long option, i.e. conservative). Rejects invalid, too-wide or unsizeable setups instead of adjusting them."""
    t, r = policy["trade"], policy["risk"]
    no = lambda *why, **d: Plan("NO_TRADE", list(why), side, symbol, detail=d)
    if not (bid > 0 and ask > 0 and bid < ask):
        return no("CROSSED_OR_LOCKED_BOOK" if bid >= ask > 0 else "DATA_MISSING")
    if delta is None or not (0.05 <= abs(delta) <= 1.0):
        return no("DATA_MISSING", why="delta needed to translate the underlying stop into a premium stop")
    if not lot_size or lot_size <= 0 or tick <= 0:
        return no("DATA_UNMAPPED")
    dist = (underlying_entry - underlying_stop) if side == "CE" else (underlying_stop - underlying_entry)
    if dist <= 0:
        return no("INVALID_STOP", why="structural stop is not on the losing side of the entry")
    entry = _round_tick(ask + t["slippage_ticks"] * tick, tick, up=True)
    stop = _round_tick(entry - abs(delta) * dist, tick, up=False)
    if stop <= tick:
        return no("INVALID_STOP", why="premium stop would be at/below zero (underlying stop too far for this delta)")
    stop_pct = (entry - stop) / entry * 100
    if stop_pct > t["max_stop_premium_pct"]:
        return no("STOP_TOO_WIDE", stop_pct=round(stop_pct, 1))
    risk_per_unit = entry - stop
    targets = [_round_tick(entry + k * risk_per_unit, tick, up=True) for k in t["targets_r"]]
    if (targets[0] - entry) / risk_per_unit < r["min_reward_risk_t1"] - 1e-9:
        return no("RR_BELOW_MIN")
    # risk per lot = stop loss + exit slippage + charges, so the budget is not flattered
    loss_per_lot = (risk_per_unit + t["slippage_ticks"] * tick) * lot_size + charges_inr(entry, stop, lot_size, policy)
    budget = equity * r["risk_per_trade_pct"] / 100
    lots = int(budget // loss_per_lot)
    max_lots_exposure = int((equity * r["max_premium_exposure_pct"] / 100) // (entry * lot_size))
    lots = min(lots, max_lots_exposure)
    if lots < 1:
        return no("RISK_BUDGET_TOO_SMALL", budget=round(budget, 2), loss_per_lot=round(loss_per_lot, 2), exposure_cap_lots=max_lots_exposure)
    return Plan("PLANNED", [], side, symbol, entry, stop, targets, lots, lot_size, tick, round(lots * loss_per_lot, 2),
                {"stop_pct": round(stop_pct, 1), "loss_per_lot": round(loss_per_lot, 2), "budget": round(budget, 2)})


def allocate_lots(lots: int, fractions: list) -> list:
    """Whole-lot split of a position over the targets (largest remainder). A position of N lots can only exit in whole lots, so 1 lot exits entirely
    at T1 and 2 lots at T1/T2 -- small accounts are therefore less able to run a winner (a real, honest consequence of lot size)."""
    base = [int(lots * f) for f in fractions]
    rem = sorted(range(len(fractions)), key=lambda i: (lots * fractions[i] - base[i]), reverse=True)
    for i in rem[: lots - sum(base)]:
        base[i] += 1
    return base


# ------------------------------------------------------------------ position state machine
class PaperPosition:
    """Premium-bar driven. Fill rules are pessimistic: a bar that touches both the stop and a target is a STOP; every exit pays slippage."""

    def __init__(self, plan: Plan, open_ts: float, policy: dict):
        assert plan.status == "PLANNED"
        self.p = policy
        self.plan, self.open_ts = plan, open_ts
        self.stop = plan.stop
        self.qty_open = plan.lots * plan.lot_size
        self.qty_total = self.qty_open
        self.alloc = [n * plan.lot_size for n in allocate_lots(plan.lots, policy["trade"]["partial_fractions"])]
        self.hit = 0                                    # targets taken
        self.fills: list = []                           # (qty, price, reason)
        self.closed = False
        self.close_reason: str | None = None

    def _sell(self, qty: int, price: float, reason: str):
        slip = self.p["trade"]["slippage_ticks"] * self.plan.tick
        px = max(self.plan.tick, price - slip)
        self.fills.append((qty, round(px, 6), reason))
        self.qty_open -= qty
        if self.qty_open <= 0:
            self.closed, self.close_reason = True, reason

    def on_bar(self, ts: float, o: float, h: float, l: float, c: float, *, structure_invalidated: bool = False) -> None:
        if self.closed:
            return
        t = self.p["trade"]
        if l <= self.stop:                                           # stop first, always (gap through the stop fills at the open)
            self._sell(self.qty_open, min(o, self.stop), "STOP" if self.hit == 0 else "STOP_AFTER_TARGET")
            return
        for k in range(self.hit, len(self.plan.targets)):
            if h < self.plan.targets[k]:
                break
            last = k == len(self.plan.targets) - 1
            qty = self.qty_open if last else min(self.qty_open, self.alloc[k])
            if qty > 0:
                self._sell(qty, max(o, self.plan.targets[k]) if o >= self.plan.targets[k] else self.plan.targets[k], f"T{k + 1}")
            self.hit = k + 1
            if self.closed:
                return
            if k == 0 and t["be_after_t1"]:
                self.stop = max(self.stop, self.plan.entry)          # never loosen: stop only moves UP
            if k == 1 and t["trail_to_t1_after_t2"]:
                self.stop = max(self.stop, self.plan.targets[0])
        if structure_invalidated:
            self._sell(self.qty_open, c, "STRUCTURE_INVALIDATION")
        elif ts - self.open_ts >= t["max_hold_minutes"] * 60:
            self._sell(self.qty_open, c, "TIME_EXIT")

    def force_close(self, price: float, reason: str = "FORCED"):
        if not self.closed:
            self._sell(self.qty_open, price, reason)

    def pnl_inr(self) -> float:
        sold = sum(q for q, _, _ in self.fills)
        if sold == 0:
            return 0.0
        avg_exit = sum(q * px for q, px, _ in self.fills) / sold
        gross = sum(q * (px - self.plan.entry) for q, px, _ in self.fills)
        return round(gross - charges_inr(self.plan.entry, avg_exit, sold, self.p), 2)

    def to_dict(self) -> dict:
        return {"plan": self.plan.__dict__, "open_ts": self.open_ts, "stop": self.stop, "qty_open": self.qty_open, "qty_total": self.qty_total, "alloc": self.alloc,
                "hit": self.hit, "fills": self.fills, "closed": self.closed, "close_reason": self.close_reason}

    @classmethod
    def from_dict(cls, d: dict, policy: dict) -> "PaperPosition":
        pos = cls(Plan(**d["plan"]), d["open_ts"], policy)
        pos.stop, pos.qty_open, pos.qty_total, pos.hit = d["stop"], d["qty_open"], d["qty_total"], d["hit"]
        pos.alloc = d["alloc"]
        pos.fills, pos.closed, pos.close_reason = [tuple(x) for x in d["fills"]], d["closed"], d["close_reason"]
        return pos


# ------------------------------------------------------------------ independent risk manager
class RiskManager:
    """Independent of scoring. Unknown state fails closed. State is plain data (persistable) so a restart cannot reset the day's limits."""

    def __init__(self, policy: dict, state: dict | None = None):
        self.p = policy["risk"]
        self.capital = self.p["paper_capital_inr"]
        self.s = state or {"day": None, "realized_inr": 0.0, "trades": 0, "last_loss_ts": None, "open": 0, "kill": False, "halted_reason": None}

    def new_day(self, day: str):
        if self.s["day"] != day:
            self.s.update({"day": day, "realized_inr": 0.0, "trades": 0, "last_loss_ts": None, "halted_reason": None})

    def can_open(self, day: str, now_ts: float) -> tuple[bool, list]:
        self.new_day(day)
        why = []
        if self.s["kill"]:
            why.append("KILL_SWITCH")
        if self.s["realized_inr"] <= -self.capital * self.p["daily_loss_limit_pct"] / 100:
            why.append("DAILY_LOSS_LIMIT")
        if self.s["trades"] >= self.p["max_trades_per_day"]:
            why.append("MAX_TRADES")
        lt = self.s["last_loss_ts"]
        if lt is not None and now_ts - lt < self.p["cooldown_after_loss_min"] * 60:
            why.append("COOLDOWN")
        if self.s["open"] >= 1:
            why.append("POSITION_OPEN")                           # one paper position at a time: no pyramiding / averaging down
        return (not why), why

    def on_open(self):
        self.s["trades"] += 1
        self.s["open"] += 1

    def on_close(self, pnl_inr: float, now_ts: float):
        self.s["open"] = max(0, self.s["open"] - 1)
        self.s["realized_inr"] = round(self.s["realized_inr"] + pnl_inr, 2)
        if pnl_inr < 0:
            self.s["last_loss_ts"] = now_ts

    def kill(self, on: bool = True):
        self.s["kill"] = bool(on)
