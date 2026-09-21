"""
Resting order-book depth snapshot -- an INFORMATION/VERIFICATION feature for
the AI shadow layer (spec section 4), never a gate on the deterministic
engine and never fed into scalp_strategy.py's own score.

HONESTY NOTE (matches this repo's own prior finding, orderflow-l2-imbalance-
gate research): tot_buy_qty/tot_sell_qty here are RESTING order-book
aggregates from Angel's SnapQuote (mode-3) best-5 depth, captured by
app.l2capture onto the front-month FUTURE of the underlying (NSE cash
indices have no order book of their own -- same reason
app.market_hub borrows VWAP from the future). This is NOT trade-level
aggressor/tape data -- there is no tick-by-tick buyer-vs-seller-initiated
flow available anywhere in this codebase. A passive-book imbalance built
from exactly this kind of data was already tested as a live GATE earlier
this session and REJECTED at feasibility (MAE-neutral, no edge). It is
included here only as an honestly-labeled informational field for Groq's
verification context, per spec section 4/5's own "label proxy metrics
honestly" instruction -- never call it "orderflow delta", never treat a
value here as a proven trading signal.

Reads from the SEPARATE l2_capture.db (app.l2capture.store) -- read-only,
never touches market_history.db or chanakya.db.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from ..l2capture.store import _DEFAULT_PATH as L2_DB_PATH

_EPS = 1e-9


def _parse_ts(ts: str | None) -> int | None:
    """decision-time cutoff (ISO string or epoch ms) -> epoch ms, or None."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return int(ts if ts > 1e12 else ts * 1000)
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def latest_snapshot(token: str, *, at_or_before: str | int | float | None = None) -> dict | None:
    """Most recent snapquote_ticks row for `token` with exch_ts_ms <=
    the given cutoff (no look-ahead: a decision at time T must never see a
    depth snapshot captured after T). None if the DB/table/row doesn't
    exist -- never fabricated, never a fresh network read."""
    try:
        conn = sqlite3.connect(f"file:{L2_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        return None
    try:
        cutoff = _parse_ts(at_or_before)
        if cutoff is not None:
            row = conn.execute(
                "SELECT * FROM snapquote_ticks WHERE token=? AND exch_ts_ms<=? "
                "ORDER BY exch_ts_ms DESC LIMIT 1", (str(token), cutoff)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM snapquote_ticks WHERE token=? "
                "ORDER BY exch_ts_ms DESC LIMIT 1", (str(token),)).fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def _liquidity_state(bid_qty, ask_qty) -> str:
    total = (bid_qty or 0) + (ask_qty or 0)
    if total <= 0:
        return "UNKNOWN"
    if total < 50:
        return "THIN"
    if total < 500:
        return "NORMAL"
    return "DEEP"


def snapshot_for_symbol(symbol: str, *, at_or_before: str | int | float | None = None) -> dict:
    """Resolve `symbol` to the same front-month FUTURE token app.l2capture
    itself subscribes to (NSE/BSE index -> resolve_index_future, MCX
    commodity -> resolve_mcx_future), then return its orderflow_snapshot().
    Read-only instrument-master lookup, no order, no network beyond that."""
    from .. import instruments
    for resolver in (instruments.resolve_index_future, instruments.resolve_mcx_future):
        try:
            row = resolver(symbol)
        except Exception:
            continue
        if row.get("status") == "OK" and row.get("symboltoken"):
            return orderflow_snapshot(str(row["symboltoken"]), at_or_before=at_or_before)
    return {"available": False, "reason": f"could not resolve a FUTURE token for {symbol}",
           "bid_volume": None, "ask_volume": None, "depth_imbalance": None,
           "spread": None, "spread_pct": None, "liquidity_state": "UNKNOWN",
           "orderflow_state": "UNKNOWN", "as_of": None,
           "note": "resting order-book aggregate, not trade-level aggressor flow"}


def orderflow_snapshot(token: str, *, at_or_before: str | int | float | None = None) -> dict:
    """Returns an honestly-labeled snapshot dict, never raises. Every field
    is "not available" (never a fabricated 0) if the underlying data is
    missing -- matches spec section 10's "not available, not zero" rule."""
    row = latest_snapshot(token, at_or_before=at_or_before)
    if not row:
        return {"available": False, "reason": "no L2 capture row for this token/time",
                "bid_volume": None, "ask_volume": None, "depth_imbalance": None,
                "spread": None, "spread_pct": None, "liquidity_state": "UNKNOWN",
                "orderflow_state": "UNKNOWN", "as_of": None,
                "note": "resting order-book aggregate, not trade-level aggressor flow"}

    bid_vol, ask_vol = row.get("tot_buy_qty"), row.get("tot_sell_qty")
    bid, ask = row.get("bid"), row.get("ask")
    imbalance = None
    if bid_vol is not None and ask_vol is not None:
        total = (bid_vol or 0) + (ask_vol or 0)
        imbalance = round((bid_vol - ask_vol) / total, 4) if total > _EPS else 0.0

    spread = spread_pct = None
    if bid is not None and ask is not None and ask > 0:
        spread = round(ask - bid, 4)
        mid = (ask + bid) / 2.0
        spread_pct = round(spread / mid, 5) if mid > _EPS else None

    state = "UNKNOWN"
    if imbalance is not None:
        state = "BULLISH" if imbalance > 0.15 else ("BEARISH" if imbalance < -0.15 else "BALANCED")

    return {
        "available": True,
        "bid_volume": bid_vol, "ask_volume": ask_vol, "depth_imbalance": imbalance,
        "spread": spread, "spread_pct": spread_pct,
        "liquidity_state": _liquidity_state(bid_vol, ask_vol),
        "orderflow_state": state,
        "as_of": row.get("exch_ts") or row.get("received_ts"),
        "note": "resting order-book aggregate (SnapQuote best-5, front-month FUTURE), "
               "not trade-level aggressor flow -- a passive-book imbalance gate was "
               "already tested and rejected at feasibility earlier this session; "
               "this is an informational field only, not a signal.",
    }
