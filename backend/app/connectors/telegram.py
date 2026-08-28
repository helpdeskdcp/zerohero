"""
Telegram alerting — sends signal/trade notifications to your bot + channel.
Reads TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_SIGNALS_CHANNEL_ID from env.
Never raises on failure — alerting must never break the trading pipeline.
"""
import os
import requests

API_BASE = "https://api.telegram.org/bot{token}/sendMessage"


def _send(text, chat_id):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token or not chat_id:
        return {"ok": False, "reason": "TELEGRAM_NOT_CONFIGURED"}
    try:
        resp = requests.post(
            API_BASE.format(token=token),
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=8,
        )
        return {"ok": resp.ok, "status_code": resp.status_code}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def notify_signal(contract: dict):
    d = contract.get("direction", "NONE")
    emoji = "🟢" if d == "BUY" else ("🔴" if d == "SELL" else "⚪")
    lines = [
        f"{emoji} <b>{contract.get('decision','NO_TRADE')}</b> — {contract.get('underlying') or contract.get('symbol','')}",
        f"Direction: {d}  |  Regime: {contract.get('market_regime','-')}",
        f"Entry: {contract.get('entry_ref','-')}  SL: {contract.get('stop_loss','-')}  T1: {contract.get('target_1','-')}",
        f"Prob: {contract.get('probability','-')}%  Conf: {contract.get('confidence','-')}%  RR: {contract.get('risk_reward','-')}",
        f"Risk: {contract.get('risk_status','-')}  |  Signal ID: {contract.get('signal_id','-')}",
        "⚠️ PAPER MODE — live_trading=false",
    ]
    text = "\n".join(lines)
    chat_id = os.environ.get("TELEGRAM_SIGNALS_CHANNEL_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    return _send(text, chat_id)


def notify_position_alert(trade: dict):
    """Monitor-only alert for an externally-held (MANUAL) position: the app has
    detected the target or stop; the user must place the real exit themselves."""
    reason = (trade.get("exit_reason") or "").upper()
    pnl = trade.get("pnl", 0) or 0
    if reason == "TARGET":
        head = "🎯 <b>TARGET HIT — EXIT NOW</b>"
    elif reason in ("STOP", "TRAIL"):
        head = "🛑 <b>STOP HIT — EXIT NOW</b>"
    else:
        head = "⚠️ <b>Position monitor</b>"
    leg = f"{trade.get('option_type') or ''} {trade.get('strike') or ''}".strip()
    text = (
        f"{head}\n"
        f"{trade.get('underlying','')} {leg}  ({trade.get('direction')})\n"
        f"Entry {trade.get('entry')}  →  Mark {trade.get('exit_price')}  "
        f"(T1 {trade.get('target_1')} / SL {trade.get('stop_loss')})\n"
        f"Paper P&L on {int(trade.get('quantity') or 0)}: {round(pnl,2)}\n"
        f"⚠️ App is monitor-only — place the exit in your broker terminal."
    )
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    return _send(text, chat_id)


def notify_combo_alert(payload: dict):
    """Combined-position (strangle) exit signal — flatten BOTH legs."""
    head = {
        "COMBO_TARGET": "🎯 STRANGLE TARGET — EXIT BOTH LEGS",
        "COMBO_STOP": "🛑 STRANGLE STOP — CUT BOTH LEGS",
        "COMBO_TRAIL": "🔒 STRANGLE TRAIL — BANK BOTH LEGS",
    }.get(payload.get("reason"), "STRANGLE — review both legs")
    text = (
        f"<b>{head}</b>\n"
        f"{' / '.join(payload.get('legs') or [])}\n"
        f"Combined debit {payload.get('entry_combined')} → mark {payload.get('combined_mark')}\n"
        f"Paper P&L (pair): {payload.get('combined_pnl')}\n"
        f"⚠️ Monitor-only — square off BOTH legs in your broker."
    )
    return _send(text, os.environ.get("TELEGRAM_CHAT_ID"))


def notify_trade_closed(trade: dict):
    pnl = trade.get("pnl", 0) or 0
    emoji = "✅" if pnl > 0 else ("❌" if pnl < 0 else "➖")
    text = (
        f"{emoji} Trade Closed — {trade.get('underlying','')} {trade.get('option_type') or ''} "
        f"{trade.get('strike') or ''}\n"
        f"Direction: {trade.get('direction')}  Result: {trade.get('result')}\n"
        f"Entry: {trade.get('entry')}  Exit: {trade.get('exit_price')}  PnL: {round(pnl,2)}\n"
        f"Trade ID: {trade.get('trade_id')}"
    )
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    return _send(text, chat_id)
