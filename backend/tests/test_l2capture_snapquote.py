"""
L2 SnapQuote (Angel WS mode 3) parser + store -- offline, no network.

Verifies the 379-byte packet is decoded field-for-field, best-5 bid/ask are
split and ordered, concatenated packets and junk sizes are tolerated, and the
append-only store dedups on (token, seq, exch_ts).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.l2capture.snapquote import (  # noqa: E402
    parse_snapquote, build_snapquote_packet, SNAPQUOTE_PACKET, SNAPQUOTE_MODE)

_DEPTH = {
    "buy": [{"price": 100.1, "quantity": 300, "orders": 3},
            {"price": 100.0, "quantity": 150, "orders": 1},
            {"price": 99.9, "quantity": 200, "orders": 2}],
    "sell": [{"price": 100.3, "quantity": 120, "orders": 1},
             {"price": 100.4, "quantity": 400, "orders": 4}],
}
_FIELDS = dict(
    exchange_type=2, token="12345", seq=987654321, exch_ts_ms=1_756_000_000_000,
    ltp=100.25, ltq=75, atp=100.2, volume=182500, tot_buy_qty=51000.0, tot_sell_qty=48250.0,
    open=99.0, high=101.0, low=98.5, close=99.5, ltt_epoch=1_756_000_000, oi=1_250_000,
    oi_change_pct=0.0, depth=_DEPTH, upper_circuit=110.0, lower_circuit=90.0,
    week52_high=140.0, week52_low=70.0,
)


def test_packet_roundtrip_all_fields():
    pkt = build_snapquote_packet(**_FIELDS)
    assert len(pkt) == SNAPQUOTE_PACKET and pkt[0] == SNAPQUOTE_MODE
    (r,) = parse_snapquote(pkt)
    assert r["token"] == "12345"
    assert r["exchange_type"] == 2
    assert r["seq"] == 987654321
    assert r["exch_ts_ms"] == 1_756_000_000_000
    assert abs(r["ltp"] - 100.25) < 1e-9
    assert r["ltq"] == 75
    assert abs(r["atp"] - 100.2) < 1e-9
    assert r["volume"] == 182500
    assert abs(r["tot_buy_qty"] - 51000.0) < 1e-6
    assert abs(r["tot_sell_qty"] - 48250.0) < 1e-6
    assert abs(r["open"] - 99.0) < 1e-9 and abs(r["high"] - 101.0) < 1e-9
    assert abs(r["low"] - 98.5) < 1e-9 and abs(r["close"] - 99.5) < 1e-9
    assert r["oi"] == 1_250_000
    assert r["ltt_epoch"] == 1_756_000_000
    assert abs(r["upper_circuit"] - 110.0) < 1e-9 and abs(r["lower_circuit"] - 90.0) < 1e-9
    assert abs(r["week52_high"] - 140.0) < 1e-9 and abs(r["week52_low"] - 70.0) < 1e-9


def test_best_five_split_and_order():
    (r,) = parse_snapquote(build_snapquote_packet(**_FIELDS))
    buy, sell = r["depth"]["buy"], r["depth"]["sell"]
    assert [b["price"] for b in buy] == [100.1, 100.0, 99.9]      # highest first
    assert [s["price"] for s in sell] == [100.3, 100.4]           # lowest first
    assert buy[0]["quantity"] == 300 and buy[0]["orders"] == 3
    assert r["bid"] == 100.1 and r["bid_qty"] == 300
    assert r["ask"] == 100.3 and r["ask_qty"] == 120


def test_concatenated_packets():
    a = build_snapquote_packet(**{**_FIELDS, "token": "111", "ltp": 10.0})
    b = build_snapquote_packet(**{**_FIELDS, "token": "222", "ltp": 20.0})
    recs = parse_snapquote(a + b)
    assert [x["token"] for x in recs] == ["111", "222"]
    assert [x["ltp"] for x in recs] == [10.0, 20.0]


def test_non_snapquote_frame_ignored():
    assert parse_snapquote(b"") == []
    assert parse_snapquote(b"\x01" + b"\x00" * 50) == []          # a 51-byte LTP frame
    assert parse_snapquote(b"\x03" + b"\x00" * 10) == []          # too short
    junk = bytearray(build_snapquote_packet(**_FIELDS))
    junk[0] = 2                                                   # mode 2, not 3
    assert parse_snapquote(bytes(junk)) == []


def test_trailing_bytes_tolerated():
    pkt = build_snapquote_packet(**_FIELDS)
    recs = parse_snapquote(pkt + b"\x00\x01\x02")
    assert len(recs) == 1 and recs[0]["token"] == "12345"


def test_store_is_append_only_and_dedups(tmp_path):
    from app.l2capture.store import L2Store
    st = L2Store(str(tmp_path / "l2.db"))
    rid = st.start_run("test", [{"token": "12345", "exchange_type": 2}])
    recs = parse_snapquote(build_snapquote_packet(**_FIELDS))
    n1 = st.insert_ticks(recs, raw_id=None, run_id=rid, meta={"12345": {"symbol": "NIFTY", "kind": "FUTURE"}})
    n2 = st.insert_ticks(recs, raw_id=None, run_id=rid, meta={"12345": {"symbol": "NIFTY", "kind": "FUTURE"}})
    assert n1 == 1 and n2 == 0                                    # same (token, seq, exch_ts) -> ignored
    s = st.summary()
    assert s["ticks"] == 1 and s["distinct_tokens"] == 1
    row = st._con.execute("SELECT symbol, kind, bid, ask, depth_json FROM snapquote_ticks").fetchone()
    assert row[0] == "NIFTY" and row[1] == "FUTURE" and row[2] == 100.1 and row[3] == 100.3
    assert '"buy"' in row[4]
    st.close()


def test_raw_frame_preserved_and_dedup(tmp_path):
    from app.l2capture.store import L2Store
    st = L2Store(str(tmp_path / "l2.db"))
    pkt = build_snapquote_packet(**_FIELDS)
    a = st.put_raw_frame(pkt, n_packets=1, run_id=None)
    b = st.put_raw_frame(pkt, n_packets=1, run_id=None)
    assert a == b                                                 # sha256 dedup
    (cnt,) = st._con.execute("SELECT COUNT(*) FROM raw_frames").fetchone()
    assert cnt == 1
    st.close()
