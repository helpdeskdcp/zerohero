"""app.connectors.dhan_historical -- daily/intraday backfill fetch + reshape.
requests.post is always stubbed; never hits the real network."""
from __future__ import annotations

from datetime import date

from app.connectors import dhan_historical as dh


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def _configure_token(monkeypatch, token="real-access-token"):
    from app import dhan_client
    monkeypatch.setattr(dhan_client, "get_access_token", lambda: token)


# ---------------------------------------------------------------------------
# fetch_daily / fetch_intraday
# ---------------------------------------------------------------------------

def test_fetch_daily_not_configured_without_a_token(monkeypatch):
    from app import dhan_client
    monkeypatch.setattr(dhan_client, "get_access_token", lambda: None)
    out = dh.fetch_daily("68407", "NSE_FNO", "FUTIDX", "2026-01-01", "2026-09-01")
    assert out["status"] == "NOT_CONFIGURED"


def test_fetch_daily_sends_real_body_and_headers(monkeypatch):
    _configure_token(monkeypatch)
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"], captured["headers"], captured["json"] = url, headers, json
        return _FakeResponse(200, {"open": [100.0], "timestamp": [1758772200]})

    monkeypatch.setattr(dh.requests, "post", fake_post)
    out = dh.fetch_daily("68407", "NSE_FNO", "FUTIDX", "2026-01-01", "2026-09-01", oi=True)
    assert out["status"] == "OK" and out["open"] == [100.0]
    assert captured["url"] == "https://api.dhan.co/v2/charts/historical"
    assert captured["headers"]["access-token"] == "real-access-token"
    assert captured["json"] == {"securityId": "68407", "exchangeSegment": "NSE_FNO",
                                "instrument": "FUTIDX", "fromDate": "2026-01-01",
                                "toDate": "2026-09-01", "oi": True}


def test_fetch_daily_surfaces_a_non_200_as_error(monkeypatch):
    _configure_token(monkeypatch)
    monkeypatch.setattr(dh.requests, "post",
                        lambda url, headers=None, json=None, timeout=None: _FakeResponse(401, {"error": "bad token"}))
    out = dh.fetch_daily("68407", "NSE_FNO", "FUTIDX", "2026-01-01", "2026-09-01")
    assert out["status"] == "ERROR" and out["http_status"] == 401


def test_fetch_intraday_not_configured_without_a_token(monkeypatch):
    from app import dhan_client
    monkeypatch.setattr(dhan_client, "get_access_token", lambda: None)
    out = dh.fetch_intraday("68407", "NSE_FNO", "FUTIDX", 1, "2026-09-01 09:00:00", "2026-09-01 09:30:00")
    assert out["status"] == "NOT_CONFIGURED"


def test_fetch_intraday_sends_the_interval_and_datetime_range(monkeypatch):
    _configure_token(monkeypatch)
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"], captured["json"] = url, json
        return _FakeResponse(200, {"open": [], "timestamp": []})

    monkeypatch.setattr(dh.requests, "post", fake_post)
    dh.fetch_intraday("68407", "NSE_FNO", "FUTIDX", 5, "2026-09-01 09:00:00", "2026-09-01 09:30:00")
    assert captured["url"] == "https://api.dhan.co/v2/charts/intraday"
    assert captured["json"]["interval"] == 5
    assert captured["json"]["fromDate"] == "2026-09-01 09:00:00"


# ---------------------------------------------------------------------------
# fetch_intraday_max -- 90-day chunking
# ---------------------------------------------------------------------------

def test_fetch_intraday_max_chunks_a_long_range_into_90_day_windows(monkeypatch):
    _configure_token(monkeypatch)
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((json["fromDate"], json["toDate"]))
        return _FakeResponse(200, {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
                                   "volume": [1], "timestamp": [1758772200], "open_interest": [0]})

    monkeypatch.setattr(dh.requests, "post", fake_post)
    monkeypatch.setattr(dh.time, "sleep", lambda s: None)
    out = dh.fetch_intraday_max("68407", "NSE_FNO", "FUTIDX", 1,
                                date(2026, 1, 1), date(2026, 9, 1))
    assert out["status"] == "OK"
    assert out["chunks_completed"] == len(calls) == 3  # 244 days / 90 -> 3 chunks
    assert len(out["timestamp"]) == 3  # one point per chunk, merged
    # chunks must be contiguous and chronological, never overlapping/gapped
    for (f1, t1), (f2, t2) in zip(calls, calls[1:]):
        from datetime import datetime, timedelta
        prev_end = datetime.strptime(t1, "%Y-%m-%d %H:%M:%S").date()
        next_start = datetime.strptime(f2, "%Y-%m-%d %H:%M:%S").date()
        assert next_start == prev_end + timedelta(days=1)


def test_fetch_intraday_max_stops_on_the_first_failed_chunk(monkeypatch):
    _configure_token(monkeypatch)
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            return _FakeResponse(200, {"open": [1.0], "timestamp": [1758772200]})
        return _FakeResponse(500, {"error": "boom"})

    monkeypatch.setattr(dh.requests, "post", fake_post)
    monkeypatch.setattr(dh.time, "sleep", lambda s: None)
    out = dh.fetch_intraday_max("68407", "NSE_FNO", "FUTIDX", 1,
                                date(2026, 1, 1), date(2026, 9, 1))
    assert out["status"] == "ERROR" and out["chunks_completed"] == 1


def test_fetch_intraday_max_single_chunk_for_a_short_range(monkeypatch):
    _configure_token(monkeypatch)
    monkeypatch.setattr(dh.requests, "post",
                        lambda url, headers=None, json=None, timeout=None: _FakeResponse(
                            200, {"open": [1.0], "timestamp": [1758772200]}))
    out = dh.fetch_intraday_max("68407", "NSE_FNO", "FUTIDX", 1, date(2026, 9, 1), date(2026, 9, 5))
    assert out["status"] == "OK" and out["chunks_completed"] == 1


# ---------------------------------------------------------------------------
# to_candle_rows -- reshape + the verified-live UTC timestamp conversion
# ---------------------------------------------------------------------------

def test_to_candle_rows_shapes_parallel_arrays_into_row_dicts():
    resp = {"status": "OK", "open": [100.0, 101.0], "high": [102.0, 103.0], "low": [99.0, 100.0],
            "close": [101.0, 102.0], "volume": [1000, 2000], "open_interest": [50, 60],
            "timestamp": [1758772200, 1758858600]}
    rows = dh.to_candle_rows(resp, instrument_key="NFO:68407", symbol="NIFTY", kind="FUTURE",
                             exchange="NSE", token="68407", tf="1d")
    assert len(rows) == 2
    assert rows[0]["o"] == 100.0 and rows[0]["h"] == 102.0 and rows[0]["v"] == 1000
    assert rows[0]["oi"] == 50 and rows[0]["source"] == "DHAN_HISTORICAL"
    assert rows[0]["instrument_key"] == "NFO:68407" and rows[0]["tf"] == "1d"


def test_to_candle_rows_timestamp_decodes_to_the_correct_utc_instant():
    """1790307900 is 2026-09-25 03:45:00 UTC == 09:15:00 IST -- the exact
    real value this codebase fetched live for NIFTY's market-open minute
    (see dhan_historical.py's module docstring), confirming no offset shift
    is applied."""
    resp = {"status": "OK", "open": [100.0], "timestamp": [1790307900]}
    rows = dh.to_candle_rows(resp, instrument_key="NFO:68407", symbol="NIFTY",
                             kind="FUTURE", exchange="NSE", token="68407", tf="1m")
    assert rows[0]["bar_start"] == "2026-09-25T03:45:00+00:00"


def test_to_candle_rows_session_date_ist_is_the_ist_calendar_date():
    """03:45 UTC on 2026-09-25 is 09:15 IST on the SAME date -- but a bar
    just before IST midnight (18:45 UTC, 15h later) rolls to the NEXT IST
    date (00:15 IST on 2026-09-26)."""
    resp = {"status": "OK", "open": [100.0], "timestamp": [1790307900, 1790307900 + 15 * 3600]}
    rows = dh.to_candle_rows(resp, instrument_key="NFO:68407", symbol="NIFTY",
                             kind="FUTURE", exchange="NSE", token="68407", tf="1m")
    assert rows[0]["session_date_ist"] == "2026-09-25"
    assert rows[1]["session_date_ist"] == "2026-09-26"


def test_to_candle_rows_empty_for_a_non_ok_response():
    assert dh.to_candle_rows({"status": "ERROR"}, instrument_key="x", symbol="NIFTY",
                             kind="FUTURE", exchange="NSE", token="1", tf="1d") == []


def test_to_candle_rows_carries_option_fields_when_given():
    resp = {"status": "OK", "open": [10.0], "timestamp": [1758772200]}
    rows = dh.to_candle_rows(resp, instrument_key="NFO:73895", symbol="NIFTY", kind="OPTION",
                             exchange="NSE", token="73895", tf="1d", expiry="2026-09-29",
                             strike=22800.0, option_type="CE")
    assert rows[0]["expiry"] == "2026-09-29" and rows[0]["strike"] == 22800.0
    assert rows[0]["option_type"] == "CE"


# ---------------------------------------------------------------------------
# backfill_daily -- orchestration into the shared market_candles table
# ---------------------------------------------------------------------------

def _isolated_store(tmp_path, monkeypatch):
    from app.histcap.store import HistStore
    store = HistStore(str(tmp_path / "mh.db"))
    monkeypatch.setattr("app.histcap.store.hist_store", lambda: store)
    return store


def test_backfill_daily_writes_rows_for_each_entry(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    _configure_token(monkeypatch)

    def fake_fetch_daily(security_id, exchange_segment, instrument, from_date, to_date, **kw):
        return {"status": "OK", "open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5],
                "volume": [1000], "open_interest": [0], "timestamp": [1758772200]}

    monkeypatch.setattr(dh, "fetch_daily", fake_fetch_daily)
    entries = [{"symbol": "NIFTY", "security_id": "68407", "exchange_segment": "NSE_FNO",
               "instrument": "FUTIDX", "kind": "FUTURE", "exchange": "NSE"}]
    out = dh.backfill_daily(entries)
    assert out["rows_written"] == 1
    assert out["entries_ok"] == [{"symbol": "NIFTY", "rows": 1}]
    assert out["entries_failed"] == []
    rows = store.get_candles("NIFTY", "1d", kind="FUTURE")
    assert len(rows) == 1 and rows[0]["source"] == "DHAN_HISTORICAL"


def test_backfill_daily_records_a_failed_entry_without_stopping_the_rest(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    _configure_token(monkeypatch)

    def fake_fetch_daily(security_id, exchange_segment, instrument, from_date, to_date, **kw):
        if security_id == "bad":
            return {"status": "ERROR", "http_status": 500}
        return {"status": "OK", "open": [1.0], "timestamp": [1758772200]}

    monkeypatch.setattr(dh, "fetch_daily", fake_fetch_daily)
    entries = [
        {"symbol": "BROKEN", "security_id": "bad", "exchange_segment": "NSE_FNO",
         "instrument": "FUTIDX", "kind": "FUTURE", "exchange": "NSE"},
        {"symbol": "NIFTY", "security_id": "68407", "exchange_segment": "NSE_FNO",
         "instrument": "FUTIDX", "kind": "FUTURE", "exchange": "NSE"},
    ]
    out = dh.backfill_daily(entries)
    assert out["rows_written"] == 1
    assert [f["symbol"] for f in out["entries_failed"]] == ["BROKEN"]
    assert [o["symbol"] for o in out["entries_ok"]] == ["NIFTY"]


# ---------------------------------------------------------------------------
# backfill_intraday
# ---------------------------------------------------------------------------

def test_backfill_intraday_writes_rows_with_the_correct_timeframe_label(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    _configure_token(monkeypatch)
    monkeypatch.setattr(dh, "fetch_intraday_max", lambda *a, **kw: {
        "status": "OK", "chunks_completed": 1, "open": [100.0], "high": [101.0], "low": [99.0],
        "close": [100.5], "volume": [10], "open_interest": [0], "timestamp": [1790307900]})
    entries = [{"symbol": "NIFTY", "security_id": "68407", "exchange_segment": "NSE_FNO",
               "instrument": "FUTIDX", "kind": "FUTURE", "exchange": "NSE"}]
    out = dh.backfill_intraday(entries, interval=1, days_back=90, sleep_between_calls=0)
    assert out["rows_written"] == 1
    assert out["entries_ok"] == [{"symbol": "NIFTY", "rows": 1, "chunks": 1}]
    rows = store.get_candles("NIFTY", "1m", kind="FUTURE")
    assert len(rows) == 1 and rows[0]["tf"] == "1m"


def test_backfill_intraday_records_a_failed_entry_without_stopping_the_rest(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    _configure_token(monkeypatch)

    def fake_fetch_intraday_max(security_id, *a, **kw):
        if security_id == "bad":
            return {"status": "ERROR", "http_status": 429, "chunks_completed": 0}
        return {"status": "OK", "chunks_completed": 1, "open": [1.0], "timestamp": [1790307900]}

    monkeypatch.setattr(dh, "fetch_intraday_max", fake_fetch_intraday_max)
    entries = [
        {"symbol": "BROKEN", "security_id": "bad", "exchange_segment": "NSE_FNO",
         "instrument": "FUTIDX", "kind": "FUTURE", "exchange": "NSE"},
        {"symbol": "NIFTY", "security_id": "68407", "exchange_segment": "NSE_FNO",
         "instrument": "FUTIDX", "kind": "FUTURE", "exchange": "NSE"},
    ]
    out = dh.backfill_intraday(entries, sleep_between_calls=0)
    assert out["rows_written"] == 1
    assert [f["symbol"] for f in out["entries_failed"]] == ["BROKEN"]
    assert [o["symbol"] for o in out["entries_ok"]] == ["NIFTY"]
