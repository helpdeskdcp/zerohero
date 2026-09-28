"""app.connectors.dhan_parser / dhan_ws / app.engines.orderflow_engine.

All packet bytes are hand-built per the documented Dhan Live Market Feed
protocol (https://dhanhq.co/docs/v2/live-market-feed/,
https://dhanhq.co/docs/v2/annexure/, verified live 2026-09-28) -- never hits
the real network. dhan_ws.DhanOrderflowWorker's actual socket I/O is not
exercised here (that needs a real/fake websocket server); this file covers
message parsing, aggressor classification, and queue/engine dispatch, which
is where real bugs (byte-offset errors, off-by-one framing, double counting)
would actually live.
"""
from __future__ import annotations

import asyncio
import struct

from app.connectors import dhan_parser, dhan_ws
from app.engines import orderflow_engine


def _pack_full(security_id=101, ltp=100.5, ltq=25, ltt=1_700_000_000, atp=100.0,
              volume=1000, total_sell_qty=500, total_buy_qty=600, oi=200,
              day_open=99.0, day_close=98.5, day_high=101.0, day_low=98.0,
              depth=None, exchange_segment=2) -> bytes:
    """Build one real-shape Full(8) packet, 162 bytes total."""
    if depth is None:
        depth = [{"bid_qty": 10, "ask_qty": 12, "bid_orders": 1, "ask_orders": 1,
                  "bid_price": 100.0, "ask_price": 101.0}] * 5
    buf = bytearray(162)
    buf[0] = 8
    struct.pack_into("<h", buf, 1, 154)  # message length (payload, excl. 8-byte header)
    buf[3] = exchange_segment
    struct.pack_into("<i", buf, 4, security_id)
    struct.pack_into("<f", buf, 8, ltp)
    struct.pack_into("<h", buf, 12, ltq)
    struct.pack_into("<i", buf, 14, ltt)
    struct.pack_into("<f", buf, 18, atp)
    struct.pack_into("<i", buf, 22, volume)
    struct.pack_into("<i", buf, 26, total_sell_qty)
    struct.pack_into("<i", buf, 30, total_buy_qty)
    struct.pack_into("<i", buf, 34, oi)
    struct.pack_into("<i", buf, 38, 0)   # highest OI, unused
    struct.pack_into("<i", buf, 42, 0)   # lowest OI, unused
    struct.pack_into("<f", buf, 46, day_open)
    struct.pack_into("<f", buf, 50, day_close)
    struct.pack_into("<f", buf, 54, day_high)
    struct.pack_into("<f", buf, 58, day_low)
    for lvl, d in enumerate(depth[:5]):
        base = 62 + lvl * 20
        struct.pack_into("<i", buf, base, d["bid_qty"])
        struct.pack_into("<i", buf, base + 4, d["ask_qty"])
        struct.pack_into("<h", buf, base + 8, d["bid_orders"])
        struct.pack_into("<h", buf, base + 10, d["ask_orders"])
        struct.pack_into("<f", buf, base + 12, d["bid_price"])
        struct.pack_into("<f", buf, base + 16, d["ask_price"])
    return bytes(buf)


def _pack_disconnect(security_id=101, reason_code=805) -> bytes:
    buf = bytearray(10)
    buf[0] = 50
    struct.pack_into("<h", buf, 1, 2)
    buf[3] = 2
    struct.pack_into("<i", buf, 4, security_id)
    struct.pack_into("<h", buf, 8, reason_code)
    return bytes(buf)


# ---------------------------------------------------------------------------
# dhan_parser.parse_frame
# ---------------------------------------------------------------------------

def test_parse_frame_decodes_a_single_full_packet():
    out = dhan_parser.parse_frame(_pack_full(security_id=42, ltp=250.5, ltq=10, ltt=1_700_000_100))
    assert len(out) == 1
    p = out[0]
    assert p["type"] == "FULL" and p["security_id"] == 42
    assert p["ltp"] == 250.5 and p["ltq"] == 10 and p["ltt"] == 1_700_000_100
    assert len(p["depth"]) == 5
    assert p["depth"][0]["bid_price"] == 100.0 and p["depth"][0]["ask_price"] == 101.0


def test_parse_frame_handles_concatenated_packets_in_one_ws_frame():
    frame = _pack_full(security_id=1, ltt=1) + _pack_full(security_id=2, ltt=2)
    out = dhan_parser.parse_frame(frame)
    assert [p["security_id"] for p in out] == [1, 2]


def test_parse_frame_recognizes_disconnect_packet():
    out = dhan_parser.parse_frame(_pack_disconnect(reason_code=805))
    assert out == [{"type": "DISCONNECT", "security_id": 101, "reason_code": 805}]


def test_parse_frame_stops_cleanly_on_unknown_response_code_without_desync():
    good = _pack_full(security_id=7, ltt=7)
    junk = bytes([99]) + b"\x00" * 20  # unrecognized response code
    out = dhan_parser.parse_frame(good + junk)
    assert len(out) == 1 and out[0]["security_id"] == 7  # the good packet is still decoded


def test_parse_frame_stops_on_a_truncated_trailing_packet():
    out = dhan_parser.parse_frame(_pack_full()[:100])  # header + partial Full, no complete packet
    assert out == []


def test_parse_frame_empty_bytes_is_empty_list():
    assert dhan_parser.parse_frame(b"") == []


# ---------------------------------------------------------------------------
# dhan_parser.classify_tick -- aggressor inference
# ---------------------------------------------------------------------------

def test_classify_tick_quote_rule_buy_when_trade_at_or_above_best_ask():
    packet = dhan_parser.parse_frame(_pack_full(ltp=101.0, ltt=1))[0]  # best_ask=101.0
    tick, state = dhan_parser.classify_tick(packet, None)
    assert tick["side"] == "BUY_AGGRESSOR" and tick["classification_method"] == "QUOTE_RULE"
    assert state == {"ltt": 1, "ltp": 101.0}


def test_classify_tick_quote_rule_sell_when_trade_at_or_below_best_bid():
    packet = dhan_parser.parse_frame(_pack_full(ltp=100.0, ltt=1))[0]  # best_bid=100.0
    tick, _ = dhan_parser.classify_tick(packet, None)
    assert tick["side"] == "SELL_AGGRESSOR" and tick["classification_method"] == "QUOTE_RULE"


def test_classify_tick_falls_back_to_tick_rule_inside_the_spread():
    depth = [{"bid_qty": 1, "ask_qty": 1, "bid_orders": 1, "ask_orders": 1,
             "bid_price": 99.0, "ask_price": 101.0}] * 5  # 100.5 is strictly inside
    packet = dhan_parser.parse_frame(_pack_full(ltp=100.5, ltt=2, depth=depth))[0]
    tick, _ = dhan_parser.classify_tick(packet, {"ltt": 1, "ltp": 100.0})
    assert tick["side"] == "BUY_AGGRESSOR" and tick["classification_method"] == "TICK_RULE"

    tick2, _ = dhan_parser.classify_tick(packet, {"ltt": 1, "ltp": 101.0})
    assert tick2["side"] == "SELL_AGGRESSOR" and tick2["classification_method"] == "TICK_RULE"


def test_classify_tick_unknown_when_flat_inside_spread_with_no_movement():
    depth = [{"bid_qty": 1, "ask_qty": 1, "bid_orders": 1, "ask_orders": 1,
             "bid_price": 99.0, "ask_price": 101.0}] * 5
    packet = dhan_parser.parse_frame(_pack_full(ltp=100.0, ltt=2, depth=depth))[0]
    tick, _ = dhan_parser.classify_tick(packet, {"ltt": 1, "ltp": 100.0})
    assert tick["side"] == "UNKNOWN" and tick["classification_method"] == "TICK_RULE_FLAT"


def test_classify_tick_unknown_on_the_very_first_tick_with_no_prior_state():
    depth = [{"bid_qty": 1, "ask_qty": 1, "bid_orders": 1, "ask_orders": 1,
             "bid_price": 99.0, "ask_price": 101.0}] * 5
    packet = dhan_parser.parse_frame(_pack_full(ltp=100.0, ltt=1, depth=depth))[0]
    tick, _ = dhan_parser.classify_tick(packet, None)
    assert tick["side"] == "UNKNOWN" and tick["classification_method"] == "INSUFFICIENT_DATA"


def test_classify_tick_returns_none_when_last_trade_time_is_unchanged():
    """A repeated depth-only snapshot (same ltt) must not be double-counted
    as a new trade."""
    packet = dhan_parser.parse_frame(_pack_full(ltp=101.0, ltt=5))[0]
    tick, state = dhan_parser.classify_tick(packet, {"ltt": 5, "ltp": 101.0})
    assert tick is None
    assert state == {"ltt": 5, "ltp": 101.0}  # state still refreshed


# ---------------------------------------------------------------------------
# dhan_ws instrument-list env parsing
# ---------------------------------------------------------------------------

def test_get_spot_uses_market_context_when_it_has_a_value(monkeypatch):
    from app.mathematical_confluence import context as _ctx
    monkeypatch.setattr(dhan_ws, "_histcap_latest_index_spot", lambda sym: 999.0)  # must NOT be used
    monkeypatch.setattr(_ctx, "market_context", lambda sym: {"spot": 22780.25})
    assert dhan_ws._get_spot("NIFTY") == 22780.25


def test_get_spot_falls_back_to_histcap_when_market_context_has_none(monkeypatch):
    from app.mathematical_confluence import context as _ctx
    monkeypatch.setattr(_ctx, "market_context", lambda sym: {"spot": None})
    monkeypatch.setattr(dhan_ws, "_histcap_latest_index_spot", lambda sym: 72771.72)
    assert dhan_ws._get_spot("SENSEX") == 72771.72


def test_histcap_latest_index_spot_reads_the_newest_row(tmp_path, monkeypatch):
    import sqlite3
    from datetime import datetime, timezone
    db_path = str(tmp_path / "market_history.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE quote_snapshots (symbol TEXT, kind TEXT, ltp REAL, received_ts TEXT)")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO quote_snapshots VALUES ('SENSEX','INDEX',72000.0,'2020-01-01T00:00:00+00:00')")
    conn.execute("INSERT INTO quote_snapshots VALUES ('SENSEX','INDEX',72771.72,?)", (now,))
    conn.commit(); conn.close()
    monkeypatch.setattr("app.histcap.store.DB_PATH", db_path)
    assert dhan_ws._histcap_latest_index_spot("SENSEX") == 72771.72


def test_histcap_latest_index_spot_rejects_a_stale_row(tmp_path, monkeypatch):
    import sqlite3
    db_path = str(tmp_path / "market_history.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE quote_snapshots (symbol TEXT, kind TEXT, ltp REAL, received_ts TEXT)")
    conn.execute("INSERT INTO quote_snapshots VALUES ('SENSEX','INDEX',72000.0,'2020-01-01T00:00:00+00:00')")
    conn.commit(); conn.close()
    monkeypatch.setattr("app.histcap.store.DB_PATH", db_path)
    assert dhan_ws._histcap_latest_index_spot("SENSEX") is None


def test_histcap_latest_index_spot_none_when_symbol_never_captured(tmp_path, monkeypatch):
    import sqlite3
    db_path = str(tmp_path / "market_history.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE quote_snapshots (symbol TEXT, kind TEXT, ltp REAL, received_ts TEXT)")
    conn.commit(); conn.close()
    monkeypatch.setattr("app.histcap.store.DB_PATH", db_path)
    assert dhan_ws._histcap_latest_index_spot("SENSEX") is None


def test_instruments_from_env_parses_segment_security_id_pairs(monkeypatch):
    monkeypatch.setenv("DHAN_ORDERFLOW_INSTRUMENTS", "NSE_FNO:49081, NSE_FNO:49082 ,BAD_ENTRY")
    out = dhan_ws.instruments_from_env()
    assert out == [{"exchange_segment": "NSE_FNO", "security_id": "49081"},
                   {"exchange_segment": "NSE_FNO", "security_id": "49082"}]


def test_instruments_from_env_empty_when_unset(monkeypatch):
    monkeypatch.delenv("DHAN_ORDERFLOW_INSTRUMENTS", raising=False)
    assert dhan_ws.instruments_from_env() == []


def test_worker_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DHAN_ORDERFLOW_ENABLED", raising=False)
    w = dhan_ws.DhanOrderflowWorker(instruments=[{"exchange_segment": "NSE_FNO", "security_id": "1"}])
    assert w.enabled is False
    w.start()
    assert w._task is None
    assert "disabled" in w.last_error


def test_worker_refuses_to_start_with_no_instruments(monkeypatch):
    monkeypatch.setenv("DHAN_ORDERFLOW_ENABLED", "1")
    w = dhan_ws.DhanOrderflowWorker(instruments=[])
    w.start()
    assert w._task is None
    assert "no instruments" in w.last_error


def test_worker_with_explicit_instruments_never_auto_refreshes(monkeypatch):
    """A fixed instrument list (tests, or a manual override) must never be
    silently replaced by daily auto-resolution -- that only applies to the
    default/production construction path (instruments=None)."""
    fixed = [{"exchange_segment": "NSE_FNO", "security_id": "42"}]
    w = dhan_ws.DhanOrderflowWorker(instruments=fixed)
    asyncio.run(w._refresh_instruments())
    assert w._instruments == fixed
    assert w._instruments_day is None


def test_worker_default_construction_reads_auto_symbols_from_env(monkeypatch):
    monkeypatch.setenv("DHAN_ORDERFLOW_AUTO_SYMBOLS", "NIFTY, BANKNIFTY")
    monkeypatch.delenv("DHAN_ORDERFLOW_INSTRUMENTS", raising=False)
    w = dhan_ws.DhanOrderflowWorker()
    assert w._auto_syms == ["NIFTY", "BANKNIFTY"]
    assert w._explicit_instruments is None


def test_worker_refresh_instruments_merges_auto_and_static(monkeypatch):
    from app.connectors import dhan_instruments as di
    monkeypatch.setenv("DHAN_ORDERFLOW_INSTRUMENTS", "MCX_COMM:570750")
    w = dhan_ws.DhanOrderflowWorker()
    w._auto_syms = ["NIFTY"]

    monkeypatch.setattr(di, "resolve_daily_instruments", lambda symbols, get_spot: (
        [{"exchange_segment": "NSE_FNO", "security_id": "100"}],
        {"100": {"symbol": "NIFTY", "kind": "FUTURE"}}, []))  # fully resolved -- incomplete=[]
    asyncio.run(w._refresh_instruments())
    assert {i["security_id"] for i in w._instruments} == {"100", "570750"}
    assert w._instruments_day == di.today_ist()


def test_worker_refresh_instruments_is_a_noop_within_the_same_day(monkeypatch):
    from app.connectors import dhan_instruments as di
    w = dhan_ws.DhanOrderflowWorker()
    w._auto_syms = ["NIFTY"]
    calls = []
    monkeypatch.setattr(di, "resolve_daily_instruments",
                        lambda symbols, get_spot: (calls.append(1), ([], {}, []))[1])
    asyncio.run(w._refresh_instruments())
    asyncio.run(w._refresh_instruments())
    assert len(calls) == 1  # second call short-circuits on the unchanged day


def test_worker_refresh_instruments_retries_on_next_call_when_incomplete(monkeypatch):
    """A partial resolution (e.g. one symbol's spot lookup failed) must NOT
    lock in _instruments_day -- the next refresh call should try again
    instead of waiting until tomorrow."""
    from app.connectors import dhan_instruments as di
    w = dhan_ws.DhanOrderflowWorker()
    w._auto_syms = ["NIFTY", "BANKNIFTY"]
    calls = []

    def flaky(symbols, get_spot):
        calls.append(1)
        if len(calls) == 1:
            return ([{"exchange_segment": "NSE_FNO", "security_id": "100"}],
                    {"100": {"symbol": "NIFTY", "kind": "FUTURE"}}, ["BANKNIFTY"])
        return ([{"exchange_segment": "NSE_FNO", "security_id": "100"}],
                {"100": {"symbol": "NIFTY", "kind": "FUTURE"}}, [])

    monkeypatch.setattr(di, "resolve_daily_instruments", flaky)
    asyncio.run(w._refresh_instruments())
    assert w._instruments_day is None and w.last_error and "incomplete" in w.last_error
    asyncio.run(w._refresh_instruments())
    assert len(calls) == 2 and w._instruments_day == di.today_ist()


# ---------------------------------------------------------------------------
# app.engines.orderflow_engine -- footprint bars / CVD / stacked imbalance
# ---------------------------------------------------------------------------

def _full_packet(**kw) -> dict:
    return dhan_parser.parse_frame(_pack_full(**kw))[0]


def test_ingest_builds_cvd_from_classified_ticks():
    q: asyncio.Queue = asyncio.Queue()
    eng = orderflow_engine.OrderflowEngine(q)
    eng.ingest(_full_packet(security_id=1, ltp=101.0, ltq=10, ltt=1))  # >=ask -> BUY, +10
    eng.ingest(_full_packet(security_id=1, ltp=100.0, ltq=4, ltt=2))   # <=bid -> SELL, -4
    snap = eng.snapshot(1)
    assert snap["cvd"] == 6
    assert eng.status()["ticks_classified"] == 2


def test_ingest_skips_a_repeated_ltt_snapshot():
    q: asyncio.Queue = asyncio.Queue()
    eng = orderflow_engine.OrderflowEngine(q)
    eng.ingest(_full_packet(security_id=1, ltp=101.0, ltq=10, ltt=1))
    result = eng.ingest(_full_packet(security_id=1, ltp=101.0, ltq=10, ltt=1))  # same ltt
    assert result is None
    assert eng.snapshot(1)["cvd"] == 10  # not double-counted


def test_ingest_rolls_a_new_bar_when_the_60s_boundary_is_crossed():
    q: asyncio.Queue = asyncio.Queue()
    eng = orderflow_engine.OrderflowEngine(q)
    eng.ingest(_full_packet(security_id=1, ltp=101.0, ltq=10, ltt=1_700_000_000))
    eng.ingest(_full_packet(security_id=1, ltp=101.0, ltq=5, ltt=1_700_000_070))  # +70s -> new 60s bar
    snap = eng.snapshot(1)
    assert len(snap["closed_bars"][60]) == 1
    assert snap["closed_bars"][60][0]["buy_vol"] == 10
    assert list(snap["open_bars"][60])[0]["buy_vol"] == 5


def test_footprint_bar_delta_and_dict_shape():
    bar = orderflow_engine.FootprintBar(bar_start=0, bar_sec=60, price=100.0)
    bar.add({"ltp": 100.0, "ltq": 10, "side": "BUY_AGGRESSOR", "classification_method": "QUOTE_RULE"})
    bar.add({"ltp": 100.0, "ltq": 4, "side": "SELL_AGGRESSOR", "classification_method": "QUOTE_RULE"})
    bar.add({"ltp": 100.0, "ltq": 99, "side": "UNKNOWN", "classification_method": "INSUFFICIENT_DATA"})
    assert bar.buy_vol == 10 and bar.sell_vol == 4 and bar.delta == 6
    d = bar.to_dict()
    assert d["delta"] == 6 and d["classification_methods"]["INSUFFICIENT_DATA"] == 1


def test_stacked_imbalance_detects_three_consecutive_buy_dominant_levels():
    bar = orderflow_engine.FootprintBar(bar_start=0, bar_sec=60, price=100.0)
    for price in (100.0, 100.5, 101.0):
        bar.add({"ltp": price, "ltq": 30, "side": "BUY_AGGRESSOR", "classification_method": "QUOTE_RULE"})
        bar.add({"ltp": price, "ltq": 5, "side": "SELL_AGGRESSOR", "classification_method": "QUOTE_RULE"})
    runs = bar.stacked_imbalances()
    assert len(runs) == 1
    assert runs[0]["direction"] == "BUY" and runs[0]["levels"] == 3
    assert runs[0]["price_from"] == 100.0 and runs[0]["price_to"] == 101.0


def test_stacked_imbalance_ignores_a_run_shorter_than_three_levels():
    bar = orderflow_engine.FootprintBar(bar_start=0, bar_sec=60, price=100.0)
    for price in (100.0, 100.5):  # only 2 levels
        bar.add({"ltp": price, "ltq": 30, "side": "BUY_AGGRESSOR", "classification_method": "QUOTE_RULE"})
        bar.add({"ltp": price, "ltq": 5, "side": "SELL_AGGRESSOR", "classification_method": "QUOTE_RULE"})
    assert bar.stacked_imbalances() == []


def test_stacked_imbalance_requires_the_ratio_not_just_a_majority():
    bar = orderflow_engine.FootprintBar(bar_start=0, bar_sec=60, price=100.0)
    for price in (100.0, 100.5, 101.0):  # 2:1, below the 3:1 bar
        bar.add({"ltp": price, "ltq": 20, "side": "BUY_AGGRESSOR", "classification_method": "QUOTE_RULE"})
        bar.add({"ltp": price, "ltq": 10, "side": "SELL_AGGRESSOR", "classification_method": "QUOTE_RULE"})
    assert bar.stacked_imbalances() == []
