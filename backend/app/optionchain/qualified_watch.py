"""Option-Chain QUALIFIED-verdict watcher -- read-only, alerts on state change
only. For each watched underlying, computes today's structure gate
(get_chain -> analyze -> qualify) from already-captured data and pushes ONE
Telegram alert the moment its verdict flips to QUALIFIED for a given
direction (dedup: one per underlying+direction+day, persisted via
app.db.get_setting/set_setting -- same pattern as app/orderflow/notify.py).

This gate is structure-only: it has no entry price, strike, SL or target of
its own. The alert says so explicitly and points at /api/signals/unified for
an actual tradeable contract. Never calls the broker beyond what get_chain
already does with allow_network=False (captured-data only); no order path.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .. import db, telegram_dispatcher
from .qualify import qualify
from .resolve import get_chain
from .structure import analyze

WATCHLIST = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX",
             "CRUDEOIL", "NATURALGAS")
_SENT_KEY = "optionchain_qualified_alert_sent"


def _sent_ids() -> set:
    return set(json.loads(db.get_setting(_SENT_KEY) or "[]"))


def _mark_sent(ids: set) -> None:
    db.set_setting(_SENT_KEY, json.dumps(sorted(ids)))


def _alert_text(sym: str, d: dict, spot) -> str:
    gates = "; ".join(f"{g['name']}:{g['status']}" for g in d.get("gates", []))
    return (
        f"OPTIONCHAIN STRUCTURE QUALIFIED -- {sym} {d.get('direction')}\n"
        f"spot: {spot}  regime: {d.get('regime_context')}\n"
        f"structure_bias: {d.get('structure_bias')}\n"
        f"gates: {gates}\n"
        f"summary: {d.get('summary')}\n"
        f"NOTE: structure-only gate -- no entry/strike/SL/target of its own. "
        f"Cross-check autoscalp/HCS before acting. Research-only, not an order."
    )


def scan_and_alert(watchlist=WATCHLIST, today: str | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date().isoformat()
    already = _sent_ids()
    newly: set = set()
    per_symbol: dict = {}

    for sym in watchlist:
        try:
            chain = get_chain(sym, "AUTO", allow_network=False)
            if chain is None or not chain.rows:
                per_symbol[sym] = {"status": "NO_DATA"}
                continue
            d = qualify(analyze(chain)).to_dict()
            verdict, direction = d.get("verdict"), d.get("direction")
            per_symbol[sym] = {"verdict": verdict, "direction": direction}
            dedup_key = f"{today}:{sym}:{direction}"
            if verdict != "QUALIFIED" or dedup_key in already:
                continue
            telegram_dispatcher.dispatch(
                source_engine="optionchain_qualified_watch", underlying=sym,
                direction=direction, text=_alert_text(sym, d, chain.spot))
            newly.add(dedup_key)
            per_symbol[sym]["alerted"] = True
        except Exception as e:
            per_symbol[sym] = {"status": "ERROR", "detail": f"{type(e).__name__}: {e}"}

    if newly:
        _mark_sent(already | newly)
    return {"today": today, "alerted": sorted(newly), "results": per_symbol}
