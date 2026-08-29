"""
AI-MASTER-ORCHESTRATOR — authoritative fail-closed gate.
Chains: AngelOne Connector -> Signal Engine -> OI Options Engine -> Risk Engine
       -> NO-TRADE GATE -> Log Signal -> (if approved) Paper Trading.

live_trading is hard-coded False everywhere and never derived from input.
This mirrors the n8n workflow's node-by-node data prep exactly.
"""
import json
import math
import os
import time
from datetime import datetime, timezone

from .engines.signal_engine import run_signal_engine
from .engines.oi_options_engine import run_oi_options_engine
from .engines.risk_engine import run_risk_engine
from .connectors import angelone
from . import db
from . import pipeline_core
from . import instruments


def _signal_id():
    return pipeline_core.signal_id("SIG")


def run_pipeline(req: dict) -> dict:
    req = req or {}
    symbol = instruments.canonical(req.get("symbol"))
    supplied_underlying = instruments.canonical(req.get("underlying")) if req.get("underlying") else symbol
    # The symbol is authoritative; an inconsistent caller-provided underlying
    # is rejected below rather than allowed to contaminate downstream engines.
    requested_underlying = symbol

    # --- 1. Connector (or pass through pre-supplied candles for backtest/replay) ---
    if req.get("candles") is not None:
        candles = req.get("candles") or []
        data_ts = candles[-1][0] if candles and isinstance(candles[-1], (list, tuple)) else None
        try:
            from datetime import datetime, timezone
            if isinstance(data_ts, (int, float)):
                ts = data_ts / 1000 if data_ts > 1e12 else data_ts
                data_iso = datetime.fromtimestamp(ts, timezone.utc).isoformat()
            else:
                data_iso = datetime.fromisoformat(str(data_ts).replace("Z", "+00:00")).isoformat()
            age = max(0.0, time.time() - datetime.fromisoformat(data_iso).timestamp())
        except Exception:
            data_iso, age = None, None
        conn = {
            "market": req.get("market"), "symbol": symbol,
            "instrument": req.get("instrument"), "timeframe": req.get("timeframe"),
            "data_status": "OK" if age is not None and age <= float(os.environ.get("CHANAKYA_MAX_DATA_AGE_SEC", "900")) else "STALE",
            "candles": candles, "data_timestamp": data_iso,
            "stale_seconds": age, "data_age_seconds": age,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "server_timestamp": datetime.now(timezone.utc).isoformat(),
            "snapshot_id": f"{(req.get('market') or 'UNKNOWN').upper()}-{symbol}-{int(time.time()*1000)}",
            "market_status": "UNKNOWN",
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
        "symbol": conn.get("symbol") or symbol,
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
        "underlying": requested_underlying,
        "spot": req.get("spot"),
        "expiry": req.get("expiry"),
        "directional_bias": sig.get("direction") or "NONE",
        "chain": req.get("chain") or [],
        "config": req.get("oi_config") or {},
    }
    oi = run_oi_options_engine(oi_input)

    # --- 3b. Turning-Point Engine (deterministic, additive/informational) ---
    tp = None
    if (req.get("signal_config") or {}).get("turning_point", True) and (conn.get("candles") or []):
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
        except Exception:
            tp = None

    # --- 4. Risk Engine ---
    calc = sig.get("calculations") or {}
    ez = sig.get("entry_zone") or {}
    risk_input = {
        "signal": {
            "direction": sig.get("direction") or "NONE",
            "entry_ref": ez.get("ref"),
            "stop_loss": sig.get("stop_loss"),
            "target_1": sig.get("target_1"),
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
    if (req.get("market") or conn.get("market") or "").upper() not in ("NSE", "MCX"):
        gate_msgs.append("GATE: MARKET_VALID failed")
    if symbol and supplied_underlying and symbol != supplied_underlying:
        gate_msgs.append(f"GATE: UNDERLYING_VALID failed (symbol={symbol}, underlying={supplied_underlying})")
    if not instruments.resolve(req.get("symbol")):
        gate_msgs.append("GATE: SYMBOL_VALID failed (instrument not in canonical registry)")
    data_status = conn.get("data_status") or "DATA_UNAVAILABLE"
    data_ok = data_status == "OK"
    if not data_ok:
        gate_msgs.append(f"GATE: data_status={data_status} (require OK)")
    # Connector timestamps are authoritative.  A fetched_at timestamp alone
    # must never make an old candle set appear live.
    age = conn.get("stale_seconds")
    max_age = float(os.environ.get("CHANAKYA_MAX_DATA_AGE_SEC", "900"))
    if age is None or not math.isfinite(float(age)):
        gate_msgs.append("GATE: data timestamp missing (DATA_VALID/DATA_FRESH failed)")
    elif float(age) > max_age:
        gate_msgs.append(f"GATE: data stale ({age}s > {max_age:g}s)")
    if conn.get("market_open") is False:
        gate_msgs.append("GATE: MARKET_CLOSED")

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

    # Mandatory contract/level gates.  These run even when an upstream model
    # reports a high probability, preventing zero/default fields from reaching
    # paper trading.
    direction = sig.get("direction")
    if direction not in ("BUY", "SELL"):
        gate_msgs.append("GATE: DIRECTION_VALID failed")
    def positive(value):
        try:
            return math.isfinite(float(value)) and float(value) > 0
        except (TypeError, ValueError):
            return False
    if not positive(ez.get("ref")):
        gate_msgs.append("GATE: ENTRY_VALID failed (entry_ref must be > 0)")
    if not positive(sig.get("stop_loss")):
        gate_msgs.append("GATE: STOP_VALID failed (stop_loss must be > 0)")
    if not positive(sig.get("target_1")):
        gate_msgs.append("GATE: TARGET_VALID failed (target_1 must be > 0)")
    if is_option:
        if not str(req.get("expiry") or "").strip():
            gate_msgs.append("GATE: CONTRACT_VALID failed (expiry missing)")
        if not positive(strike or sig.get("strike")):
            gate_msgs.append("GATE: CONTRACT_VALID failed (strike missing)")
        if option_type not in ("CE", "PE"):
            gate_msgs.append("GATE: CONTRACT_VALID failed (option_type missing)")

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
        "underlying": requested_underlying or sig.get("symbol") or "",
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
        "turning_point": tp,
        "snapshot_id": conn.get("snapshot_id") or f"{(conn.get('market') or req.get('market') or 'UNKNOWN').upper()}-{int(time.time()*1000)}",
        "data_timestamp": conn.get("data_timestamp") or conn.get("fetched_at"),
        "data_age_seconds": age,
        "server_timestamp": conn.get("server_timestamp") or datetime.now(timezone.utc).isoformat(),
        "market_status": conn.get("market_status") or ("OPEN" if conn.get("market_open") is True else "CLOSED" if conn.get("market_open") is False else "UNKNOWN"),
    }

    # --- 6. Log + notify ---
    pipeline_core.log_and_notify(contract)

    # --- 7. Paper trade if approved ---
    trade = None
    if approved:
        trade = pipeline_core.open_from_contract(contract, reason="orchestrator approved")

    # --- 8. Order Adapter (additive, opt-in via req['execution']['enabled']) ---
    execution = None
    if approved:
        from .execution.integration import run_execution
        execution = run_execution({**contract, "trade_id": (trade or {}).get("trade_id")
                                   or contract["signal_id"]}, req, connector=conn)
        if execution:
            contract["execution"] = execution

    return {
        "contract": contract,
        "connector": conn,
        "signal": sig,
        "oi": oi,
        "risk": risk,
        "turning_point": tp,
        "trade": trade,
        "execution": execution,
    }
