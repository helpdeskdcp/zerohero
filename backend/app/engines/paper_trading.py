"""
AI-PAPER-TRADING engine — simulated order lifecycle (OPEN / UPDATE / CLOSE).
No real orders are ever placed. live_trading is always false.

Extended for scalping: per-strategy tagging, MFE/MAE excursion tracking,
breakeven + trailing-stop ratchet, and a hard time-in-trade stop so a
scalp is flattened when max_hold_sec elapses regardless of price.
"""
from __future__ import annotations
import time
import random
from datetime import datetime, timezone

from .. import db
from ..connectors import telegram


def _new_trade_id():
    return "TRD-" + format(int(time.time() * 1000), "x") + "-" + format(random.randint(0, 0xFFFFF), "x")


def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def open_trade(signal: dict) -> dict:
    trade_id = _new_trade_id()
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "trade_id": trade_id,
        "signal_id": signal.get("signal_id"),
        "opened_ts": now,
        "closed_ts": None,
        "status": "OPEN",
        "result": None,
        "market": signal.get("market"),
        "underlying": signal.get("underlying"),
        "instrument": signal.get("instrument"),
        "expiry": signal.get("expiry"),
        "strike": signal.get("strike"),
        "option_type": signal.get("option_type"),
        "direction": signal.get("direction"),
        "timeframe": signal.get("timeframe"),
        "entry": signal.get("entry"),
        "exit_price": None,
        "target_1": signal.get("target_1"),
        "target_2": signal.get("target_2"),
        "stop_loss": signal.get("stop_loss"),
        "trailing_stop": signal.get("trailing_stop"),
        "quantity": signal.get("quantity"),
        "probability": signal.get("probability"),
        "confidence": signal.get("confidence"),
        "market_regime": signal.get("market_regime"),
        "oi_evidence": signal.get("oi_evidence"),
        "pnl": 0.0,
        "reason": signal.get("reason", "orchestrator approved"),
        "strategy": signal.get("strategy") or "CORE",
        "setup": signal.get("setup"),
        "atr_pct": signal.get("atr_pct"),
        "max_hold_sec": signal.get("max_hold_sec"),
        "symboltoken": signal.get("symboltoken"),
        "mfe": 0.0,
        "mae": 0.0,
        "exit_reason": None,
    }
    db.insert_trade(row)
    return row


def _excursions(t, ltp):
    """Return (mfe, mae) in price points, direction-aware, ratcheted."""
    entry = t.get("entry") or 0
    sign = 1 if t.get("direction") == "BUY" else -1
    fav = sign * (ltp - entry)
    adv = -fav
    mfe = max(t.get("mfe") or 0.0, fav if fav > 0 else 0.0)
    mae = max(t.get("mae") or 0.0, adv if adv > 0 else 0.0)
    return round(mfe, 4), round(mae, 4)


def update_trade_price(trade_id: str, ltp: float, now: datetime | None = None) -> dict | None:
    """Mark-to-market an open trade. Applies, in order:
       time-stop -> stop/target hit -> breakeven move -> trailing ratchet.
       Auto-closes and returns the closed row on any exit trigger.
    """
    t = db.get_trade(trade_id)
    if not t or t["status"] != "OPEN":
        return t

    now = now or datetime.now(timezone.utc)
    entry = t["entry"] or 0
    qty = t["quantity"] or 0
    direction = t["direction"]
    sign = 1 if direction == "BUY" else -1
    pnl = sign * (ltp - entry) * qty
    mfe, mae = _excursions(t, ltp)

    sl = t["stop_loss"]
    hit_sl = sl is not None and (
        (direction == "BUY" and ltp <= sl) or (direction == "SELL" and ltp >= sl))
    hit_t1 = t["target_1"] is not None and (
        (direction == "BUY" and ltp >= t["target_1"]) or
        (direction == "SELL" and ltp <= t["target_1"]))

    # MANUAL = monitor-only. Mark P&L, report which level is hit via `_hit`,
    # but NEVER auto-close — the app cannot square a real broker position, and
    # closing the mirror just churns against broker re-sync.
    if (t.get("strategy") or "").upper() == "MANUAL":
        db.update_trade(trade_id, {"pnl": round(pnl, 2), "mfe": mfe, "mae": mae})
        row = db.get_trade(trade_id)
        row["_hit"] = "TARGET" if hit_t1 else ("STOP" if hit_sl else None)
        return row

    # --- hard time-in-trade stop (scalp discipline) ---
    max_hold = t.get("max_hold_sec")
    opened = _parse_ts(t.get("opened_ts"))
    if max_hold and opened and (now - opened).total_seconds() >= float(max_hold):
        db.update_trade(trade_id, {"mfe": mfe, "mae": mae})
        return close_trade(trade_id, ltp, exit_reason="TIME")

    stop_moved_favourably = (
        sl is not None and (
            (direction == "BUY" and sl >= entry) or (direction == "SELL" and sl <= entry)
        ) and sl != entry
    )

    if hit_sl or hit_t1:
        db.update_trade(trade_id, {"mfe": mfe, "mae": mae})
        if hit_t1:
            reason, res = "TARGET", "WIN"
        elif stop_moved_favourably:
            reason, res = "TRAIL", ("WIN" if sign * (ltp - entry) > 0 else "FLAT")
        else:
            reason, res = "STOP", "LOSS"
        return close_trade(trade_id, ltp, forced_result=res, exit_reason=reason)

    # --- breakeven + trailing ratchet (never loosens the stop) --- (SCALP only)
    fields = {"pnl": round(pnl, 2), "mfe": mfe, "mae": mae}
    trail_dist = t.get("trailing_stop")
    tick_stop_dist = abs(entry - sl) if sl is not None else None
    new_sl = sl
    if tick_stop_dist and mfe >= tick_stop_dist and not stop_moved_favourably:
        new_sl = entry  # 1R reached -> risk-free
    if trail_dist and mfe >= float(trail_dist):
        cand = ltp - sign * float(trail_dist)
        if new_sl is None or (direction == "BUY" and cand > new_sl) or (direction == "SELL" and cand < new_sl):
            new_sl = round(cand, 2)
    if new_sl is not None and new_sl != sl:
        fields["stop_loss"] = new_sl

    db.update_trade(trade_id, fields)
    return db.get_trade(trade_id)


def close_trade(trade_id: str, exit_price: float, forced_result: str | None = None,
                exit_reason: str = "MANUAL") -> dict | None:
    t = db.get_trade(trade_id)
    if not t or t["status"] != "OPEN":
        return t
    entry = t["entry"] or 0
    qty = t["quantity"] or 0
    direction = t["direction"]
    sign = 1 if direction == "BUY" else -1
    pnl = round(sign * (exit_price - entry) * qty, 2)
    result = forced_result or ("WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "FLAT"))
    mfe, mae = _excursions(t, exit_price)

    fields = {
        "status": "CLOSED",
        "closed_ts": datetime.now(timezone.utc).isoformat(),
        "exit_price": exit_price,
        "pnl": pnl,
        "result": result,
        "exit_reason": exit_reason,
        "mfe": mfe,
        "mae": mae,
    }
    db.update_trade(trade_id, fields)
    updated = db.get_trade(trade_id)
    try:
        if str(exit_reason).startswith("COMBO"):
            pass  # combo layer sends one paired alert instead of two per-leg ones
        elif (updated.get("strategy") or "").upper() == "MANUAL":
            telegram.notify_position_alert(updated)
        else:
            telegram.notify_trade_closed(updated)
    except Exception:
        pass
    return updated
