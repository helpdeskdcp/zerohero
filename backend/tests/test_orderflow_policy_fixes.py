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


def test_static_mcx_instruments_get_meta_from_the_master():
    from app.connectors import dhan_instruments as di
    rows = [{"SEM_EXM_EXCH_ID": "MCX", "SEM_SMST_SECURITY_ID": "570750", "SEM_INSTRUMENT_NAME": "FUTCOM", "SEM_TRADING_SYMBOL": "NATURALGAS-27Oct2026-FUT",
             "SM_SYMBOL_NAME": "NATURALGAS", "SEM_EXPIRY_DATE": "2026-10-27 23:30:00", "SEM_STRIKE_PRICE": "0.00000", "SEM_OPTION_TYPE": "XX"},
            {"SEM_EXM_EXCH_ID": "NSE", "SEM_SMST_SECURITY_ID": "570750", "SEM_INSTRUMENT_NAME": "OPTIDX", "SEM_TRADING_SYMBOL": "NIFTY-X", "SM_SYMBOL_NAME": "",
             "SEM_EXPIRY_DATE": "2026-10-27 00:00:00", "SEM_STRIKE_PRICE": "1.0", "SEM_OPTION_TYPE": "CE"},      # same id on another exchange must not match
            {"SEM_EXM_EXCH_ID": "MCX", "SEM_SMST_SECURITY_ID": "999", "SEM_INSTRUMENT_NAME": "OPTFUT", "SEM_TRADING_SYMBOL": "CRUDEOIL-19Oct2026-6000-CE",
             "SM_SYMBOL_NAME": "", "SEM_EXPIRY_DATE": "2026-10-19 23:30:00", "SEM_STRIKE_PRICE": "6000.0", "SEM_OPTION_TYPE": "CE"}]
    out = di.describe_static_instruments([{"exchange_segment": "MCX_COMM", "security_id": "570750"}, {"exchange_segment": "MCX_COMM", "security_id": "999"},
                                          {"exchange_segment": "MCX_COMM", "security_id": "404"}], rows)
    assert out["570750"] == {"symbol": "NATURALGAS", "kind": "FUTURE", "expiry": "2026-10-27", "trading_symbol": "NATURALGAS-27Oct2026-FUT", "tick_size": None}
    assert out["999"]["symbol"] == "CRUDEOIL" and out["999"]["kind"] == "OPTION_CE" and out["999"]["strike"] == 6000.0
    assert "404" not in out                                  # unknown ids are never guessed


def test_describe_static_instruments_without_instruments_does_not_load_the_master(monkeypatch):
    from app.connectors import dhan_instruments as di
    monkeypatch.setattr(di, "_load_master", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert di.describe_static_instruments([]) == {}


# ---- MCX futures auto-roll (CRUDEOIL 569900 expires 2026-10-19 23:30) -------------------------------------------------------
def _fut_row(sec_id, sym, exp):
    return {"SEM_EXM_EXCH_ID": "MCX", "SEM_SEGMENT": "M", "SEM_SMST_SECURITY_ID": sec_id, "SEM_INSTRUMENT_NAME": "FUTCOM",
            "SEM_TRADING_SYMBOL": f"{sym}-{exp}-FUT", "SM_SYMBOL_NAME": sym, "SEM_EXPIRY_DATE": f"{exp} 23:30:00", "SEM_STRIKE_PRICE": "0", "SEM_OPTION_TYPE": "XX"}


def test_front_future_rolls_on_expiry_day_with_min_days(monkeypatch):
    from datetime import date
    from app.connectors import dhan_instruments as di
    rows = [_fut_row("569900", "CRUDEOIL", "2026-10-19"), _fut_row("571111", "CRUDEOIL", "2026-11-19"),
            {**_fut_row("1", "CRUDEOILM", "2026-10-19"), "SEM_TRADING_SYMBOL": "CRUDEOILM-19Oct2026-FUT"}]      # mini contract must not match CRUDEOIL
    for today, want in ((date(2026, 10, 16), "569900"), (date(2026, 10, 18), "569900"), (date(2026, 10, 19), "571111"), (date(2026, 10, 20), "571111")):
        monkeypatch.setattr(di, "today_ist", lambda t=today: t)
        out, meta, inc = di.resolve_front_futures(["CRUDEOIL"], min_days=1, rows=rows)
        assert out == [{"exchange_segment": "MCX_COMM", "security_id": want}] and inc == [] and meta[want]["symbol"] == "CRUDEOIL", today
    monkeypatch.setattr(di, "today_ist", lambda: date(2026, 10, 19))
    out, _, _ = di.resolve_front_futures(["CRUDEOIL"], min_days=0, rows=rows)           # min_days=0 keeps the expiring contract until it expires
    assert out[0]["security_id"] == "569900"


def test_unknown_or_missing_future_is_incomplete_not_guessed():
    from app.connectors import dhan_instruments as di
    out, meta, inc = di.resolve_front_futures(["GOLD", "NATURALGAS"], rows=[])
    assert out == [] and meta == {} and sorted(inc) == ["GOLD", "NATURALGAS"]


def test_worker_rolls_mcx_and_drops_expired_static_ids(monkeypatch):
    from datetime import date
    from app.connectors import dhan_instruments as di, dhan_ws
    monkeypatch.setenv("DHAN_ORDERFLOW_AUTO_FUTURES", "CRUDEOIL")
    monkeypatch.setenv("DHAN_ORDERFLOW_AUTO_SYMBOLS", "")
    monkeypatch.setenv("DHAN_ORDERFLOW_INSTRUMENTS", "MCX_COMM:569900,MCX_COMM:570750")
    rows = [_fut_row("569900", "CRUDEOIL", "2026-10-19"), _fut_row("571111", "CRUDEOIL", "2026-11-19"), _fut_row("570750", "NATURALGAS", "2026-10-27")]
    monkeypatch.setattr(di, "_load_master", lambda *a, **k: rows)
    monkeypatch.setattr(di, "today_ist", lambda: date(2026, 10, 20))                        # after the Oct crude contract expired
    w = dhan_ws.DhanOrderflowWorker()
    asyncio.run(w._refresh_instruments())
    ids = [str(i["security_id"]) for i in w._instruments]
    assert ids == ["571111", "570750"]                                                       # crude rolled to Nov; stale 569900 dropped; NG static kept once
    assert w._instrument_meta["571111"]["symbol"] == "CRUDEOIL" and w._instrument_meta["570750"]["symbol"] == "NATURALGAS"
    assert w._instruments_day == date(2026, 10, 20)


def test_worker_static_id_already_auto_resolved_is_not_subscribed_twice(monkeypatch):
    from datetime import date
    from app.connectors import dhan_instruments as di, dhan_ws
    monkeypatch.setenv("DHAN_ORDERFLOW_AUTO_FUTURES", "NATURALGAS")
    monkeypatch.setenv("DHAN_ORDERFLOW_AUTO_SYMBOLS", "")
    monkeypatch.setenv("DHAN_ORDERFLOW_INSTRUMENTS", "MCX_COMM:570750")
    monkeypatch.setattr(di, "_load_master", lambda *a, **k: [_fut_row("570750", "NATURALGAS", "2026-10-27")])
    monkeypatch.setattr(di, "today_ist", lambda: date(2026, 10, 16))
    w = dhan_ws.DhanOrderflowWorker()
    asyncio.run(w._refresh_instruments())
    assert [str(i["security_id"]) for i in w._instruments] == ["570750"]


def test_auto_futures_default_and_opt_out(monkeypatch):
    from app.connectors import dhan_ws
    monkeypatch.delenv("DHAN_ORDERFLOW_AUTO_FUTURES", raising=False)
    assert dhan_ws._auto_futures() == ["NATURALGAS", "CRUDEOIL"]
    monkeypatch.setenv("DHAN_ORDERFLOW_AUTO_FUTURES", "")
    assert dhan_ws._auto_futures() == []


# ---- real Dhan prices are float32: snap to the tick grid (audit replay 2026-10-17) -----------------------------------------------
def test_float32_prices_are_adjacent_on_the_tick_grid():
    bar = orderflow_engine.FootprintBar(0, 60, 288.3, tick_size=0.1)
    for price in (288.299987792969, 288.399993896484, 288.5, 288.600006103516):     # real NATURALGAS values, diffs 0.100006 / 0.099976
        bar.add(_ADD(price, 30, "BUY_AGGRESSOR")); bar.add(_ADD(price, 5, "SELL_AGGRESSOR"))
    runs = bar.stacked_imbalances()
    assert len(runs) == 1 and runs[0]["levels"] == 4 and runs[0]["adjacency_verified"] is True
    assert runs[0]["price_from"] == 288.3 and runs[0]["price_to"] == 288.6                 # snapped, not float32 noise


def test_float32_noise_does_not_split_one_price_into_two_levels():
    bar = orderflow_engine.FootprintBar(0, 60, 22371.3, tick_size=0.1)
    bar.add(_ADD(22371.30078125, 10, "BUY_AGGRESSOR")); bar.add(_ADD(22371.3, 10, "BUY_AGGRESSOR"))
    assert len(bar.levels) == 1


def test_engine_passes_instrument_tick_size_from_meta():
    eng = orderflow_engine.OrderflowEngine(asyncio.Queue(), meta_provider=lambda: {"570750": {"symbol": "NATURALGAS", "tick_size": 0.1}})
    eng.ingest(_full_packet(security_id=570750, ltp=101.0, ltq=10, ltt=1_700_000_000))
    assert eng._symbols[570750].tick_size == 0.1
    assert next(iter(eng.snapshot(570750)["open_bars"][60]))["n_levels"] == 1


def test_master_tick_size_is_converted_from_paise():
    from app.connectors import dhan_instruments as di
    assert di._tick_size({"SEM_TICK_SIZE": "10.0000"}) == 0.1 and di._tick_size({"SEM_TICK_SIZE": "100.0000"}) == 1.0
    assert di._tick_size({"SEM_TICK_SIZE": "20.0000"}) == 0.2 and di._tick_size({"SEM_TICK_SIZE": ""}) is None and di._tick_size({}) is None
    rows = [{"SEM_EXM_EXCH_ID": "MCX", "SEM_SEGMENT": "M", "SEM_SMST_SECURITY_ID": "570750", "SEM_INSTRUMENT_NAME": "FUTCOM",
             "SEM_TRADING_SYMBOL": "NATURALGAS-27Oct2026-FUT", "SM_SYMBOL_NAME": "NATURALGAS", "SEM_EXPIRY_DATE": "2026-10-27 23:30:00",
             "SEM_STRIKE_PRICE": "0", "SEM_OPTION_TYPE": "XX", "SEM_TICK_SIZE": "10.0000"}]
    assert di.describe_static_instruments([{"exchange_segment": "MCX_COMM", "security_id": "570750"}], rows)["570750"]["tick_size"] == 0.1


def test_option_legs_carry_tick_size_from_the_master(monkeypatch):
    from datetime import date
    from app.connectors import dhan_instruments as di

    def leg(sid, typ):
        return {"SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "D", "SEM_SMST_SECURITY_ID": sid, "SEM_INSTRUMENT_NAME": "OPTIDX", "SEM_TRADING_SYMBOL": f"NIFTY-Oct2026-23000-{typ}",
                "SEM_EXPIRY_DATE": "2026-10-27 14:30:00", "SEM_STRIKE_PRICE": "23000.00000", "SEM_OPTION_TYPE": typ, "SEM_TICK_SIZE": "5.0000"}
    fut = {"SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "D", "SEM_SMST_SECURITY_ID": "9", "SEM_INSTRUMENT_NAME": "FUTIDX", "SEM_TRADING_SYMBOL": "NIFTY-Oct2026-FUT",
           "SEM_EXPIRY_DATE": "2026-10-27 14:30:00", "SEM_STRIKE_PRICE": "0", "SEM_OPTION_TYPE": "XX", "SEM_TICK_SIZE": "10.0000"}
    monkeypatch.setattr(di, "_load_master", lambda *a, **k: [leg("1", "CE"), leg("2", "PE"), fut])
    monkeypatch.setattr(di, "today_ist", lambda: date(2026, 10, 17))
    r = di.resolve_atm_options("NIFTY", 23010)
    assert r["ce"]["tick_size"] == 0.05 and r["pe"]["tick_size"] == 0.05
    out, meta, inc = di.resolve_daily_instruments(["NIFTY"], lambda s: 23010)
    assert inc == [] and meta["1"]["tick_size"] == 0.05 and meta["2"]["tick_size"] == 0.05 and meta["9"]["tick_size"] == 0.1
