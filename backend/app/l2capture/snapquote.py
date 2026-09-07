"""
Angel One SmartWebSocketV2  --  SnapQuote (subscription mode 3) binary parser.

PURE + DETERMINISTIC. No I/O, no network, no globals. Turns a raw binary frame
into a list of snapquote dicts. This is a *market-data capture* helper for
research (see backend/ORDERFLOW_STAGE9_L2_RESEARCH.md section 6b, route (a)); it
is NOT a trading-signal path and emits no BUY/SELL/order.

Packet layout (mode 3, 379 bytes, little-endian) per Angel's "Binary Market
Data" spec:

    off  type       field
    0    u8         subscription mode (3)
    1    u8         exchange type
    2    char[25]   token (null-padded ascii)
    27   i64        sequence number
    35   i64        exchange timestamp (ms)
    43   i64        last traded price            (paise -> /100)
    51   i64        last traded quantity
    59   i64        average traded price         (/100)
    67   i64        volume traded today
    75   f64        total buy quantity
    83   f64        total sell quantity
    91   i64        open price of the day        (/100)
    99   i64        high price of the day        (/100)
    107  i64        low price of the day         (/100)
    115  i64        close price                  (/100)
    123  i64        last traded timestamp        (epoch seconds)
    131  i64        open interest
    139  f64        open interest change %        (nominal; often 0)
    147  best-five   10 x 20 bytes = 200 bytes
         per 20-byte entry:
           +0   i16   buy/sell flag (1 = buy / bid, 0 = sell / ask)
           +2   i64   quantity
           +10  i64   price                      (/100)
           +18  i16   number of orders
    347  i64        upper circuit limit          (/100)
    355  i64        lower circuit limit          (/100)
    363  i64        52 week high price           (/100)
    371  i64        52 week low price            (/100)
    379  end

Concatenated packets in one frame are tolerated; trailing bytes and packets of
other sizes (e.g. a stray 51-byte LTP packet) are skipped. Nothing is ever
fabricated -- a field the packet does not carry stays absent.
"""
from __future__ import annotations

import struct

SNAPQUOTE_MODE = 3
SNAPQUOTE_PACKET = 379
_BEST5_OFF = 147
_BEST5_ENTRY = 20
_PRICE_DIV = 100.0


def _i64(buf, off):
    return struct.unpack_from("<q", buf, off)[0]


def _i16(buf, off):
    return struct.unpack_from("<h", buf, off)[0]


def _f64(buf, off):
    return struct.unpack_from("<d", buf, off)[0]


def _best_five(buf, base):
    """Return {"buy": [...], "sell": [...]} -- each a list of
    {price, quantity, orders}. buy sorted best (highest) first, sell lowest
    first. Zero-price / zero-qty padding entries are dropped."""
    buy, sell = [], []
    for k in range(10):
        o = base + k * _BEST5_ENTRY
        flag = _i16(buf, o)
        qty = _i64(buf, o + 2)
        price = _i64(buf, o + 10) / _PRICE_DIV
        orders = _i16(buf, o + 18)
        if price <= 0 and qty <= 0:
            continue
        row = {"price": price, "quantity": qty, "orders": orders}
        (buy if flag == 1 else sell).append(row)
    buy.sort(key=lambda r: r["price"], reverse=True)
    sell.sort(key=lambda r: r["price"])
    return {"buy": buy, "sell": sell}


def parse_snapquote(payload: bytes) -> list[dict]:
    """Split a binary frame into SnapQuote records. Only 379-byte, mode-3
    packets are decoded; anything else in the frame is skipped."""
    out: list[dict] = []
    n = len(payload)
    i = 0
    # a frame is one-or-more concatenated equal-size packets; if the leading
    # packet isn't a 379-byte snapquote, don't try to realign mid-stream.
    if n < SNAPQUOTE_PACKET or payload[0] != SNAPQUOTE_MODE:
        return out
    while i + SNAPQUOTE_PACKET <= n:
        b = payload[i:i + SNAPQUOTE_PACKET]
        if b[0] != SNAPQUOTE_MODE:
            break
        token = b[2:27].split(b"\x00", 1)[0].decode("ascii", "ignore").strip()
        if not token:
            i += SNAPQUOTE_PACKET
            continue
        depth = _best_five(b, _BEST5_OFF)
        bid = depth["buy"][0] if depth["buy"] else {}
        ask = depth["sell"][0] if depth["sell"] else {}
        out.append({
            "mode": b[0],
            "exchange_type": b[1],
            "token": token,
            "seq": _i64(b, 27),
            "exch_ts_ms": _i64(b, 35),
            "ltp": _i64(b, 43) / _PRICE_DIV,
            "ltq": _i64(b, 51),
            "atp": _i64(b, 59) / _PRICE_DIV,
            "volume": _i64(b, 67),
            "tot_buy_qty": _f64(b, 75),
            "tot_sell_qty": _f64(b, 83),
            "open": _i64(b, 91) / _PRICE_DIV,
            "high": _i64(b, 99) / _PRICE_DIV,
            "low": _i64(b, 107) / _PRICE_DIV,
            "close": _i64(b, 115) / _PRICE_DIV,
            "ltt_epoch": _i64(b, 123),
            "oi": _i64(b, 131),
            "oi_change_pct": _f64(b, 139),
            "depth": depth,
            "bid": bid.get("price"), "bid_qty": bid.get("quantity"),
            "ask": ask.get("price"), "ask_qty": ask.get("quantity"),
            "upper_circuit": _i64(b, 347) / _PRICE_DIV,
            "lower_circuit": _i64(b, 355) / _PRICE_DIV,
            "week52_high": _i64(b, 363) / _PRICE_DIV,
            "week52_low": _i64(b, 371) / _PRICE_DIV,
        })
        i += SNAPQUOTE_PACKET
    return out


def build_snapquote_packet(**f) -> bytes:
    """Assemble a 379-byte mode-3 packet. TEST HELPER ONLY -- never used in
    capture. `f` keys mirror parse_snapquote output (prices in rupees; `depth`
    = {"buy":[{price,quantity,orders}], "sell":[...]}).
    """
    b = bytearray(SNAPQUOTE_PACKET)
    b[0] = SNAPQUOTE_MODE
    b[1] = int(f.get("exchange_type", 2))
    tok = str(f.get("token", "")).encode("ascii")[:25]
    b[2:2 + len(tok)] = tok
    struct.pack_into("<q", b, 27, int(f.get("seq", 0)))
    struct.pack_into("<q", b, 35, int(f.get("exch_ts_ms", 0)))
    struct.pack_into("<q", b, 43, round(float(f.get("ltp", 0)) * 100))
    struct.pack_into("<q", b, 51, int(f.get("ltq", 0)))
    struct.pack_into("<q", b, 59, round(float(f.get("atp", 0)) * 100))
    struct.pack_into("<q", b, 67, int(f.get("volume", 0)))
    struct.pack_into("<d", b, 75, float(f.get("tot_buy_qty", 0)))
    struct.pack_into("<d", b, 83, float(f.get("tot_sell_qty", 0)))
    struct.pack_into("<q", b, 91, round(float(f.get("open", 0)) * 100))
    struct.pack_into("<q", b, 99, round(float(f.get("high", 0)) * 100))
    struct.pack_into("<q", b, 107, round(float(f.get("low", 0)) * 100))
    struct.pack_into("<q", b, 115, round(float(f.get("close", 0)) * 100))
    struct.pack_into("<q", b, 123, int(f.get("ltt_epoch", 0)))
    struct.pack_into("<q", b, 131, int(f.get("oi", 0)))
    struct.pack_into("<d", b, 139, float(f.get("oi_change_pct", 0)))
    depth = f.get("depth") or {"buy": [], "sell": []}
    entries = [(1, r) for r in depth.get("buy", [])[:5]] + [(0, r) for r in depth.get("sell", [])[:5]]
    for k, (flag, r) in enumerate(entries[:10]):
        o = _BEST5_OFF + k * _BEST5_ENTRY
        struct.pack_into("<h", b, o, flag)
        struct.pack_into("<q", b, o + 2, int(r.get("quantity", 0)))
        struct.pack_into("<q", b, o + 10, round(float(r.get("price", 0)) * 100))
        struct.pack_into("<h", b, o + 18, int(r.get("orders", 0)))
    struct.pack_into("<q", b, 347, round(float(f.get("upper_circuit", 0)) * 100))
    struct.pack_into("<q", b, 355, round(float(f.get("lower_circuit", 0)) * 100))
    struct.pack_into("<q", b, 363, round(float(f.get("week52_high", 0)) * 100))
    struct.pack_into("<q", b, 371, round(float(f.get("week52_low", 0)) * 100))
    return bytes(b)
