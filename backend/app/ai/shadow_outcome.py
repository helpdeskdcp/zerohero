"""
Forward-outcome tracking for shadow decisions -- answers "did the signal we
sent actually reach its target?" using only REAL captured option-premium
snapshots from market_history.db (app.histcap.store). Never fabricates a
missing data point and never guesses an outcome from incomplete data --
every result is either a real TARGET_HIT/SL_HIT/OPEN/TIMED_OUT_NO_HIT
derived from real snapshots, or an honest INSUFFICIENT_DATA/NO_LEVELS_
RECORDED/NO_LEG_RECORDED when the data needed to answer isn't there.

Read-only, review-only: this module never writes back into
shadow_decisions and is never called from any live/paper decision path --
it only looks at what ALREADY happened after a signal that has already
been logged, purely for reporting.

Entry/stop_loss/target_1 are OPTION PREMIUM levels (not the underlying
spot) -- exactly what app.engines.scalp_strategy.decide_from_context()
planned at signal time and app.ai.shadow persisted verbatim (see
app/db.py's shadow_decisions migration). Both BUY_CE and BUY_PE are long-
premium plans (target_1 > entry > stop_loss in premium terms), so the
same TARGET_HIT/SL_HIT comparison applies to either side.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..histcap.store import HistStore, hist_store

_DECISION_TO_OPTION_TYPE = {"BUY_CE": "CE", "BUY_PE": "PE"}


def _parse_dt(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def check_outcome(row: dict, *, store: HistStore | None = None,
                  now: datetime | None = None) -> dict:
    """`row`: one shadow_decisions record (a dict, e.g. from
    app.db.list_shadow_decisions). Returns a status plus whatever real
    evidence backs it -- never a fabricated price or guessed outcome."""
    store = store or hist_store()
    now = now or datetime.now(timezone.utc)

    entry, stop_loss, target_1 = row.get("entry"), row.get("stop_loss"), row.get("target_1")
    if entry is None or stop_loss is None or target_1 is None:
        return {"status": "NO_LEVELS_RECORDED",
               "reason": "this decision predates forward-outcome tracking, or was NO_TRADE"}

    option_type = _DECISION_TO_OPTION_TYPE.get(row.get("deterministic_decision"))
    strike, symbol = row.get("strike"), row.get("symbol")
    if option_type is None or strike is None or not symbol:
        return {"status": "NO_LEG_RECORDED",
               "reason": "no resolved option leg (strike/CE-PE) on this decision"}

    signal_ts = row.get("ts") or ""
    # `since=signal_ts` pushes the lower bound into the query itself -- get_quotes'
    # default ASC+LIMIT ordering otherwise returns a long-lived strike/expiry
    # combo's OLDEST rows, silently missing everything from today (see the
    # `since` param's own docstring in app.histcap.store for the real
    # incident this fixes).
    forward = store.get_quotes(symbol, kind="OPTION", strike=strike, option_type=option_type,
                               expiry=row.get("expiry") or None, since=signal_ts)
    if not forward:
        return {"status": "INSUFFICIENT_DATA", "snapshot_count": 0,
               "entry": entry, "stop_loss": stop_loss, "target_1": target_1}

    mfe, mae = 0.0, 0.0
    hit_status, hit_ts = None, None
    for q in forward:
        ltp = q.get("ltp")
        if ltp is None:
            continue
        mfe = max(mfe, ltp - entry)
        mae = max(mae, entry - ltp)
        if hit_status is None:      # first touch wins -- later snapshots can't un-hit it
            if ltp >= target_1:
                hit_status, hit_ts = "TARGET_HIT", (q.get("exch_ts") or q.get("received_ts"))
            elif ltp <= stop_loss:
                hit_status, hit_ts = "SL_HIT", (q.get("exch_ts") or q.get("received_ts"))

    if hit_status is None:
        max_hold_sec = row.get("max_hold_sec")
        sig_dt = _parse_dt(signal_ts)
        if max_hold_sec and sig_dt and (now - sig_dt).total_seconds() > max_hold_sec:
            hit_status = "TIMED_OUT_NO_HIT"
        else:
            hit_status = "OPEN"

    last = forward[-1]
    return {
        "status": hit_status, "hit_ts": hit_ts,
        "entry": entry, "stop_loss": stop_loss, "target_1": target_1,
        "mfe": round(mfe, 2), "mae": round(mae, 2),
        "snapshot_count": len(forward),
        "last_ltp": last.get("ltp"),
        "last_ts": last.get("exch_ts") or last.get("received_ts"),
    }


def outcomes_report(*, symbol: str | None = None, limit: int = 200,
                    store: HistStore | None = None, now: datetime | None = None) -> list[dict]:
    """Every shadow_decisions row that actually planned a leg (entry/
    stop_loss/target_1 all present -- i.e. skips NO_TRADE rows and any row
    that predates this tracking), each paired with its real outcome so
    far. Most-recent first (matches db.list_shadow_decisions' own order)."""
    from .. import db
    out = []
    for row in db.list_shadow_decisions(symbol=symbol, limit=limit):
        if row.get("entry") is None:
            continue
        result = check_outcome(row, store=store, now=now)
        out.append({
            "symbol": row.get("symbol"), "ts": row.get("ts"), "signal_id": row.get("signal_id"),
            "deterministic_decision": row.get("deterministic_decision"),
            "fused_final_state": row.get("fused_final_state"),
            "telegram_status": row.get("telegram_status"),
            **result,
        })
    return out
