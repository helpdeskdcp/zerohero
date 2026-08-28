"""
AI-MASTER-ORCHESTRATOR — authoritative fail-closed gate.
Chains: AngelOne Connector -> Signal Engine -> OI Options Engine -> Risk Engine
       -> NO-TRADE GATE -> Log Signal -> (if approved) Paper Trading.

live_trading is hard-coded False everywhere and never derived from input.
This mirrors the n8n workflow's node-by-node data prep exactly.
"""
import time
import random
import json
from datetime import datetime, timezone

from .engines.signal_engine import run_signal_engine
from .engines.oi_options_engine import run_oi_options_engine
from .engines.risk_engine import run_risk_engine
from .engines.paper_trading import open_trade
from .connectors import angelone, telegram
from . import db


def _signal_id():
    return "SIG-" + format(int(time.time() * 1000), "x") + "-" + format(random.randint(0, 0xFFFFF), "x")


def run_pipeline(req: dict) -> dict:
    req = req or {}

    # --- 1. Connector (or pass through pre-supplied candles for backtest/replay) ---
    if req.get("candles") is not None:
        conn = {
            "market": req.get("market"), "symbol": req.get("symbol"),
            "instrument": req.get("instrument"), "timeframe": req.get("timeframe"),
            "data_status": "OK", "candles": req.get("candles"),
        }
    else:
        conn = angelone.fetch_candles(
            market=req.get("market"), symbol=req.get("symbol"),
            exchange=req.get("exchange"), symboltoken=req.get("symboltoken"),
            interval=req.get("interval"), fromdate=req.get("fromdate"),
            todate=req.get("todate"), timeframe=req.get("timeframe"),
            instrument=req.get("instrument"),
        )

    # --- 2. Signal Engine ---
    sig_input = {
        "market": conn.get("market") or req.get("market"),
        "symbol": conn.get("symbol") or req.get("symbol"),
        "instrument": conn.get("instrument") or req.get("instrument"),
        "timeframe": conn.get("timeframe") or req.get("timeframe"),
        "expiry": req.get("expiry"),
        "strike": req.get("strike"),
        "source": conn.get("source", "ANGELONE"),
        "data_status": conn.get("data_status", "DATA_UNAVAILABLE"),
        "candles": conn.get("candles") or [],
        "config": req.get("signal_config") or {},
    }
    sig = run_signal_engine(sig_input)

    # --- 3. OI Options Engine ---
    oi_input = {
        "underlying": req.get("underlying") or sig.get("symbol"),
        "spot": req.get("spot"),
        "expiry": req.get("expiry"),
        "directional_bias": sig.get("direction") or "NONE",
        "chain": req.get("chain") or [],
        "config": req.get("oi_config") or {},
    }
    oi = run_oi_options_engine(oi_input)

    # --- 4. Risk Engine ---
    calc = sig.get("calculations") or {}
    ez = sig.get("entry_zone") or {}
    risk_input = {
        "signal": {
            "direction": sig.get("direction") or "NONE",
            "entry_ref": ez.get("ref"),
            "stop_loss": sig.get("stop_loss"),
        },
        "account": req.get("account") or {},
        "instrument": req.get("risk_instrument") or {},
        "state": req.get("state") or {},
        "limits": req.get("limits") or {},
        "volatility_pct": calc.get("volatility_pct"),
    }
    risk = run_risk_engine(risk_input)

    # --- 5. NO-TRADE GATE (authoritative, fail-closed) ---
    gate_msgs = []
    data_ok = conn.get("data_status") == "OK"
    if not data_ok:
        gate_msgs.append(f"GATE: data_status={conn.get('data_status','MISSING')} (require OK)")

    signal_trade = sig.get("decision") == "TRADE"
    if not signal_trade:
        gate_msgs.append(f"GATE: signal.decision={sig.get('decision','MISSING')} (require TRADE)")

    risk_approved = risk.get("risk_status") == "APPROVED"
    if not risk_approved:
        gate_msgs.append(f"GATE: risk_status={risk.get('risk_status','MISSING')} (require APPROVED)")

    is_option = "OPTION" in str(sig.get("instrument") or req.get("instrument") or "").upper()
    option_type = None
    strike = None
    oi_evidence = ""
    if oi and oi.get("decision") == "TRADE":
        option_type = oi.get("option_type")
        strike = oi.get("recommended_strike")
        oi_evidence = json.dumps(oi.get("oi_evidence") or {})
    if is_option and not (oi and oi.get("decision") == "TRADE"):
        gate_msgs.append(f"GATE: options trade requires OI decision=TRADE (got {oi.get('decision') if oi else 'MISSING'})")

    approved = len(gate_msgs) == 0
    signal_id = _signal_id()

    reason_parts = (["GATE: APPROVED — all stages passed"] if approved else gate_msgs)
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
        "strike": strike or sig.get("strike") or 0,
        "option_type": option_type or "",
        "direction": sig.get("direction") or "NONE",
        "timeframe": sig.get("timeframe") or req.get("timeframe") or "",
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
        "model_version": "+".join(filter(None, [
            sig.get("model_version"), oi.get("model_version"), risk.get("model_version")])),
        "live_trading": 0,
        # extras (not persisted to log columns)
        "final_decision": "APPROVED" if approved else "NO_TRADE",
        "approved": approved,
        "allowed_quantity": risk.get("allowed_quantity") or 0,
        "oi_evidence": oi_evidence,
    }

    # --- 6. Log + notify ---
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

    # --- 7. Paper trade if approved ---
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
            "market_regime": contract["market_regime"], "oi_evidence": contract["oi_evidence"],
            "reason": "orchestrator approved",
        })

    return {
        "contract": contract,
        "connector": conn,
        "signal": sig,
        "oi": oi,
        "risk": risk,
        "trade": trade,
    }
