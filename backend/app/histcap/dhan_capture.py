"""Dhan chain -> canonical capture rows, PARALLEL to AngelOne's own capture
(normalize.py's norm_quote/norm_greeks build these same row shapes from
AngelOne's raw payloads). MCP 3.3 priority #5: Dhan is now PRIMARY for
on-demand reads (app.optionchain.resolve); this wires the SAME already-
verified dhan_chain.fetch() into the CONTINUOUS background capture too,
alongside -- never replacing -- AngelOne's.

Row-level append-only safety is automatic, not something this module has
to enforce: quote_snapshots' UNIQUE(token, snap_key) and option_greeks'
UNIQUE(underlying, expiry, strike, option_type, snap_key) key on `token`/
the strike identity, and Dhan's real security_id (when present) or this
module's synthetic fallback token are always disjoint from AngelOne's own
symboltoken namespace -- the two providers' rows for "the same" real
contract can never collide or overwrite each other, they're just two
permanently distinct rows tagged by `source`.

Dhan's real capability boundary (verified live 2026-09-30, not assumed):
/v2/optionchain has no historical/timestamp parameter -- current-state-
only. Its historical/intraday chart endpoint returns OHLCV for options
with NO OI and NO Greeks. There is no way to backfill Dhan option history;
this module only ever captures Dhan's CURRENT live chain, each time it's
called -- a real historical record only from whenever this is first wired
into the periodic worker, going forward, never retroactively.
"""
from __future__ import annotations

from ..optionchain.sources import dhan_chain as _dhan
from .normalize import ist_date, now_utc_iso, snap_key


def capture_dhan_chain(sym: str, expiry: str, *, received_ts: str | None = None) -> dict | None:
    """Fetch Dhan's CURRENT live chain for sym/expiry and return
    {"quote_rows": [...], "greek_rows": [...], "spot": float|None,
    "exch_ts": str} in the exact shapes HistStore.write_quotes/write_greeks
    expect -- or None if Dhan has nothing right now (expired OAuth session,
    network failure, unsupported symbol/expiry). Never raises; the caller
    treats None exactly like any other soft per-symbol capture miss, same
    discipline as every AngelOne capture step in worker.py."""
    recv = received_ts or now_utc_iso()
    try:
        chain = _dhan.fetch(sym, expiry)
    except Exception:
        return None
    if chain is None or not chain.rows:
        return None

    exch_ts = chain.ts or recv
    key = snap_key(exch_ts, recv)
    sd = ist_date(exch_ts)
    quote_rows: list[dict] = []
    greek_rows: list[dict] = []

    def _token(leg, strike, ot) -> str:
        # Real Dhan security_id when the source gave one (it does for a
        # live /v2/optionchain leg -- see sources/dhan_chain.py); a
        # deterministic synthetic identifier otherwise, still guaranteed
        # disjoint from AngelOne's own (purely numeric) token namespace.
        return leg.security_id or f"DHAN-{sym}-{expiry}-{strike}-{ot}"

    for row in chain.rows:
        for ot, leg in (("CE", row.ce), ("PE", row.pe)):
            if leg is None:
                continue
            tok = _token(leg, row.strike, ot)
            quote_rows.append({
                "received_ts": recv, "server_ts": None, "exch_ts": exch_ts, "snap_key": key,
                "instrument_key": f"DHAN:{tok}", "symbol": sym, "kind": "OPTION",
                "exchange": "NFO" if sym.upper() not in ("SENSEX", "BANKEX") else "BFO",
                "token": tok, "expiry": expiry, "strike": row.strike, "option_type": ot,
                "session_date_ist": sd, "ltp": leg.ltp,
                "open": None, "high": None, "low": None, "close": None,
                "volume": leg.volume, "oi": leg.oi, "oi_change": leg.oi_change,
                "avg_price": None, "last_trade_qty": None,
                "bid": leg.bid, "ask": leg.ask, "bid_qty": leg.bid_qty, "ask_qty": leg.ask_qty,
                "tot_buy_qty": None, "tot_sell_qty": None, "depth_json": None,
                "net_change": None, "pct_change": None,
                "lower_circuit": None, "upper_circuit": None,
                "week52_high": None, "week52_low": None, "basis": None,
                "quote_status": "OK", "source": "DHAN_CHAIN",
            })
            if leg.delta is not None or leg.iv is not None:
                greek_rows.append({
                    "received_ts": recv, "server_ts": None, "snap_key": key,
                    "underlying": sym.upper(), "expiry": str(expiry).upper(),
                    "strike": row.strike, "option_type": ot, "session_date_ist": sd,
                    "delta": leg.delta, "gamma": leg.gamma, "theta": leg.theta, "vega": leg.vega,
                    "iv": leg.iv, "iv_pct": (leg.iv * 100.0) if leg.iv is not None else None,
                    "trade_volume": leg.volume,
                    "broker_status": "OK", "source": "DHAN_OPTION_GREEK",
                })

    return {"quote_rows": quote_rows, "greek_rows": greek_rows, "spot": chain.spot, "exch_ts": exch_ts}
