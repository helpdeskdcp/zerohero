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
import time
import random
import json
from datetime import datetime, timezone

from .engines.scalp_engine import run_scalp_engine
from .engines.risk_engine import run_risk_engine
from .engines.paper_trading import open_trade
from .connectors import angelone, telegram
from . import db


def _signal_id():
    return "SCL-" + format(int(time.time() * 1000), "x") + "-" + format(random.randint(0, 0xFFFFF), "x")


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

    # --- 3. risk engine ---
    ez = sig.get("entry_zone") or {}
    risk = run_risk_engine({
        "signal": {
            "direction": sig.get("direction") or "NONE",
            "entry_ref": ez.get("ref"),
            "stop_loss": sig.get("stop_loss"),
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
    }

    db.insert_signal({k: contract[k] for k in contract if k in (
        "signal_id", "created_ts", "market", "symbol", "instrument", "underlying",
        "expiry", "strike", "option_type", "direction", "timeframe", "entry_ref",
        "target_1", "target_2", "stop_loss", "trailing_stop", "probability",
        "confidence", "risk_reward", "market_regime", "decision", "data_status",
        "risk_status", "reason", "model_version", "live_trading")})
    try:
        telegram.notify_signal(contract)
    except Exception:
        pass

    trade = None
    if approved:
        trade = open_trade({
            "signal_id": contract["signal_id"],
            "market": contract["market"], "underlying": contract["underlying"],
            "instrument": contract["instrument"], "expiry": contract["expiry"],
            "strike": contract["strike"], "option_type": contract["option_type"],
            "direction": contract["direction"], "timeframe": contract["timeframe"],
            "entry": contract["entry_ref"], "target_1": contract["target_1"],
            "target_2": contract["target_2"], "stop_loss": contract["stop_loss"],
            "trailing_stop": contract["trailing_stop"], "quantity": contract["allowed_quantity"],
            "probability": contract["probability"], "confidence": contract["confidence"],
            "market_regime": contract["market_regime"], "oi_evidence": "",
            "reason": f"scalp {contract['setup']} approved",
            "strategy": "SCALP", "setup": contract["setup"], "atr_pct": contract["atr_pct"],
            "max_hold_sec": contract["max_hold_sec"],
        })

    return {"contract": contract, "connector": conn, "signal": sig, "risk": risk, "trade": trade}
