"""
AI-RISK-ENGINE — position sizing and hard-gate risk checks.
Ported 1:1 from the n8n Code node logic.
"""
import math
from datetime import datetime, timezone

MODEL_VERSION = "risk-engine-v1"


def _num(x):
    try:
        if x is None:
            return None
        f = float(x)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _round(x, d=2):
    if x is None or not math.isfinite(x):
        return None
    p = 10 ** d
    return round(x * p) / p


def run_risk_engine(inp: dict) -> dict:
    inp = inp or {}
    sig = inp.get("signal") or {}
    acc = inp.get("account") or {}
    ins = inp.get("instrument") or {}
    st = inp.get("state") or {}
    lim = inp.get("limits") or {}

    L = {
        "max_daily_loss_pct": _num(lim.get("max_daily_loss_pct")) if _num(lim.get("max_daily_loss_pct")) is not None else 3,
        "max_trades": _num(lim.get("max_trades")) if _num(lim.get("max_trades")) is not None else 10,
        "max_open_positions": _num(lim.get("max_open_positions")) if _num(lim.get("max_open_positions")) is not None else 3,
        "max_consecutive_losses": _num(lim.get("max_consecutive_losses")) if _num(lim.get("max_consecutive_losses")) is not None else 3,
        "max_volatility_pct": _num(lim.get("max_volatility_pct")) if _num(lim.get("max_volatility_pct")) is not None else 5,
        "kill_switch": lim.get("kill_switch") is True,
    }

    def out(status, extra=None):
        o = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "risk_status": status,
            "allowed_quantity": 0,
            "risk_per_trade": None,
            "risk_per_trade_pct": None,
            "stop_loss": _num(sig.get("stop_loss")),
            "reasons": [],
            "limits_evaluated": L,
            "model_version": MODEL_VERSION,
        }
        if extra:
            o.update(extra)
        return o

    capital = _num(acc.get("capital"))
    risk_pct = _num(acc.get("risk_pct"))
    if risk_pct is None:
        risk_pct = 1
    entry = _num(sig.get("entry_ref"))
    sl = _num(sig.get("stop_loss"))
    target = _num(sig.get("target_1"))
    rr_min = _num(lim.get("rr_min")) if _num(lim.get("rr_min")) is not None else 1.0
    lot = _num(ins.get("lot_size"))
    if lot is None:
        lot = 1

    rejects = []
    if L["kill_switch"]:
        rejects.append("RISK: kill switch is ON")
    if capital is None or capital <= 0:
        rejects.append("RISK: capital not configured")
    if entry is None or sl is None:
        rejects.append("RISK: entry/stop missing - cannot size")
    if target is not None and entry is not None and sl is not None and abs(entry - sl) > 0:
        rr = abs(target - entry) / abs(entry - sl)
        if rr < rr_min:
            rejects.append(f"RISK: risk/reward {rr:.2f} < minimum {rr_min:g}")
    if sig.get("direction") not in ("BUY", "SELL"):
        rejects.append("RISK: no actionable direction")

    per_unit_risk = None
    if entry is not None and sl is not None:
        per_unit_risk = abs(entry - sl)
        if per_unit_risk == 0:
            rejects.append("RISK: zero stop distance")
        if sig.get("direction") == "BUY" and sl >= entry:
            rejects.append("RISK: BUY stop not below entry")
        if sig.get("direction") == "SELL" and sl <= entry:
            rejects.append("RISK: SELL stop not above entry")

    daily_pnl = _num(st.get("daily_pnl")) or 0
    max_daily_loss = -(capital * L["max_daily_loss_pct"] / 100) if capital is not None else None
    if max_daily_loss is not None and daily_pnl <= max_daily_loss:
        rejects.append(f"RISK: daily loss limit hit ({_round(daily_pnl)} <= {_round(max_daily_loss)})")
    if (_num(st.get("consecutive_losses")) or 0) >= L["max_consecutive_losses"]:
        rejects.append(f"RISK: consecutive-loss limit ({st.get('consecutive_losses')})")
    if (_num(st.get("trades_today")) or 0) >= L["max_trades"]:
        rejects.append(f"RISK: max trades/day reached ({st.get('trades_today')})")
    if (_num(st.get("open_positions")) or 0) >= L["max_open_positions"]:
        rejects.append(f"RISK: max open positions reached ({st.get('open_positions')})")

    vol = _num(inp.get("volatility_pct"))
    if vol is not None and vol > L["max_volatility_pct"]:
        rejects.append(f"RISK: abnormal volatility {vol}% > {L['max_volatility_pct']}%")
    if ins.get("liquidity_ok") is False:
        rejects.append("RISK: instrument liquidity flagged poor")
    spread = _num(ins.get("spread_pct"))
    if spread is not None and spread > 3:
        rejects.append(f"RISK: spread too wide {spread}%")

    if rejects:
        return out("REJECTED", {"reasons": rejects})

    risk_budget = capital * risk_pct / 100
    qty_units = math.floor(risk_budget / per_unit_risk) if per_unit_risk and per_unit_risk > 0 else 0
    lots = math.floor(qty_units / lot) if lot > 0 else 0
    allowed_qty = lots * lot

    margin = _num(acc.get("available_margin"))
    if margin is not None and entry and entry > 0:
        max_by_margin = math.floor(margin / entry)
        if max_by_margin < allowed_qty:
            allowed_qty = math.floor(max_by_margin / lot) * lot

    if allowed_qty <= 0:
        return out("REJECTED", {
            "risk_per_trade": _round(risk_budget),
            "reasons": ["RISK: computed quantity is 0 (risk budget too small for stop distance / margin)"],
        })

    actual_risk = allowed_qty * per_unit_risk
    return out("APPROVED", {
        "allowed_quantity": allowed_qty,
        "lots": lots,
        "risk_per_trade": _round(actual_risk),
        "risk_per_trade_pct": _round(100 * actual_risk / capital, 2),
        "per_unit_risk": _round(per_unit_risk),
        "reasons": [f"RISK: approved; sized {allowed_qty} units ({lots} lot(s)) at "
                    f"{_round(100*actual_risk/capital,2)}% risk"],
    })
