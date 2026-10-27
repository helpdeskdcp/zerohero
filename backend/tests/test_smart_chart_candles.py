from datetime import datetime

import pytest

from app.smart_chart import candles, symbols, upstox_ext
from tests import smart_chart_fakes as fk

IST = candles.IST
NIFTY = symbols.resolve("NIFTY")


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    return fk.install(monkeypatch)


def hm(ms):
    return datetime.fromtimestamp(ms / 1000, IST).strftime("%H:%M")


def test_parse_drops_corrupt_sorts_and_dedupes_order():
    rows = [["2026-10-23T09:16:00+05:30", 10, 11, 9, 10.5, 5, 0], ["2026-10-23T09:15:00+05:30", 10, 11, 9, 10, 1, 0],
            ["2026-10-23T09:17:00+05:30", 10, 9, 9, 10, 1, 0],        # high < open: corrupt
            ["2026-10-23T09:18:00+05:30", 0, 1, 1, 1, 1, 0],          # zero price: corrupt
            ["bad", 1, 1, 1, 1, 1, 0], ["2026-10-23T09:19:00+05:30", 10, 11, 9, 10, None, 0]]
    got = upstox_ext.parse_candles(rows)
    assert [b[0] for b in got] == sorted(b[0] for b in got) and len(got) == 3 and got[-1][5] == 0.0


def test_five_minute_buckets_equal_manual_aggregation_of_1m():
    r = candles.get_candles(NIFTY, "5m", 200)
    c = r["candles"][-30]
    raw = [b for b in fk.minute_bars() if c["time"] <= b[0] < c["time"] + 300_000]
    assert len(raw) == 5
    assert (c["open"], c["high"], c["low"], c["close"]) == (raw[0][1], max(b[2] for b in raw), min(b[3] for b in raw), raw[-1][4])


def test_hourly_buckets_anchor_to_session_open_with_15m_stub():
    r = candles.get_candles(NIFTY, "1h", 14)
    day = [c for c in r["candles"] if datetime.fromtimestamp(c["time"] / 1000, IST).date() == datetime.fromtimestamp(r["candles"][-1]["time"] / 1000, IST).date()]
    assert [hm(c["time"]) for c in day] == ["09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15"]
    assert r["meta"]["native_interval"] == "30minute" and r["meta"]["aggregated"] is True


def test_no_bucket_spans_two_days_and_series_is_ascending_unique():
    r = candles.get_candles(NIFTY, "15m", 900)
    ts = [c["time"] for c in r["candles"]]
    assert ts == sorted(set(ts))
    for c in r["candles"]:
        assert datetime.fromtimestamp(c["time"] / 1000, IST).date() == datetime.fromtimestamp((c["time"] + 899_999) / 1000, IST).date()


def test_limit_before_pagination_and_native_timeframes():
    a = candles.get_candles(NIFTY, "5m", 100)
    assert len(a["candles"]) == 100
    older = candles.get_candles(NIFTY, "5m", 100, before_ms=a["candles"][0]["time"])
    assert older["candles"][-1]["time"] < a["candles"][0]["time"] and len(older["candles"]) == 100
    d = candles.get_candles(NIFTY, "1d", 50)
    assert len(d["candles"]) == 50 and d["meta"]["native_interval"] == "day" and d["meta"]["aggregated"] is False
    assert candles.get_candles(NIFTY, "30m", 40)["meta"]["native_interval"] == "30minute"


def test_second_call_is_served_from_cache(fake):
    candles.get_candles(NIFTY, "5m", 200)
    n = dict(fake)
    candles.get_candles(NIFTY, "5m", 200)
    assert fake["hist"] == n["hist"]


def test_unsupported_timeframe_and_symbols():
    with pytest.raises(candles.CandleError):
        candles.get_candles(NIFTY, "1s", 10)
    with pytest.raises(candles.CandleError):
        candles.get_candles(NIFTY, "1w", 10)
    for bad in ("", "NSE:", "FOO", "NSE:../../etc", "NIFTY;DROP", "MCX:CRUDEOIL"):
        with pytest.raises(symbols.SymbolError):
            symbols.resolve(bad)


def test_tail_merge_replaces_forming_bar_without_duplicates(monkeypatch):
    s = candles._Series()
    bars = [(1000 + i * 60_000, 1, 2, 0.5, 1.5, 0) for i in range(10)]
    s.merge(bars)
    s.merge([(bars[-1][0], 1, 3, 0.5, 2.5, 0), (bars[-1][0] + 60_000, 2, 2, 2, 2, 0)])    # forming bar updated + new bar
    assert [b[0] for b in s.bars] == sorted(set(b[0] for b in s.bars)) and len(s.bars) == 11 and s.bars[9][2] == 3
    s.merge([(bars[-1][0] + 60_000, 2, 2.5, 2, 2.2, 0)])                                    # duplicate delivery of the newest bar
    assert len(s.bars) == 11 and s.bars[-1][2] == 2.5
    s.merge([bars[3]])                                                                      # stale out-of-order (older, single) bar must not corrupt order
    assert [b[0] for b in s.bars] == sorted(b[0] for b in s.bars)


def test_upstox_failure_serves_cached_data_marked_stale(monkeypatch, fake):
    candles.get_candles(NIFTY, "5m", 100)

    def boom(key, interval):
        raise upstox_ext.UpstoxError("Upstox token invalid or expired; renew the Upstox token in the server configuration", "UDAPI100050", 401)
    monkeypatch.setattr(upstox_ext, "fetch_intraday", boom)
    monkeypatch.setattr(candles.config, "LIVE_TAIL_TTL_SEC", 0.0)
    monkeypatch.setattr(candles, "_market_open", lambda ex: True)
    candles._series(NIFTY.key, "1minute").tail_at = 0.0
    r = candles.get_candles(NIFTY, "5m", 100)
    assert len(r["candles"]) == 100 and r["meta"]["stale"] is True and "token" in r["meta"]["error"]
    assert "Bearer" not in str(r) and "access_token" not in str(r)


def test_no_data_at_all_raises_clean_error(monkeypatch):
    monkeypatch.setattr(upstox_ext, "fetch_historical", lambda *a: [])
    candles.clear_cache()
    with pytest.raises(candles.CandleError):
        candles.get_candles(NIFTY, "5m", 10)


def test_volume_flag_index_vs_equity(monkeypatch):
    assert NIFTY.has_volume is False
    monkeypatch.setattr(symbols, "_equities", lambda: {"RELIANCE": ("NSE_EQ|INE002A01018", "RELIANCE INDUSTRIES")})
    eq = symbols.resolve("NSE:RELIANCE")
    assert eq.kind == "EQUITY" and eq.has_volume and symbols.resolve("RELIANCE").id == "NSE:RELIANCE"
