"""
Shadow-mode Telegram notification -- sends a message ONLY after the full
DATA -> DETERMINISTIC -> BEHAVIOR -> GROQ SHADOW -> FUSION pipeline has
completed (never for an intermediate/partial state). Opt-in, default OFF
(ai_shadow_mode.telegram_enabled) -- app.ai.shadow.run_shadow_decision's own
DB logging already works identically with this OFF; this module only adds
the Telegram side-effect on top, and never influences the real paper trade
(same as shadow.py itself -- this is observation, not execution).

Routes through the existing canonical dispatcher (app.telegram_dispatcher)
for dedup/agreement/conflict detection rather than talking to
app.connectors.telegram directly -- reuse, not a second dedup scheme.

NO_TRADE is never sent (the whole point of shadow mode is that AI can only
weaken a decision toward NO_TRADE; a NO_TRADE result carries nothing new to
announce). WEAK_BUY/WEAK_SELL are opt-in via
ai_shadow_mode.telegram_include_weak (default False), matching the "everything
new here is opt-in, default OFF" convention used throughout app/ai/.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import telegram_dispatcher

_STRONG_STATES = {"BUY", "STRONG_BUY", "SELL", "STRONG_SELL"}
_WEAK_STATES = {"WEAK_BUY", "WEAK_SELL"}
_BUY_SIDE = {"WEAK_BUY", "BUY", "STRONG_BUY"}


def extract_spot(bars_by_tf: dict | None) -> float | None:
    """Real last-close of the most granular available timeframe -- the same
    bars decide_from_context() already saw, so this is never a fresh read
    and never look-ahead. None (never a fabricated number) if bars_by_tf
    wasn't passed or is empty."""
    if not bars_by_tf:
        return None
    for tf in ("1m", "3m", "5m", "15m", "30m", "1h"):
        rows = bars_by_tf.get(tf)
        if not rows:
            continue
        last = rows[-1]
        c = last[4] if isinstance(last, (list, tuple)) and len(last) > 4 else \
            (last.get("c") if isinstance(last, dict) else None)
        try:
            return float(c) if c is not None else None
        except (TypeError, ValueError):
            continue
    return None


def _option_side(sig: dict) -> str | None:
    """CE/PE from the ORIGINAL deterministic decision -- fusion only ever
    weakens the same side toward NO_TRADE, it never flips CE<->PE, so this
    stays valid for whatever final_state fusion produced."""
    d = sig.get("decision")
    if d == "BUY_CE":
        return "CE"
    if d == "BUY_PE":
        return "PE"
    return None


def _invalidation_line(sig: dict, side: str) -> str:
    sl = sig.get("stop_loss")
    if sl is None:
        return "not available"
    return f"below {sl}" if side == "CE" else f"above {sl}"


def _key_reasons(sig: dict) -> list[str]:
    """Real reasons only -- sig["reason"] is the same pipe-joined string
    already shown on the dashboard/DB row, never invented for this message."""
    raw = str(sig.get("reason") or "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split("|") if p.strip()]


def format_message(symbol: str, sig: dict, ai_result: dict, fused: dict,
                   spot: float | None = None) -> str:
    side = _option_side(sig)
    decision_label = f"BUY {side}" if side else str(sig.get("decision") or "?")
    reasons = _key_reasons(sig)
    reasons_block = "\n".join(f"• {r}" for r in reasons) or "(none recorded)"
    ai_status = ai_result.get("status") or "UNAVAILABLE"
    ai_validation = ai_result.get("signal_validation") if ai_status == "OK" else ai_status
    ai_risk = ai_result.get("risk") if ai_status == "OK" else "N/A"
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    return (
        "ZEROHERO SHADOW SIGNAL\n\n"
        f"Symbol: {symbol}\n"
        f"Spot: {spot if spot is not None else 'not available'}\n"
        f"Decision: {decision_label}\n"
        f"Regime: {fused.get('regime') or sig.get('regime') or 'UNKNOWN'}\n\n"
        f"Deterministic Score: {sig.get('signal_score')}\n"
        f"Deterministic Confidence: {sig.get('confidence')}\n\n"
        f"Groq Shadow: {ai_validation}\n"
        f"AI Risk: {ai_risk}\n\n"
        f"Final Shadow Decision: {fused.get('final_state')}\n\n"
        f"Key Reasons:\n{reasons_block}\n\n"
        f"Invalidation:\n{_invalidation_line(sig, side) if side else 'not available'}\n\n"
        "Mode:\nPAPER / SHADOW ONLY\n\n"
        f"Timestamp:\n{ts}\n\n"
        "IMPORTANT:\n"
        "This is a shadow/paper signal.\n"
        "NO LIVE BROKER ORDER WAS PLACED."
    )


def _signal_zone(sig: dict) -> str:
    """Same setup shouldn't re-announce every tick -- bucket by the anchor
    S/R level (rounded) rather than by strike/premium, which moves tick to
    tick even for the "same" setup."""
    level = sig.get("sr_level")
    return str(round(level, 0)) if level is not None else "NA"


def build_signal_id(symbol: str, sig: dict) -> str:
    """symbol + decision + signal_zone + a 10-minute time bucket. Exposed
    publicly (not just an internal helper) so a caller can check for an
    existing row with this signal_id in the persisted shadow_decisions table
    BEFORE calling maybe_notify() -- restart-safe dedup, unlike
    telegram_dispatcher's own in-memory-only recent-signal registry, which
    resets on every process restart."""
    time_bucket = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")[:-1]   # 10-min bucket
    return f"{symbol}:{sig.get('decision')}:{_signal_zone(sig)}:{time_bucket}"


def should_notify(sig: dict, fused: dict, cfg: dict | None = None) -> bool:
    """Pure gating check (no I/O) -- same STRONG/WEAK/side rules maybe_notify
    applies, exposed separately so a caller can decide whether it's even
    worth querying the DB for a duplicate before doing any of that work."""
    cfg = cfg or {}
    if not cfg.get("telegram_enabled", False):
        return False
    final_state = fused.get("final_state")
    if final_state not in _STRONG_STATES:
        if not (final_state in _WEAK_STATES and cfg.get("telegram_include_weak", False)):
            return False
    return _option_side(sig) is not None


def maybe_notify(symbol: str, sig: dict, ai_result: dict, fused: dict, *,
                 cfg: dict | None = None, bars_by_tf: dict | None = None,
                 signal_id: str | None = None) -> dict | None:
    """Best-effort, never raises (matches shadow.py's own contract). Returns
    the dispatcher's record dict, or None if gated out / notifications are
    disabled / anything goes wrong. `signal_id`: pass build_signal_id()'s
    value if the caller already computed it (e.g. to dedup-check first);
    computed fresh here otherwise."""
    try:
        if not should_notify(sig, fused, cfg):
            return None

        final_state = fused.get("final_state")
        direction = "BULLISH" if final_state in _BUY_SIDE else "BEARISH"
        sid = signal_id or build_signal_id(symbol, sig)

        text = format_message(symbol, sig, ai_result, fused, spot=extract_spot(bars_by_tf))
        rec = telegram_dispatcher.dispatch(
            source_engine="zerohero_shadow", underlying=symbol, direction=direction,
            text=text, signal_id=sid)
        rec["signal_id"] = sid
        return rec
    except Exception:
        return None
