"""Dhan chain -> canonical capture rows, PARALLEL to AngelOne's own capture
(normalize.py's norm_quote/norm_greeks build these same row shapes from
AngelOne's raw payloads). MCP 3.3 priority #5: Dhan is now PRIMARY for
on-demand reads (app.optionchain.resolve); this wires the SAME already-
verified dhan_chain.fetch() into the CONTINUOUS background capture too,
alongside -- never replacing -- AngelOne's.

Row-level append-only safety is enforced by source-aware UNIQUE keys in
histcap.schema. Real numeric security IDs may overlap between brokers;
keep them unchanged and distinguish the rows by source (and exchange for
quotes). Legacy tables are migrated without rewriting their stored IDs.

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


def _exchange(sym: str) -> str:
    u = sym.upper()
    if u in _dhan._MCX_SYMBOLS:
        return "MCX"
    return "BFO" if u in ("SENSEX", "BANKEX") else "NFO"


def capture_dhan_chain(sym: str, expiry: str, *, received_ts: str | None = None) -> dict | None:
    """Fetch Dhan's CURRENT live chain for sym/expiry and return
    {"quote_rows": [...], "greek_rows": [...], "spot": float|None,
    "exch_ts": str, "raw": dict|None} in the exact shapes HistStore.
    write_quotes/write_greeks expect -- or None if Dhan has nothing right
    now (expired OAuth session, network failure, unsupported symbol/
    expiry). Never raises; the caller treats None exactly like any other
    soft per-symbol capture miss, same discipline as every AngelOne
    capture step in worker.py.

    "raw" is the exact response body from ONE network call
    (dhan_chain.fetch_raw(), then parsed locally -- never a second HTTP
    request just to get the raw copy) -- market data only, never contains
    the request's auth headers (see fetch_raw()'s own docstring for why
    that's a structural guarantee, not a filter). None when Dhan returned
    nothing (nothing to archive)."""
    recv = received_ts or now_utc_iso()
    try:
        raw = _dhan.fetch_raw(sym, expiry)
        chain = _dhan.parse(raw, sym, expiry) if raw is not None else None
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
        # deterministic synthetic identifier otherwise. Source-aware storage
        # keys provide isolation even when the real numeric IDs overlap.
        return leg.security_id or f"DHAN-{sym}-{expiry}-{strike}-{ot}"

    for row in chain.rows:
        for ot, leg in (("CE", row.ce), ("PE", row.pe)):
            if leg is None:
                continue
            tok = _token(leg, row.strike, ot)
            quote_rows.append({
                "received_ts": recv, "server_ts": None, "exch_ts": exch_ts, "snap_key": key,
                "instrument_key": f"DHAN:{tok}", "symbol": sym, "kind": "OPTION",
                "exchange": _exchange(sym),
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

    return {"quote_rows": quote_rows, "greek_rows": greek_rows, "spot": chain.spot,
           "exch_ts": exch_ts, "raw": raw}
