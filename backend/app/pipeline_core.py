"""
Shared pipeline plumbing — the parts of run_pipeline (orchestrator) and
run_scalp_pipeline (scalp) that were byte-for-byte identical.

Deliberately small: only the signal-id, the ai_signals_log column filter +
Telegram notify, and the contract -> open_trade mapping are shared. Each
pipeline keeps its own connector step, engine calls, gate logic and contract
dict — those genuinely differ (OI stage, reason ordering, scalp extras) and
the audit says orchestrator must keep its 1:1 n8n semantics.
"""
import time
import random

from . import db
from .connectors import telegram
from .engines.paper_trading import open_trade

# columns that actually exist on ai_signals_log — anything else on the
# contract dict (final_decision, approved, allowed_quantity, oi_evidence,
# scalp extras) is runtime-only and not persisted.
_SIGNAL_COLS = (
    "signal_id", "created_ts", "market", "symbol", "instrument", "underlying",
    "expiry", "strike", "option_type", "direction", "timeframe", "entry_ref",
    "target_1", "target_2", "stop_loss", "trailing_stop", "probability",
    "confidence", "risk_reward", "market_regime", "decision", "data_status",
    "risk_status", "reason", "model_version", "live_trading",
)


def signal_id(prefix: str) -> str:
    return (f"{prefix}-" + format(int(time.time() * 1000), "x")
            + "-" + format(random.randint(0, 0xFFFFF), "x"))


def log_and_notify(contract: dict) -> None:
    db.insert_signal({k: contract[k] for k in contract if k in _SIGNAL_COLS})
    try:
        telegram.notify_signal(contract)
    except Exception:
        pass


def open_from_contract(contract: dict, *, reason: str, extra: dict | None = None) -> dict:
    row = {
        "signal_id": contract["signal_id"],
        "market": contract["market"], "underlying": contract["underlying"],
        "instrument": contract["instrument"], "expiry": contract["expiry"],
        "strike": contract["strike"], "option_type": contract["option_type"],
        "direction": contract["direction"], "timeframe": contract["timeframe"],
        "entry": contract["entry_ref"], "target_1": contract["target_1"],
        "target_2": contract["target_2"], "stop_loss": contract["stop_loss"],
        "trailing_stop": contract["trailing_stop"], "quantity": contract["allowed_quantity"],
        "probability": contract["probability"], "confidence": contract["confidence"],
        "market_regime": contract["market_regime"],
        "oi_evidence": contract.get("oi_evidence", ""),
        "reason": reason,
    }
    if extra:
        row.update(extra)
    return open_trade(row)
