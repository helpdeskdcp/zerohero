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

from datetime import datetime, timedelta, timezone

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
    far. Most-recent first (matches db.list_shadow_decisions' own order).

    NOTE: this is PER-TICK, not per-opportunity -- the shadow-decision loop
    re-evaluates every ~30s, so one continuing setup produces many rows
    here. Use setups_report() for an honest "how many real opportunities"
    count; this function stays as-is (unchanged contract, existing callers/
    tests) for anyone who genuinely wants the raw per-tick detail."""
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


# ---------------------------------------------------------------- setup identity
# Real incident (2026-09-21): NATURALGAS's engine re-evaluated the IDENTICAL
# BUY_PE 275-strike leg every ~30s for 8 minutes during one continuous price
# move (4.0 -> 5.9). A naive per-tick or per-(entry,stop_loss,target_1)-tuple
# count reported this as 7-16 "distinct setups" / "wins" -- each tick's
# entry/target drifts slightly as price moves, so even tuple-grouping
# overstates independence. All 16 raw ticks were also VETOED by AI/fusion
# every single time (fused_final_state alternated NO_TRADE/WEAK_SELL, never
# reached a tradeable state) and NO real paper trade ever opened for this
# episode (cross-checked against scalp_signals -- see real_trade below) --
# this was a shadow-only hypothetical the live engine correctly never acted
# on, which the per-tick view alone does not make obvious.

_MAX_SETUP_GAP_SEC = 120.0   # 4x the default 30s decide_every_sec, generous margin


def _leg_key(row: dict):
    """The most concrete available identity for "the same option contract":
    the resolved token when present, else strike+expiry (still specific
    enough -- two different tokens essentially never share both)."""
    tok = row.get("option_token")
    if tok:
        return ("token", tok)
    strike, expiry = row.get("strike"), row.get("expiry")
    if strike is not None and expiry:
        return ("strike", strike, expiry)
    return None


def group_into_setups(rows: list[dict], *, max_gap_sec: float = _MAX_SETUP_GAP_SEC) -> list[dict]:
    """`rows`: shadow_decisions rows for ONE symbol, chronological (oldest
    first), INCLUDING rows with no recorded entry -- a real deterministic
    NO_TRADE tick (entry is None) is what correctly BREAKS a chain, so it
    must be present in the input, not pre-filtered out.

    A contiguous run of ticks sharing the same symbol + deterministic
    direction (BUY_CE/BUY_PE) + resolved option leg, with no real NO_TRADE/
    opposite-direction/different-leg tick and no gap wider than
    `max_gap_sec`, is ONE setup. `fused_final_state` oscillating between
    NO_TRADE/WEAK_SELL/SELL on the SAME leg does NOT break the chain --
    that is the AI's confidence wavering on the same opportunity, not a new
    one; the deterministic engine's own decision is the identity signal.

    Returns one dict per setup: symbol, deterministic_decision, leg_key,
    first_seen, last_seen, evaluation_count, rows (the raw ticks, oldest
    first)."""
    setups: list[dict] = []
    cur: dict | None = None
    for row in rows:
        decision = row.get("deterministic_decision")
        has_leg = decision in _DECISION_TO_OPTION_TYPE and row.get("entry") is not None
        leg = _leg_key(row) if has_leg else None
        if leg is None:
            cur = None       # NO_TRADE / no-leg tick always breaks any run
            continue
        ts = row.get("ts")
        dt = _parse_dt(ts)
        same_run = (cur is not None and cur["symbol"] == row.get("symbol")
                   and cur["deterministic_decision"] == decision and cur["leg_key"] == leg
                   and not (dt and cur["_last_dt"] and (dt - cur["_last_dt"]).total_seconds() > max_gap_sec))
        if not same_run:
            cur = {"symbol": row.get("symbol"), "deterministic_decision": decision,
                  "leg_key": leg, "first_seen": ts, "last_seen": ts,
                  "evaluation_count": 0, "rows": [], "_last_dt": dt}
            setups.append(cur)
        cur["last_seen"] = ts
        cur["evaluation_count"] += 1
        cur["rows"].append(row)
        cur["_last_dt"] = dt
    for s in setups:
        s.pop("_last_dt", None)
    return setups


def _real_trade_match(symbol: str, decision: str, first_seen: str, last_seen: str, *,
                      list_scalp_signals=None) -> dict | None:
    """Cross-checks the REAL paper-trading table (scalp_signals, gated by
    app.autoscalp.runner's own open_keys/safeguards.check_entry -- a
    genuinely separate, already-existing "don't duplicate-enter" mechanism,
    unrelated to and unaffected by the shadow-decision over-counting this
    module fixes) for a real trade opened near this setup's window. None
    if no real trade matches -- most shadow setups never do, since fusion/
    AI vetoes most of them before they'd ever reach a real entry."""
    from .. import db
    list_scalp_signals = list_scalp_signals or db.list_scalp_signals
    first_dt, last_dt = _parse_dt(first_seen), _parse_dt(last_seen)
    if not first_dt or not last_dt:
        return None
    window_start = first_dt - timedelta(seconds=90)
    window_end = last_dt + timedelta(seconds=1800)
    for r in list_scalp_signals(symbol=symbol, limit=200):
        if r.get("decision") != decision:
            continue
        created = _parse_dt(r.get("created_ts"))
        if not created:
            continue
        # generous window: a real entry could fire slightly before the first
        # shadow tick recorded it (different code paths, same underlying
        # signal) through well after the last tick (still-open position).
        if window_start <= created <= window_end:
            return {"status": r.get("status"), "outcome": r.get("outcome"),
                   "entry": r.get("entry"), "exit_reason": r.get("exit_reason"),
                   "points": r.get("points"), "created_ts": r.get("created_ts")}
    return None


def setups_report(*, symbol: str | None = None, limit: int = 500,
                  store: HistStore | None = None, now: datetime | None = None) -> list[dict]:
    """The honest "how many real opportunities" view: raw shadow_decisions
    rows grouped into setups (group_into_setups), each resolved from its
    FIRST tick (the moment the opportunity was actually detected -- not a
    later tick's drifted entry/target), plus whether a REAL paper trade
    ever matched it. Most-recent setup first."""
    from .. import db
    rows = db.list_shadow_decisions(symbol=symbol, limit=limit)
    by_symbol: dict[str, list[dict]] = {}
    for r in rows:
        by_symbol.setdefault(r.get("symbol"), []).append(r)

    out = []
    for sym, sym_rows in by_symbol.items():
        sym_rows.sort(key=lambda r: r.get("ts") or "")     # chronological
        for setup in group_into_setups(sym_rows):
            first = setup["rows"][0]
            result = check_outcome(first, store=store, now=now)
            real_trade = _real_trade_match(setup["symbol"], setup["deterministic_decision"],
                                           setup["first_seen"], setup["last_seen"])
            out.append({
                "symbol": setup["symbol"], "deterministic_decision": setup["deterministic_decision"],
                "first_seen": setup["first_seen"], "last_seen": setup["last_seen"],
                "evaluation_count": setup["evaluation_count"],
                "real_trade": real_trade,
                **result,
            })
    out.sort(key=lambda o: o["first_seen"] or "", reverse=True)
    return out
