"""Hypothetical paper book. No broker/order API is imported here; ENABLE_LIVE_ORDERS must be false (or unset) or construction raises."""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict

from . import store


class LiveOrdersForbidden(RuntimeError): pass


def assert_paper_only():
    if os.environ.get("ENABLE_LIVE_ORDERS", "false").strip().lower() in ("1", "true", "yes", "on"):
        raise LiveOrdersForbidden("ENABLE_LIVE_ORDERS is set; the NIFTY direction engine is paper-only and refuses to run")


@dataclass
class PaperConfig:
    qty: int = 1                    # index units (hypothetical)
    slippage_bps: float = 1.0       # per side, adverse
    cost_bps_round_trip: float = 3.0   # ASSUMPTION (brokerage+taxes on a futures-like instrument); not a validated NSE cost model
    rr: float = 1.5                 # target = rr * stop distance
    max_hold_bars: int = 12         # 5m bars
    max_trades_per_day: int = 3
    max_daily_loss: float = 0.0     # in index points*qty; 0 disables
    def to_dict(self): return asdict(self)


class PaperBook:
    def __init__(self, cfg: PaperConfig | None = None, data_kind: str = "UNSET", db_path: str | None = None, persist: bool = True):
        assert_paper_only(); self.cfg = cfg or PaperConfig(); self.kind = data_kind; self.path = db_path; self.persist = persist
        self.pos: dict | None = None; self.closed: list[dict] = []; self._day_n: dict = {}; self._day_pnl: dict = {}

    def _slip(self, px, side, opening): s = self.cfg.slippage_bps / 1e4; return px * (1 + s) if (side == "LONG") == opening else px * (1 - s)

    def open(self, side, px, stop, ts, day, signal_id=None) -> dict | None:
        assert_paper_only()
        if self.pos or self._day_n.get(day, 0) >= self.cfg.max_trades_per_day: return None
        if self.cfg.max_daily_loss and self._day_pnl.get(day, 0) <= -self.cfg.max_daily_loss: return None
        e = self._slip(px, side, True); d = abs(e - stop)
        if d <= 0 or (side == "LONG" and stop >= e) or (side == "SHORT" and stop <= e): return None
        tgt = e + self.cfg.rr * d if side == "LONG" else e - self.cfg.rr * d
        self.pos = {"side": side, "entry_px": e, "stop": stop, "target": tgt, "entry_ts": ts, "day": day, "bars": 0, "signal_id": signal_id}; self._day_n[day] = self._day_n.get(day, 0) + 1
        return self.pos

    def on_bar(self, bar: dict, ts: float, force_reason: str | None = None) -> dict | None:
        """Evaluate the open position against a CLOSED bar. Stop checked before target when both are touched (pessimistic)."""
        p = self.pos
        if not p: return None
        p["bars"] += 1; long = p["side"] == "LONG"; r = px = None
        if force_reason: r, px = force_reason, bar["c"]
        elif (long and bar["l"] <= p["stop"]) or (not long and bar["h"] >= p["stop"]): r, px = "STOP", p["stop"]
        elif (long and bar["h"] >= p["target"]) or (not long and bar["l"] <= p["target"]): r, px = "TARGET", p["target"]
        elif p["bars"] >= self.cfg.max_hold_bars: r, px = "TIMEOUT", bar["c"]
        if r is None: return None
        x = self._slip(px, p["side"], False); gross = ((x - p["entry_px"]) if long else (p["entry_px"] - x)) * self.cfg.qty
        cost = (p["entry_px"] + x) / 2 * self.cfg.qty * self.cfg.cost_bps_round_trip / 1e4
        t = {**p, "exit_ts": ts, "exit_px": x, "exit_reason": r, "gross": gross, "cost": cost, "net": gross - cost, "qty": self.cfg.qty}
        self.closed.append(t); self._day_pnl[p["day"]] = self._day_pnl.get(p["day"], 0) + t["net"]; self.pos = None
        if self.persist:
            with store.conn(self.path) as c:
                c.execute("INSERT INTO paper_trades (signal_id,side,qty,entry_ts,entry_px,stop_px,target_px,exit_ts,exit_px,exit_reason,gross,cost,net,data_kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (t["signal_id"], t["side"], t["qty"], t["entry_ts"], t["entry_px"], t["stop"], t["target"], t["exit_ts"], t["exit_px"], r, t["gross"], cost, t["net"], self.kind))
        return t

    def summary(self) -> dict:
        n = len(self.closed); net = sum(t["net"] for t in self.closed)
        return {"trades": n, "net": net, "gross": sum(t["gross"] for t in self.closed), "cost": sum(t["cost"] for t in self.closed), "win_rate": (sum(t["net"] > 0 for t in self.closed) / n) if n else None}
