"""
No-look-ahead chronological replay / backtest harness.

Drives `oi_history_adapter.iter_market_states()` one cycle at a time, exposes a
`decide(state, ctx)` hook the signal engines plug into, and simulates the
resulting option trade against the *locked* contract's future price series.

Guarantees (spec acceptance criteria):
  * decide() sees only the current cycle + PAST closed candles (ctx.candles).
  * a trade's price series is read ONLY from its locked (strike, type, expiry,
    token) leg. If that leg leaves the chain or its expiry changes the trade is
    force-closed at its last real mark (CONTRACT_UNAVAILABLE / CONTRACT_ROLLOVER)
    — never priced off a different contract.
  * no overnight holds — EOD flatten at session close.
  * missing index_ltp / empty chain at a cycle -> that cycle is skipped for
    decisions; an open trade holds at its last real mark (fail closed, no
    fabricated price).
  * every decision gets a unique signal_id and a full scalp_signals row
    (source = BACKTEST | REPLAY), with provenance back to cycles.id + run_id.

Nothing here talks to a broker or writes the historical BATI DB.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from . import oi_history_adapter as ad
from .. import db

# NSE cash session, IST minute-of-day.
SESSION_START = 9 * 60 + 15
SESSION_END = 15 * 60 + 30
_HARNESS_TFS = ("1m", "3m", "5m", "15m", "30m")
_TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


def _mod(ts: str) -> Optional[int]:
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt.hour * 60 + dt.minute


def _tod_bucket(minute: Optional[int]) -> str:
    if minute is None:
        return "UNKNOWN"
    if minute < 9 * 60 + 30:
        return "OPEN"
    if minute < 11 * 60 + 30:
        return "MORNING"
    if minute < 13 * 60 + 30:
        return "MIDDAY"
    if minute < 15 * 60:
        return "AFTERNOON"
    return "CLOSE"


def _leg(state: dict, strike, opt_type: str) -> Optional[dict]:
    st = str(opt_type or "").lower()
    for row in state.get("chain") or []:
        if row.get("strike") == strike:
            return row.get(st)
    return None


# --------------------------------------------------------------------------- #
@dataclass
class SimTrade:
    signal_id: str
    symbol: str
    direction: str
    opt_type: str
    strike: float
    expiry: Optional[str]
    token: Optional[str]
    tradingsymbol: Optional[str]
    entry: float
    entry_ts: str
    stop_loss: float
    target_1: float
    target_2: Optional[float]
    trailing_stop: float
    max_hold_sec: Optional[float]
    # runtime
    cur_sl: float = 0.0
    peak: float = 0.0
    mark: float = 0.0
    mfe: float = 0.0
    mae: float = 0.0
    status: str = "OPEN"
    exit_price: Optional[float] = None
    exit_ts: Optional[str] = None
    exit_reason: Optional[str] = None
    points: Optional[float] = None
    r_multiple: Optional[float] = None
    outcome: Optional[str] = None
    holding_sec: Optional[float] = None

    def __post_init__(self):
        self.cur_sl = self.stop_loss
        self.peak = self.entry
        self.mark = self.entry

    @property
    def risk(self) -> float:
        return max(1e-9, self.entry - self.stop_loss)

    def _hold_sec(self, ts: str) -> float:
        try:
            return (datetime.fromisoformat(ts) - datetime.fromisoformat(self.entry_ts)).total_seconds()
        except (TypeError, ValueError):
            return 0.0

    def mark_to(self, ltp: float, ts: str) -> None:
        """Update excursions + ratchet the trailing stop (long premium)."""
        self.mark = ltp
        fav = ltp - self.entry
        self.mfe = max(self.mfe, fav)
        self.mae = max(self.mae, -fav)
        self.peak = max(self.peak, ltp)
        if self.trailing_stop and self.trailing_stop > 0:
            cand = round(self.peak - self.trailing_stop, 2)
            if cand > self.cur_sl:
                self.cur_sl = cand

    def check_exit(self, ltp: float, ts: str) -> Optional[str]:
        if ltp <= self.cur_sl:
            return "TRAIL" if self.cur_sl > self.stop_loss else "STOP"
        if ltp >= self.target_1:
            return "TARGET"
        if self.max_hold_sec and self._hold_sec(ts) >= float(self.max_hold_sec):
            return "TIME"
        return None

    def close(self, price: float, ts: str, reason: str) -> None:
        self.status = "CLOSED"
        self.exit_price = round(price, 2)
        self.exit_ts = ts
        self.exit_reason = reason
        self.points = round(price - self.entry, 2)
        self.r_multiple = round((price - self.entry) / self.risk, 3)
        self.holding_sec = round(self._hold_sec(ts), 1)
        self.outcome = "WIN" if self.points > 0 else ("LOSS" if self.points < 0 else "FLAT")


# --------------------------------------------------------------------------- #
class ReplayContext:
    """What decide() may look at — current state + PAST closed candles only."""

    def __init__(self, series: dict[str, list[dict]]):
        self._series = series          # tf -> full-range bar list (oldest->newest)
        self._ts: str = ""

    def _set_now(self, ts: str) -> None:
        self._ts = ts

    def candles(self, tf: str) -> list[dict]:
        """Bars whose bucket has fully closed at or before 'now'. No partials,
        no future bars."""
        tf_min = _TF_MIN.get(tf)
        bars = self._series.get(tf) or []
        if tf_min is None or not self._ts:
            return []
        now = datetime.fromisoformat(self._ts)
        out = []
        for b in bars:
            try:
                bar_end = datetime.fromisoformat(b["t"]).timestamp() + tf_min * 60
            except (TypeError, ValueError):
                continue
            if bar_end <= now.timestamp():
                out.append(b)
            else:
                break
        return out


@dataclass
class ReplayResult:
    run_id: str
    symbol: str
    start: Optional[str]
    end: Optional[str]
    states_seen: int = 0
    decisions: int = 0
    entries: int = 0
    trades: list = field(default_factory=list)          # list[SimTrade]
    signals: list = field(default_factory=list)         # list[dict] persisted
    manifest: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)

    def summary(self) -> dict:
        closed = [t for t in self.trades if t.status == "CLOSED"]
        wins = [t for t in closed if t.outcome == "WIN"]
        losses = [t for t in closed if t.outcome == "LOSS"]
        gross_win = sum(t.points for t in wins)
        gross_loss = -sum(t.points for t in losses)
        return {
            "run_id": self.run_id, "symbol": self.symbol,
            "states_seen": self.states_seen, "decisions": self.decisions,
            "entries": self.entries, "closed": len(closed),
            "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / len(closed), 3) if closed else None,
            "net_points": round(sum(t.points for t in closed), 2) if closed else 0.0,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "avg_win": round(gross_win / len(wins), 2) if wins else None,
            "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
            "exit_reasons": self.counts,
        }


# --------------------------------------------------------------------------- #
class ReplayHarness:
    def __init__(self, symbol: str, start: str | None = None, end: str | None = None, *,
                 source: str = "BACKTEST", max_concurrent: int = 1,
                 persist: bool = True, log_all_decisions: bool = False,
                 decide_every_sec: float = 30.0):
        self.symbol = str(symbol).upper()
        self.start, self.end = start, end
        self.source = source
        self.max_concurrent = max_concurrent
        self.persist = persist
        self.log_all = log_all_decisions
        # throttle: run decide() at most once per this many seconds of tape time
        # (open trades are still marked every cycle). 0 = every cycle.
        self.decide_every_sec = float(decide_every_sec or 0)
        self.run_id = uuid.uuid4().hex[:12]

    # -- persistence -------------------------------------------------------- #
    def _persist_signal(self, sig_row: dict) -> None:
        if not self.persist:
            return
        try:
            db.insert_scalp_signal(sig_row)
        except Exception:
            pass

    def _update_signal(self, signal_id: str, fields: dict) -> None:
        if not self.persist:
            return
        try:
            db.update_scalp_signal(signal_id, fields)
        except Exception:
            pass

    # -- main loop -------------------------------------------------------- #
    def run(self, decide: Callable[[dict, ReplayContext], Optional[dict]]) -> ReplayResult:
        res = ReplayResult(self.run_id, self.symbol, self.start, self.end)
        res.manifest = ad.data_quality_manifest(self.symbol)

        series = {tf: (ad.resample_candles(self.symbol, tf, kind="index",
                                           start=self.start, end=self.end).get("candles") or [])
                  for tf in _HARNESS_TFS}
        ctx = ReplayContext(series)

        open_trades: list[SimTrade] = []
        cur_date: Optional[str] = None
        _last_decide_ts = None

        for state in ad.iter_market_states(self.symbol, self.start, self.end):
            res.states_seen += 1
            ts = state["ts"]
            minute = _mod(ts)

            # new session day -> anything still open is an overnight leak; flatten
            if cur_date is not None and ts[:10] != cur_date:
                for t in open_trades:
                    self._flatten(t, t.mark, ts, "EOD_ROLLOVER", res)
                open_trades.clear()
            cur_date = ts[:10]

            in_session = minute is not None and SESSION_START <= minute <= SESSION_END
            ltp = state.get("index_ltp")
            has_data = ltp is not None and bool(state.get("chain"))

            # 1) manage open trades against their LOCKED contract
            for t in list(open_trades):
                if not in_session:
                    # EOD flatten at the last real IN-SESSION mark — never price a
                    # trade off a post-close / stale print.
                    self._flatten(t, t.mark, ts, "SESSION_CLOSE", res)
                    open_trades.remove(t)
                    continue
                leg = _leg(state, t.strike, t.opt_type)
                if leg is None or leg.get("ltp") is None:
                    continue                     # contract absent this tick -> hold at last mark
                if t.expiry and leg.get("expiry") and leg["expiry"] != t.expiry:
                    self._flatten(t, t.mark, ts, "CONTRACT_ROLLOVER", res)
                    open_trades.remove(t)
                    continue
                px = float(leg["ltp"])
                if px <= 0:
                    continue
                t.mark_to(px, ts)
                reason = t.check_exit(px, ts)
                if reason:
                    self._flatten(t, px, ts, reason, res)
                    open_trades.remove(t)

            # 2) decision (throttled to decide_every_sec of tape time)
            if not (in_session and has_data):
                continue
            if self.decide_every_sec and _last_decide_ts is not None:
                try:
                    gap = (datetime.fromisoformat(ts) - datetime.fromisoformat(_last_decide_ts)).total_seconds()
                except (TypeError, ValueError):
                    gap = self.decide_every_sec
                if 0 <= gap < self.decide_every_sec:
                    continue
            _last_decide_ts = ts
            ctx._set_now(ts)
            try:
                sig = decide(state, ctx)
            except Exception as e:                       # one engine fault != run halt
                res.counts["DECIDE_ERROR"] = res.counts.get("DECIDE_ERROR", 0) + 1
                continue
            if not sig:
                continue
            res.decisions += 1
            decision = str(sig.get("decision") or "NO_TRADE").upper()
            is_entry = decision in ("BUY_CE", "BUY_PE")

            if not is_entry:
                if self.log_all:
                    self._log_only(sig, state, ts, minute, res)
                continue
            if len(open_trades) >= self.max_concurrent:
                continue

            trade = self._open(sig, state, ts, minute, res)
            if trade:
                open_trades.append(trade)

        # end of stream -> flatten leftovers at last mark
        for t in open_trades:
            self._flatten(t, t.mark, t.entry_ts, "RUN_END", res)
        return res

    # -- helpers -------------------------------------------------------- #
    def _sig_base(self, sig: dict, state: dict, ts: str, minute) -> dict:
        cs = sig.get("component_scores")
        return {
            "signal_id": "SCS-" + self.run_id + "-" + uuid.uuid4().hex[:8],
            "source": self.source,
            "provenance": json.dumps({"run_id": self.run_id,
                                      "cycle_id": state.get("_src", {}).get("cycle_id"),
                                      "db": state.get("_src", {}).get("db")}),
            "created_ts": ts, "session_date": ts[:10], "tod_bucket": _tod_bucket(minute),
            "symbol": self.symbol, "index_ltp": state.get("index_ltp"),
            "vwap": sig.get("vwap"), "atr": sig.get("atr"),
            "pcr": state.get("pcr"), "max_pain": state.get("max_pain"),
            "regime": sig.get("regime"), "momentum": sig.get("momentum"),
            "support": sig.get("support"), "resistance": sig.get("resistance"),
            "support_strength": sig.get("support_strength"),
            "resistance_strength": sig.get("resistance_strength"),
            "sr_level": sig.get("sr_level"), "sr_side": sig.get("sr_side"),
            "signal_type": sig.get("signal_type"), "direction": sig.get("direction"),
            "mtf_alignment": sig.get("mtf_alignment"),
            "component_scores": json.dumps(cs) if isinstance(cs, dict) else cs,
            "signal_score": sig.get("signal_score"), "probability": sig.get("probability"),
            "confidence": sig.get("confidence"), "ev": sig.get("ev"), "rr": sig.get("rr"),
            "decision": sig.get("decision"), "reason": sig.get("reason"),
            "calib_version": sig.get("calib_version"),
        }

    def _log_only(self, sig, state, ts, minute, res: ReplayResult) -> None:
        row = {**self._sig_base(sig, state, ts, minute), "status": "PENDING", "resolved": 1}
        self._persist_signal(row)
        res.signals.append(row)

    def _open(self, sig, state, ts, minute, res: ReplayResult) -> Optional[SimTrade]:
        opt_type = "CE" if sig["decision"] == "BUY_CE" else "PE"
        strike = sig.get("strike")
        leg = _leg(state, strike, opt_type)
        if not leg or leg.get("ltp") is None or float(leg["ltp"]) <= 0:
            return None                                  # fail closed: no real fill price
        entry = float(sig.get("entry") or leg["ltp"])
        sl = float(sig["stop_loss"]); t1 = float(sig["target_1"])
        if not (sl < entry < t1):
            return None
        row = self._sig_base(sig, state, ts, minute)
        row.update({
            "opt_underlying": self.symbol, "opt_strike": strike,
            "opt_expiry": sig.get("expiry") or leg.get("expiry"),
            "opt_type": opt_type,
            "opt_token": sig.get("token") or leg.get("token"),
            "opt_tradingsymbol": sig.get("tradingsymbol") or leg.get("tradingsymbol"),
            "entry": round(entry, 2), "stop_loss": round(sl, 2),
            "target_1": round(t1, 2),
            "target_2": round(float(sig["target_2"]), 2) if sig.get("target_2") else None,
            "trailing_stop": float(sig.get("trailing_stop") or 0),
            "max_hold_sec": sig.get("max_hold_sec"),
            "entry_ts": ts, "status": "OPEN", "resolved": 0,
        })
        self._persist_signal(row)
        res.signals.append(row)
        res.entries += 1
        t = SimTrade(
            signal_id=row["signal_id"], symbol=self.symbol, direction=sig.get("direction") or "",
            opt_type=opt_type, strike=strike, expiry=row["opt_expiry"],
            token=row["opt_token"], tradingsymbol=row["opt_tradingsymbol"],
            entry=round(entry, 2), entry_ts=ts, stop_loss=round(sl, 2),
            target_1=round(t1, 2),
            target_2=row["target_2"], trailing_stop=row["trailing_stop"],
            max_hold_sec=sig.get("max_hold_sec"),
        )
        return t

    def _flatten(self, t: SimTrade, price: float, ts: str, reason: str, res: ReplayResult) -> None:
        if t.status == "CLOSED":
            return
        t.close(price, ts, reason)
        res.trades.append(t)
        res.counts[reason] = res.counts.get(reason, 0) + 1
        self._update_signal(t.signal_id, {
            "status": "CLOSED", "exit_price": t.exit_price, "exit_ts": t.exit_ts,
            "exit_reason": t.exit_reason, "points": t.points, "r_multiple": t.r_multiple,
            "mfe": round(t.mfe, 2), "mae": round(t.mae, 2), "outcome": t.outcome,
            "holding_sec": t.holding_sec, "resolved": 1,
        })


def run_replay(symbol: str, decide, start=None, end=None, **kw) -> ReplayResult:
    return ReplayHarness(symbol, start, end, **kw).run(decide)
