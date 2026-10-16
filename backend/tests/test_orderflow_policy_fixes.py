"""Regression tests for defects found in the 2026-10-16 OrderFlow rule audit (each failed on the old code)."""
import asyncio

from app.connectors import dhan_parser
from app.engines import orderflow_engine
from tests.test_dhan_connector import _full_packet, _pack_full

_ADD = lambda ltp, ltq, side: {"ltp": ltp, "ltq": ltq, "side": side, "classification_method": "QUOTE_RULE"}


def _crossed_depth(bid, ask):
    return [{"bid_qty": 1, "ask_qty": 1, "bid_orders": 1, "ask_orders": 1, "bid_price": bid, "ask_price": ask}] * 5


def test_crossed_book_is_not_classified_by_quote_rule():
    # old code: ltp 100.5 >= ask 100 -> BUY via QUOTE_RULE even though the book is crossed (bid 101 > ask 100)
    pkt = dhan_parser.parse_frame(_pack_full(ltp=100.5, ltt=2, depth=_crossed_depth(101.0, 100.0)))[0]
    tick, _ = dhan_parser.classify_tick(pkt, {"ltt": 1, "ltp": 101.0})          # price fell vs previous trade
    assert tick["book_state"] == "CROSSED_OR_LOCKED"
    assert tick["side"] == "SELL_AGGRESSOR" and tick["classification_method"] == "TICK_RULE_BOOK_INVALID"


def test_locked_book_is_not_quote_ruled():
    pkt = dhan_parser.parse_frame(_pack_full(ltp=100.0, ltt=2, depth=_crossed_depth(100.0, 100.0)))[0]
    tick, _ = dhan_parser.classify_tick(pkt, None)
    assert tick["book_state"] == "CROSSED_OR_LOCKED" and tick["side"] == "UNKNOWN"


def test_valid_book_still_uses_quote_rule():
    pkt = dhan_parser.parse_frame(_pack_full(ltp=101.0, ltt=2))[0]
    tick, _ = dhan_parser.classify_tick(pkt, None)
    assert tick["book_state"] == "VALID" and tick["classification_method"] == "QUOTE_RULE"


def test_cvd_resets_on_a_new_ist_session_day():
    eng = orderflow_engine.OrderflowEngine(asyncio.Queue())
    day1 = 1_700_000_000
    eng.ingest(_full_packet(security_id=1, ltp=101.0, ltq=10, ltt=day1))
    eng.ingest(_full_packet(security_id=1, ltp=101.0, ltq=5, ltt=day1 + 60))
    assert eng.snapshot(1)["cvd"] == 15
    eng.ingest(_full_packet(security_id=1, ltp=101.0, ltq=7, ltt=day1 + 86400))     # next IST day
    snap = eng.snapshot(1)
    assert snap["cvd"] == 7 and snap["cvd_resets"] == 1


def test_out_of_order_tick_does_not_close_the_current_bar():
    eng = orderflow_engine.OrderflowEngine(asyncio.Queue())
    t0 = 1_700_000_040
    eng.ingest(_full_packet(security_id=1, ltp=101.0, ltq=10, ltt=t0 + 120))        # bar B (newer)
    eng.ingest(_full_packet(security_id=1, ltp=101.0, ltq=3, ltt=t0))               # late tick for an older bar
    eng.ingest(_full_packet(security_id=1, ltp=101.0, ltq=4, ltt=t0 + 121))         # still bar B
    snap = eng.snapshot(1)
    assert snap["late_ticks"] == 1
    assert len(snap["closed_bars"][60]) == 0                                       # old code closed bar B here and reopened the old bar
    assert [b["buy_vol"] for b in snap["open_bars"][60]] == [14]
    assert snap["cvd"] == 17                                                       # CVD is path-independent: late tick still counted


def test_stacked_imbalance_requires_adjacent_levels_when_tick_size_known():
    bar = orderflow_engine.FootprintBar(0, 60, 100.0, tick_size=0.5)
    for price in (100.0, 100.5, 105.0):                  # 105.0 is NOT adjacent to 100.5
        bar.add(_ADD(price, 30, "BUY_AGGRESSOR")); bar.add(_ADD(price, 5, "SELL_AGGRESSOR"))
    assert bar.stacked_imbalances() == []
    legacy = orderflow_engine.FootprintBar(0, 60, 100.0)  # tick size unknown -> legacy 'next occupied level', flagged as unverified
    for price in (100.0, 100.5, 105.0):
        legacy.add(_ADD(price, 30, "BUY_AGGRESSOR")); legacy.add(_ADD(price, 5, "SELL_AGGRESSOR"))
    runs = legacy.stacked_imbalances()
    assert len(runs) == 1 and runs[0]["adjacency_verified"] is False


def test_thin_levels_are_not_imbalances():
    bar = orderflow_engine.FootprintBar(0, 60, 100.0, tick_size=0.5)
    for price in (100.0, 100.5, 101.0):                  # 1 lot vs 0 would be an 'infinite' ratio under the old rule
        bar.add(_ADD(price, 1, "BUY_AGGRESSOR"))
    assert bar.stacked_imbalances() == []


def test_delta_pct_and_classified_volume_are_reported():
    bar = orderflow_engine.FootprintBar(0, 60, 100.0)
    bar.add(_ADD(100.0, 30, "BUY_AGGRESSOR")); bar.add(_ADD(100.0, 10, "SELL_AGGRESSOR")); bar.add(_ADD(100.0, 99, "UNKNOWN"))
    d = bar.to_dict()
    assert d["classified_vol"] == 40 and d["delta"] == 20 and d["delta_pct"] == 0.5
    assert orderflow_engine.FootprintBar(0, 60, 1.0).to_dict()["delta_pct"] is None


def test_persisted_ticks_carry_symbol_and_kind_when_meta_is_keyed_by_string_id():
    """old code looked up meta[int sid] in a dict keyed by str -> symbol/kind were NULL in all 538,393 persisted rows (audit 2026-10-16)."""
    class Store:
        def __init__(self): self.rows = []
        def insert_many(self, rows): self.rows += rows; return len(rows)
    store = Store()
    eng = orderflow_engine.OrderflowEngine(asyncio.Queue(), tick_store=store, meta_provider=lambda: {"48704": {"symbol": "NIFTY", "kind": "FUTURE"}})
    eng.ingest(_full_packet(security_id=48704, ltp=101.0, ltq=10, ltt=1_700_000_000))
    assert eng._pending[0]["symbol"] == "NIFTY" and eng._pending[0]["kind"] == "FUTURE"
