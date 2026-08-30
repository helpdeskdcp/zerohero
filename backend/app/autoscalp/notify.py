"""spec-14 Telegram formatting for the autonomous scalper.

Pure string builders + a non-blocking sender wrapper. A Telegram failure must
never propagate into the trading loop, so `push()` swallows everything.
"""
from __future__ import annotations

BAR = "━" * 22


def _g(d, *ks, default="-"):
    for k in ks:
        v = d.get(k)
        if v is not None and v != "":
            return v
    return default


def signal_card(sig: dict, *, symbol: str, index_ltp=None, status: str = "PAPER") -> str:
    side = str(sig.get("decision", "")).replace("BUY_", "")
    return "\n".join([
        BAR, "     IDADDY AI SIGNAL", BAR, "",
        f"Instrument: {symbol}",
        f"Direction: {_g(sig, 'direction')}", "",
        "Setup:", str(_g(sig, 'signal_type')), "",
        f"Index:\n{_g(sig, 'index_ltp', default=index_ltp)}", "",
        f"Level ({_g(sig,'sr_side')}):\n{_g(sig, 'sr_level')}", "",
        f"Option:\n{symbol} {side} {_g(sig,'strike')}  ({_g(sig,'opt_tradingsymbol','tradingsymbol')})", "",
        f"Entry:\n{_g(sig, 'entry')}",
        f"Stop Loss:\n{_g(sig, 'stop_loss')}",
        f"Target 1:\n{_g(sig, 'target_1')}",
        f"Target 2:\n{_g(sig, 'target_2')}", "",
        f"Signal Score:\n{_g(sig, 'signal_score')}/100",
        f"Probability:\n{round(float(_g(sig,'probability',default=0))*100)}%",
        f"Confidence:\n{_g(sig, 'confidence')}", "",
        f"Regime: {_g(sig, 'regime')}",
        f"MTF: {_g(sig, 'mtf_alignment')}",
        f"Support str / Resistance str: {_g(sig,'support_strength')} / {_g(sig,'resistance_strength')}",
        f"Risk/Reward: 1:{_g(sig, 'rr')}",
        f"Expected value: {_g(sig, 'ev')}", "",
        f"Status:\n{status}  (no live order)",
        BAR,
    ])


_LIFECYCLE_ICON = {
    "ENTRY": "▶", "TARGET": "✅", "TARGET_2": "\U0001f3af", "STOP": "❌",
    "TRAIL": "\U0001f512", "TIME": "⏱", "CANCELLED": "⛔",
    "FALSE_BREAKOUT": "⚠️", "EXIT": "✔", "RESULT": "\U0001f4ca",
}


def lifecycle(kind: str, trade: dict, *, note: str = "", status: str = "PAPER") -> str:
    icon = _LIFECYCLE_ICON.get(kind.upper(), "•")
    head = (f"{icon} <b>AUTO-SCALP {kind.upper().replace('_', ' ')} — "
            f"{_g(trade, 'underlying', 'symbol')} {_g(trade, 'option_type', 'opt_type', default='')}"
            f"{int(float(_g(trade, 'strike', 'opt_strike', default=0)))}</b>")
    lines = [head]
    if _g(trade, "entry", default=None) is not None:
        lines.append(f"entry {_g(trade, 'entry')}  "
                     f"exit {_g(trade, 'exit_price', default='-')}  "
                     f"P&L {_g(trade, 'pnl', 'points', default='-')}  "
                     f"({_g(trade, 'result', 'outcome', default='')})")
    if note:
        lines.append(note)
    lines.append(f"{status} — monitor / no live order")
    return "\n".join(lines)


def push(send_fn, text: str) -> bool:
    """Fire-and-forget. Any error (network, config, formatting) is swallowed."""
    try:
        if send_fn and text:
            send_fn(text)
            return True
    except Exception:
        pass
    return False
