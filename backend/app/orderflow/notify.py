"""
Telegram delivery for order-flow (smart-money) signals.

Pure card builder + a dedup-aware sender. A smart-money "signal" here is one
spike candle's BUY or SELL setup that has ACTUALLY broken out (outcome status
!= PENDING). Dedup identity = (symbol, session_date, candle bar_start, side) so
the same setup is never sent twice, even across restarts (marker persisted in
app_settings).

Sending never raises into a caller -- a Telegram failure is logged and
swallowed, matching app/autoscalp/notify.py.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from .. import db

_log = logging.getLogger(__name__)
BAR = "━" * 22
_SENT_KEY = "orderflow_signals_sent"      # app_settings: JSON list of dedup ids


def _fmt(x, nd=2):
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return "-"


def signal_card(*, symbol: str, session_date: str, spike: dict, side: str) -> str:
    """`spike` is one element of smart_money_setups()['setups']; `side` in
    {'buy','sell'}."""
    leg = spike[side]
    c = spike["candle"]
    oc = (leg.get("outcome") or {}).get("status", "-")
    return "\n".join([
        BAR, "      ORDERFLOW SIGNAL", BAR, "",
        f"Instrument: {symbol}",
        f"Session: {session_date}",
        f"Direction: {leg['side']}", "",
        f"Smart-money candle: {c.get('bar_start')}",
        f"  O/H/L/C: {_fmt(c.get('o'))} / {_fmt(c.get('h'))} / {_fmt(c.get('l'))} / {_fmt(c.get('c'))}",
        f"  Volume: {_fmt(c.get('v'), 0)}  ({spike.get('volume_x_avg')}x session avg)", "",
        f"Entry (breakout): {_fmt(leg.get('entry'))}",
        f"Stop Loss: {_fmt(leg.get('stop_loss'))}",
        f"Target: {_fmt(leg.get('target'))}",
        f"Risk / Reward: {_fmt(leg.get('risk_points'))} pts  ->  1:{leg.get('rr')}", "",
        f"Breakout bar: {leg.get('breakout_bar') or '-'}",
        f"Outcome so far: {oc}", "",
        "OHLCV ~5m bars, not tick data. Breakout/target/stop judged at bar",
        "granularity; a bar spanning both -> STOP assumed.",
        BAR,
    ])


def _sent_ids() -> set:
    try:
        return set(json.loads(db.get_setting(_SENT_KEY) or "[]"))
    except Exception:
        return set()


def _mark_sent(ids: set):
    try:
        # cap the persisted list so it can't grow without bound
        keep = sorted(ids)[-2000:]
        db.set_setting(_SENT_KEY, json.dumps(keep))
    except Exception as e:
        _log.warning("orderflow.notify: could not persist sent-markers: %r", e)


def _dedup_id(symbol: str, session_date: str, spike: dict, side: str) -> str:
    return f"{symbol.upper()}|{session_date}|{spike['candle'].get('bar_start')}|{side}"


def _breakout_age_min(leg: dict) -> float | None:
    bs = leg.get("breakout_bar")
    if not bs:
        return None
    try:
        dt = datetime.fromisoformat(str(bs).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
    except Exception:
        return None


def push_new_signals(symbol: str, session_date: str, sm_result: dict, *,
                     chat_id: str | None = None, dry_run: bool = False,
                     max_breakout_age_min: float | None = 20.0) -> dict:
    """Send a card for every spike setup (BUY and SELL) that has broken out
    (outcome != PENDING) and hasn't been sent before.

    `max_breakout_age_min`: a setup whose breakout is older than this is NOT
    sent (a signal from hours ago isn't actionable) but IS marked handled, so
    the first run of a day doesn't dump the whole morning's backlog and later
    runs don't fire it either. None = send regardless of age (used by tests /
    the on-demand API path)."""
    if sm_result.get("status") != "OK":
        return {"sent": 0, "skipped_status": sm_result.get("status")}

    cid = chat_id or os.environ.get("TELEGRAM_SIGNALS_CHANNEL_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    already = _sent_ids()
    newly = set()
    sent = 0
    considered = 0
    suppressed_stale = 0

    for spike in sm_result.get("setups", []):
        for side in ("buy", "sell"):
            leg = spike.get(side) or {}
            status = (leg.get("outcome") or {}).get("status")
            if status in (None, "PENDING"):
                continue                       # not a signal yet -- no breakout
            considered += 1
            did = _dedup_id(symbol, session_date, spike, side)
            if did in already:
                continue
            age = _breakout_age_min(leg)
            if (max_breakout_age_min is not None and age is not None
                    and age > max_breakout_age_min):
                newly.add(did)                 # mark handled, don't send stale backlog
                suppressed_stale += 1
                continue
            text = signal_card(symbol=symbol, session_date=session_date, spike=spike, side=side)
            if dry_run:
                newly.add(did)
                sent += 1
                continue
            ok = False
            if cid and os.environ.get("TELEGRAM_BOT_TOKEN"):
                try:
                    from ..connectors import telegram
                    r = telegram._send(text, cid)
                    ok = bool(r.get("ok"))
                    if not ok:
                        _log.warning("orderflow.notify: telegram send not ok: %r", r)
                except Exception as e:
                    _log.warning("orderflow.notify: telegram send failed: %r", e)
            else:
                _log.info("orderflow.notify: no telegram creds; would send:\n%s", text)
            if ok or not (cid and os.environ.get("TELEGRAM_BOT_TOKEN")):
                # mark as handled either on a successful send OR when there are
                # no creds at all (so a creds-less box doesn't spam its log
                # forever) -- but NOT on a real send failure, so that retries.
                newly.add(did)
                if ok:
                    sent += 1

    if newly and not dry_run:
        _mark_sent(already | newly)
    return {"sent": sent, "considered": considered, "new_marked": len(newly),
            "suppressed_stale": suppressed_stale,
            "chat_id_present": bool(cid), "dry_run": dry_run}
