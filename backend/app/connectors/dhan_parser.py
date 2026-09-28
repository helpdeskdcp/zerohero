"""
Dhan Live Market Feed -- binary packet parsing + aggressor-side classification.

Protocol reference: https://dhanhq.co/docs/v2/live-market-feed/ and
https://dhanhq.co/docs/v2/annexure/ (verified live 2026-09-28). All
multi-byte fields are little-endian. Every response packet starts with an
8-byte header:
  offset 0  u8   response code
  offset 1  i16  message length (payload bytes, excludes this 8-byte header)
  offset 3  u8   exchange segment code
  offset 4  i32  security id

We only ever SUBSCRIBE in Full mode (RequestCode 21, see dhan_ws.py) because
it is the only mode that carries best bid/ask (5-level market depth) --
without a live best bid/ask there is no quote rule to classify a trade
against. Ticker(2)/Quote(4)/PrevClose(6)/OI(5) packets are recognized (by
total size, so a mixed stream never desyncs the frame splitter) but not
decoded, since nothing here subscribes to those modes.

IMPORTANT -- classification is INFERRED, not broker-confirmed. Dhan (like
every broker feed already checked in this codebase: AngelOne, Upstox) never
sends a genuine buyer/seller-initiated flag on a trade. classify_tick()
applies the standard quote rule (trade price vs best bid/ask) with a
tick-rule fallback (vs the previous trade price) -- a well-established
approximation technique, but an approximation. Every classified tick
carries classification_method so nothing downstream mistakes this for
ground truth (see the orderflow-l2-imbalance-gate research memory: genuine
aggressor-tagged trade data does not exist at any broker checked so far).
"""
from __future__ import annotations

import struct

RESPONSE_HEADER_SIZE = 8

# response code -> total packet size in bytes (header included). Anything not
# in this table is unknown, at which point parse_frame stops rather than
# guess a size and desync the rest of the frame.
_PACKET_SIZE = {
    2: 16,    # Ticker
    4: 50,    # Quote
    5: 12,    # OI (associated w/ Quote mode)
    6: 16,    # Prev Close (associated w/ Ticker mode)
    8: 162,   # Full
    50: 10,   # Disconnect
}

_DEPTH_LEVEL_SIZE = 20
_DEPTH_LEVELS = 5
_DEPTH_OFFSET = 62  # inside a Full(8) packet, immediately after the day OHLC fields


def parse_frame(payload: bytes) -> list[dict]:
    """Split one WS binary frame into packet dicts. Tolerates concatenated
    packets (Dhan may batch several updates per frame); stops cleanly on an
    unrecognized response code or a truncated trailing packet rather than
    guessing a size."""
    out = []
    n = len(payload)
    i = 0
    while i + RESPONSE_HEADER_SIZE <= n:
        code = payload[i]
        size = _PACKET_SIZE.get(code)
        if size is None or i + size > n:
            break
        seg = payload[i + 3]
        security_id = struct.unpack_from("<i", payload, i + 4)[0]
        if code == 8:
            out.append(_parse_full(payload, i, seg, security_id))
        elif code == 50:
            reason = struct.unpack_from("<h", payload, i + 8)[0]
            out.append({"type": "DISCONNECT", "security_id": security_id, "reason_code": reason})
        else:
            out.append({"type": "IGNORED", "response_code": code, "security_id": security_id})
        i += size
    return out


def _parse_full(payload: bytes, off: int, exchange_segment: int, security_id: int) -> dict:
    ltp, = struct.unpack_from("<f", payload, off + 8)
    ltq, = struct.unpack_from("<h", payload, off + 12)
    ltt, = struct.unpack_from("<i", payload, off + 14)
    atp, = struct.unpack_from("<f", payload, off + 18)
    volume, = struct.unpack_from("<i", payload, off + 22)
    total_sell_qty, = struct.unpack_from("<i", payload, off + 26)
    total_buy_qty, = struct.unpack_from("<i", payload, off + 30)
    oi, = struct.unpack_from("<i", payload, off + 34)
    day_open, = struct.unpack_from("<f", payload, off + 46)
    day_close, = struct.unpack_from("<f", payload, off + 50)
    day_high, = struct.unpack_from("<f", payload, off + 54)
    day_low, = struct.unpack_from("<f", payload, off + 58)
    depth = []
    for lvl in range(_DEPTH_LEVELS):
        base = off + _DEPTH_OFFSET + lvl * _DEPTH_LEVEL_SIZE
        bid_qty, = struct.unpack_from("<i", payload, base)
        ask_qty, = struct.unpack_from("<i", payload, base + 4)
        bid_orders, = struct.unpack_from("<h", payload, base + 8)
        ask_orders, = struct.unpack_from("<h", payload, base + 10)
        bid_price, = struct.unpack_from("<f", payload, base + 12)
        ask_price, = struct.unpack_from("<f", payload, base + 16)
        depth.append({"bid_price": bid_price, "bid_qty": bid_qty, "bid_orders": bid_orders,
                      "ask_price": ask_price, "ask_qty": ask_qty, "ask_orders": ask_orders})
    return {
        "type": "FULL", "exchange_segment": exchange_segment, "security_id": security_id,
        "ltp": ltp, "ltq": ltq, "ltt": ltt, "atp": atp, "volume": volume,
        "total_sell_qty": total_sell_qty, "total_buy_qty": total_buy_qty, "oi": oi,
        "day_open": day_open, "day_close": day_close, "day_high": day_high, "day_low": day_low,
        "depth": depth,
    }


def classify_tick(packet: dict, prev_state: dict | None) -> tuple[dict | None, dict]:
    """Given a decoded FULL packet and this security_id's previous state
    ({"ltt", "ltp"}), return (classified_tick_or_None, new_state).

    Returns None for the tick when ltt (last trade time) is unchanged from
    the previous packet -- Dhan resends the full snapshot on every depth
    update, not just on a new trade, so an unchanged last-trade-time means
    this frame carries no NEW trade to classify. Skipping it here avoids
    double-counting the same trade into delta/CVD on every depth refresh."""
    new_state = {"ltt": packet["ltt"], "ltp": packet["ltp"]}
    if prev_state is not None and packet["ltt"] == prev_state.get("ltt"):
        return None, new_state

    ltp = packet["ltp"]
    depth = packet.get("depth") or []
    best_bid = depth[0]["bid_price"] if depth else 0.0
    best_ask = depth[0]["ask_price"] if depth else 0.0

    side = method = None
    if best_bid > 0 and best_ask > 0:
        if ltp >= best_ask:
            side, method = "BUY_AGGRESSOR", "QUOTE_RULE"
        elif ltp <= best_bid:
            side, method = "SELL_AGGRESSOR", "QUOTE_RULE"
    if side is None:
        prev_ltp = prev_state.get("ltp") if prev_state else None
        if prev_ltp is not None:
            if ltp > prev_ltp:
                side, method = "BUY_AGGRESSOR", "TICK_RULE"
            elif ltp < prev_ltp:
                side, method = "SELL_AGGRESSOR", "TICK_RULE"
            else:
                side, method = "UNKNOWN", "TICK_RULE_FLAT"
        else:
            side, method = "UNKNOWN", "INSUFFICIENT_DATA"

    tick = {
        "security_id": packet["security_id"], "ltp": ltp, "ltq": packet["ltq"],
        "ltt": packet["ltt"], "side": side, "classification_method": method,
        "best_bid": best_bid, "best_ask": best_ask,
    }
    return tick, new_state
