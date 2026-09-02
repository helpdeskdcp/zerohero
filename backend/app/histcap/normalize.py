"""
Broker payload -> canonical capture rows.

Real values only. A field AngelOne did not send becomes ``None`` — never
estimated, never back-filled. Greeks are delegated to
``broker.angelone.greeks.normalize_greek_row`` (optionGreek is the sole source).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from broker.angelone.greeks import normalize_greek_row, index_greek_rows, match_greek  # noqa: E402

_IST = timezone(timedelta(hours=5, minutes=30))


def _f(x):
    if x is None:
        return None
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x) if x == x else None
    s = str(x).strip()
    if not s or s.upper() in ("NA", "N/A", "-", "NULL", "NONE"):
        return None
    try:
        v = float(s)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def to_utc_iso(v) -> str | None:
    """AngelOne timestamps: epoch ms/sec, or 'YYYY-MM-DDTHH:MM:SS+05:30', or
    'DD-Mon-YYYY HH:MM:SS'. -> UTC ISO-8601 'Z'. None if unparseable/absent."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        try:
            ts = float(v)
            if ts > 1e12:            # ms
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            return None
    s = str(v).strip()
    for fmt in (None, "%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d%b%Y %H:%M:%S"):
        try:
            dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_IST)      # AngelOne local times are IST
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    return None


def ist_date(utc_iso: str | None) -> str:
    """IST calendar date for a UTC ISO string (fallback: now)."""
    try:
        dt = datetime.fromisoformat((utc_iso or "").replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc)
    return dt.astimezone(_IST).date().isoformat()


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def snap_key(primary_ts: str | None, received_ts: str) -> str:
    """Dedup key: the exchange/server ts if present, else received_ts truncated
    to the second. Identical polls of an unchanged quote collapse."""
    return (primary_ts or received_ts)[:19]


# --------------------------------------------------------------- quote (FULL)
def norm_quote(raw: dict, meta: dict, received_ts: str, *, spot_ltp: float | None = None) -> dict:
    """raw = AngelOne FULL-quote row (client.get_quote merges data[].fetched[0]).
    meta = {instrument_key, symbol, kind, exchange, token, expiry, strike, option_type}
    """
    q = raw or {}
    depth = q.get("depth") if isinstance(q.get("depth"), dict) else None
    buy = (depth or {}).get("buy") or []
    sell = (depth or {}).get("sell") or []
    bid = _f(buy[0].get("price")) if buy and isinstance(buy[0], dict) else None
    ask = _f(sell[0].get("price")) if sell and isinstance(sell[0], dict) else None
    bid_qty = _f(buy[0].get("quantity")) if buy and isinstance(buy[0], dict) else None
    ask_qty = _f(sell[0].get("quantity")) if sell and isinstance(sell[0], dict) else None

    exch_ts = to_utc_iso(q.get("exchangeTimestamp") or q.get("exchange_timestamp")
                         or q.get("exchTradeTime") or q.get("exchFeedTime"))
    server_ts = to_utc_iso(q.get("server_timestamp") or q.get("serverTime"))
    ltp = _f(q.get("ltp") or q.get("lastTradedPrice"))
    basis = None
    if meta.get("kind") == "FUTURE" and ltp is not None and spot_ltp is not None:
        basis = round(ltp - float(spot_ltp), 4)      # DERIVED — arithmetic on two real LTPs

    return {
        "received_ts": received_ts, "server_ts": server_ts, "exch_ts": exch_ts,
        "snap_key": snap_key(exch_ts or server_ts, received_ts),
        "instrument_key": meta["instrument_key"], "symbol": meta["symbol"],
        "kind": meta["kind"], "exchange": meta["exchange"], "token": str(meta["token"]),
        "expiry": meta.get("expiry"), "strike": meta.get("strike"),
        "option_type": meta.get("option_type"),
        "session_date_ist": ist_date(exch_ts or received_ts),
        "ltp": ltp,
        "open": _f(q.get("open") or q.get("openPrice")),
        "high": _f(q.get("high") or q.get("highPrice")),
        "low": _f(q.get("low") or q.get("lowPrice")),
        "close": _f(q.get("close") or q.get("closePrice")),
        "volume": _f(q.get("tradeVolume") or q.get("volume")),
        "oi": _f(q.get("opnInterest") or q.get("openInterest") or q.get("oi")),
        "oi_change": _f(q.get("changeinOpenInterest") or q.get("changeInOpenInterest")),
        "avg_price": _f(q.get("avgPrice") or q.get("averagePrice")),
        "last_trade_qty": _f(q.get("lastTradeQty") or q.get("lastTradedQty")),
        "bid": bid, "ask": ask, "bid_qty": bid_qty, "ask_qty": ask_qty,
        "tot_buy_qty": _f(q.get("totBuyQuan") or q.get("totalBuyQuantity")),
        "tot_sell_qty": _f(q.get("totSellQuan") or q.get("totalSellQuantity")),
        "depth_json": depth,
        "net_change": _f(q.get("netChange")),
        "pct_change": _f(q.get("percentChange")),
        "lower_circuit": _f(q.get("lowerCircuit") or q.get("lowerCircuitLimit")),
        "upper_circuit": _f(q.get("upperCircuit") or q.get("upperCircuitLimit")),
        "week52_high": _f(q.get("52WeekHigh") or q.get("weekHigh")),
        "week52_low": _f(q.get("52WeekLow") or q.get("weekLow")),
        "basis": basis,
        "quote_status": q.get("status") or "DATA_UNAVAILABLE",
        "source": "ANGELONE_QUOTE_FULL",
    }


# --------------------------------------------------------------- candles
_TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60, "1d": 375}


def norm_candles(raw_candles: list, meta: dict, tf: str, received_ts: str, *,
                 only_closed_before: datetime | None = None) -> list[dict]:
    """raw_candles = [{timestamp, open, high, low, close, volume}, ...] from
    client.get_candles(). Emits only bars whose close is in the past
    (`only_closed_before`, default now-UTC) so no partial/open bar is stored."""
    cutoff = only_closed_before or datetime.now(timezone.utc)
    tf_min = _TF_MIN.get(tf, 1)
    out = []
    for row in raw_candles or []:
        bar_start = to_utc_iso(row.get("timestamp") or row.get("t"))
        if not bar_start:
            continue
        try:
            bs_dt = datetime.fromisoformat(bar_start.replace("Z", "+00:00"))
        except ValueError:
            continue
        if bs_dt + timedelta(minutes=tf_min) > cutoff:
            continue                                     # bar not closed yet -> skip
        out.append({
            "received_ts": received_ts,
            "instrument_key": meta["instrument_key"], "symbol": meta["symbol"],
            "kind": meta["kind"], "exchange": meta["exchange"], "token": str(meta["token"]),
            "expiry": meta.get("expiry"), "strike": meta.get("strike"),
            "option_type": meta.get("option_type"),
            "tf": tf, "bar_start": bar_start, "session_date_ist": ist_date(bar_start),
            "o": _f(row.get("open") or row.get("o")),
            "h": _f(row.get("high") or row.get("h")),
            "l": _f(row.get("low") or row.get("l")),
            "c": _f(row.get("close") or row.get("c")),
            "v": _f(row.get("volume") if row.get("volume") is not None else row.get("v")),
            "oi": None, "oi_change": None,                # getCandleData does not return OI
            "source": "ANGELONE_CANDLES",
        })
    return out


# --------------------------------------------------------------- greeks
def norm_greeks(raw_rows: list, underlying: str, expiry: str, broker_status: str,
                received_ts: str, *, server_ts: str | None = None) -> list[dict]:
    key = snap_key(server_ts, received_ts)
    sd = ist_date(server_ts or received_ts)
    out = []
    for r in raw_rows or []:
        nr = normalize_greek_row(r) if "status" not in r else r
        if nr["strike"] is None or nr["option_type"] is None:
            continue
        out.append({
            "received_ts": received_ts, "server_ts": server_ts, "snap_key": key,
            "underlying": underlying.upper(), "expiry": str(expiry).upper(),
            "strike": nr["strike"], "option_type": nr["option_type"],
            "session_date_ist": sd,
            "delta": nr["delta"], "gamma": nr["gamma"], "theta": nr["theta"],
            "vega": nr["vega"], "iv": nr["iv"], "iv_pct": nr["iv_pct"],
            "trade_volume": nr["trade_volume"],
            "broker_status": broker_status, "source": "ANGELONE_OPTION_GREEK",
        })
    return out
