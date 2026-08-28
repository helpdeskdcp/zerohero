"""
Scalp pipeline — a lean, fail-closed chain purpose-built for scalping.

Deliberately NOT routed through AI-MASTER-ORCHESTRATOR (that stays a 1:1
n8n port). Shape of the result mirrors run_pipeline so the frontend and
WebSocket feed treat both the same.

    connector (or replay candles) -> scalp engine -> risk engine
      -> NO-TRADE GATE (data OK + scalp TRADE + risk APPROVED)
      -> log to ai_signals_log -> (if approved) open SCALP paper trade

live_trading is hard-coded False. No broker order call exists in this file.
"""
from datetime import datetime, timezone

from .engines.scalp_engine import run_scalp_engine
from .engines.risk_engine import run_risk_engine
from .connectors import angelone
from . import db
from . import pipeline_core


def _signal_id():
    return pipeline_core.signal_id("SCL")


def run_scalp_pipeline(req: dict) -> dict:
    req = req or {}

    # --- 1. data ---
    if req.get("candles") is not None:
        conn = {
            "market": req.get("market"), "symbol": req.get("symbol"),
            "instrument": req.get("instrument"), "timeframe": req.get("timeframe"),
            "data_status": "OK", "candles": req.get("candles"), "source": "REPLAY",
        }
    else:
        conn = angelone.fetch_candles(
            market=req.get("market"), symbol=req.get("symbol"),
            exchange=req.get("exchange"), symboltoken=req.get("symboltoken"),
            interval=req.get("interval"), fromdate=req.get("fromdate"),
            todate=req.get("todate"), timeframe=req.get("timeframe") or "1m",
            instrument=req.get("instrument"),
        )

    # --- 2. scalp engine ---
    sig = run_scalp_engine({
        "market": conn.get("market") or req.get("market"),
        "symbol": conn.get("symbol") or req.get("symbol"),
        "instrument": conn.get("instrument") or req.get("instrument"),
        "timeframe": conn.get("timeframe") or req.get("timeframe") or "1m",
        "expiry": req.get("expiry"),
        "strike": req.get("strike"),
        "source": conn.get("source", "ANGELONE"),
        "candles": conn.get("candles") or [],
        "instrument_meta": req.get("instrument_meta") or {},
        "config": req.get("scalp_config") or {},
    })

    # --- 2b. Turning-Point Engine (deterministic; additive — informs, never
    #         silently overrides). Reuses the scalp engine's indicator calc. ---
    tp = None
    if req.get("turning_point", True) and (conn.get("candles") or []):
        try:
            from .engines.turning_point_engine import run_turning_point_engine
            from . import tp_calibration
            tp = run_turning_point_engine({
                "candles": conn.get("candles") or [],
                "signal_calc": sig.get("calculations"),
                "chain": req.get("chain"),
                "config": req.get("tp_config") or {},
                "calibration": tp_calibration.load(),
            })
            if req.get("tp_record", True):
                tp_calibration.record(tp, sig.get("symbol") or req.get("symbol") or "",
                                      sig.get("timeframe") or req.get("timeframe") or "1m")
        except Exception:
            tp = None

    _tp_dir = {"UP_TURN": "BUY", "DOWN_TURN": "SELL"}.get((tp or {}).get("direction"))
    tp_agrees = bool(_tp_dir and _tp_dir == sig.get("direction"))
    tp_opposes = bool(_tp_dir and sig.get("direction") in ("BUY", "SELL") and _tp_dir != sig.get("direction"))
    tp_confirmed = bool(tp and tp.get("high_confidence") and tp_agrees)

    # --- 3. risk engine ---
    ez = sig.get("entry_zone") or {}
    # when the turn is confirmed and tp_use_levels is on, hand Risk the
    # zone-based entry/stop instead of the raw scalp levels
    _tref = (tp or {}).get("trade_ref") or {}
    if tp_confirmed and req.get("tp_use_levels") and _tref.get("entry_ref"):
        risk_entry, risk_stop = _tref["entry_ref"], _tref["stop_loss"]
    else:
        risk_entry, risk_stop = ez.get("ref"), sig.get("stop_loss")
    risk = run_risk_engine({
        "signal": {
            "direction": sig.get("direction") or "NONE",
            "entry_ref": risk_entry,
            "stop_loss": risk_stop,
        },
        "account": req.get("account") or {},
        "instrument": req.get("risk_instrument") or {},
        "state": req.get("state") or {},
        "limits": req.get("limits") or {},
        "volatility_pct": sig.get("atr_pct"),
    })

    # --- 4. NO-TRADE GATE (fail-closed) ---
    gate = []
    if conn.get("data_status") != "OK":
        gate.append(f"GATE: data_status={conn.get('data_status', 'MISSING')} (require OK)")
    if sig.get("decision") != "TRADE":
        gate.append(f"GATE: scalp.decision={sig.get('decision', 'MISSING')} (require TRADE)")
    if risk.get("risk_status") != "APPROVED":
        gate.append(f"GATE: risk_status={risk.get('risk_status', 'MISSING')} (require APPROVED)")
    if req.get("tp_veto") and tp and tp.get("high_confidence") and tp_opposes:
        gate.append(f"GATE: turning-point engine opposes ({tp['direction']} conf {tp['confidence']}%)")
    approved = not gate

    signal_id = _signal_id()
    reason_parts = (["GATE: APPROVED — scalp stages passed"] if approved else gate)
    reason_parts += (sig.get("reason") or [])[:3]
    reason_parts += (risk.get("reasons") or [])[:2]

    contract = {
        "signal_id": signal_id,
        "created_ts": datetime.now(timezone.utc).isoformat(),
        "market": sig.get("market") or req.get("market") or "",
        "symbol": sig.get("symbol") or req.get("symbol") or "",
        "instrument": sig.get("instrument") or req.get("instrument") or "",
        "underlying": req.get("underlying") or sig.get("symbol") or "",
        "expiry": sig.get("expiry") or req.get("expiry") or "",
        "strike": sig.get("strike") or req.get("strike") or 0,
        "option_type": req.get("option_type") or "",
        "direction": sig.get("direction") or "NONE",
        "timeframe": sig.get("timeframe") or req.get("timeframe") or "1m",
        "entry_ref": ez.get("ref") or 0,
        "target_1": sig.get("target_1") or 0,
        "target_2": sig.get("target_2") or 0,
        "stop_loss": sig.get("stop_loss") or 0,
        "trailing_stop": sig.get("trailing_stop") or 0,
        "probability": sig.get("probability") or 0,
        "confidence": sig.get("confidence") or 0,
        "risk_reward": sig.get("risk_reward") or 0,
        "market_regime": sig.get("market_regime") or "",
        "decision": sig.get("decision") or "NO_TRADE",
        "data_status": conn.get("data_status") or "DATA_UNAVAILABLE",
        "risk_status": risk.get("risk_status") or "REJECTED",
        "reason": " | ".join(reason_parts)[:900],
        "model_version": "+".join(filter(None, [sig.get("model_version"), risk.get("model_version")])),
        "live_trading": 0,
        # scalp extras (not persisted to ai_signals_log columns)
        "strategy": "SCALP",
        "setup": sig.get("setup") or "",
        "atr_pct": sig.get("atr_pct"),
        "max_hold_sec": sig.get("max_hold_sec"),
        "tick_target": sig.get("tick_target"),
        "tick_stop": sig.get("tick_stop"),
        "final_decision": "APPROVED" if approved else "NO_TRADE",
        "approved": approved,
        "allowed_quantity": risk.get("allowed_quantity") or 0,
        "turning_point": tp,
        "tp_confirmed": tp_confirmed,
        "tp_agrees": tp_agrees,
        "tp_opposes": tp_opposes,
    }
    if tp_confirmed:
        reason_parts.append(f"TP-CONFIRMED ({tp['direction']} conf {tp['confidence']}%, "
                            f"p_up {tp['p_up']}, exp {tp['expected_move']['pts']}pts)")
        contract["reason"] = " | ".join(reason_parts)[:900]

    pipeline_core.log_and_notify(contract)

    trade = None
    if approved:
        trade = pipeline_core.open_from_contract(
            contract, reason=f"scalp {contract['setup']} approved",
            extra={"strategy": "SCALP", "setup": contract["setup"],
                   "atr_pct": contract["atr_pct"], "max_hold_sec": contract["max_hold_sec"]})

    # --- 5. Order Adapter (additive, opt-in via req['execution']['enabled']) ---
    # Pre-arm + submit the approved contract through the OrderManager. The paper
    # trade row above is the POSITION ledger; broker_orders is the ORDER ledger.
    # Default OFF — with no execution block this is a no-op.
    execution = None
    if approved:
        from .execution.integration import run_execution
        execution = run_execution({**contract, "trade_id": (trade or {}).get("trade_id")
                                   or contract["signal_id"]}, req, connector=conn)
        if execution:
            contract["execution"] = execution

    return {"contract": contract, "connector": conn, "signal": sig, "risk": risk,
            "turning_point": tp, "trade": trade, "execution": execution}
