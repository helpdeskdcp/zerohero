"""app.connectors.angelone._fetch_candles_uncached -- real production
symptom (2026-09-22): AngelOne intermittently returns HTTP 200 with an
empty/malformed body ("Expecting value: line 1 column 1"), distinct from
the 5xx/connection-error cases _http() already retries. One extra attempt
must recover most of these. No real network call is ever made."""
import json

import pytest

from app.connectors import angelone


class _FakeResp:
    def __init__(self, content=b"", json_data=None, json_error=None):
        self.content = content
        self._json_data = json_data
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._json_data


def _ok_body():
    return {"status": True, "data": [["2026-09-22T09:00:00+05:30", 100, 101, 99, 100.5, 1000]]}


@pytest.fixture(autouse=True)
def _auth_ok(monkeypatch):
    monkeypatch.setattr(angelone, "_market_sdk", lambda *a, **k: None)   # force the REST fallback path
    monkeypatch.setattr(angelone, "_get_jwt", lambda: ("OK", "JWT-1", ""))
    monkeypatch.setattr(angelone, "_creds", lambda: {"api_key": "k", "client_id": "c",
                                                      "password": "p", "totp_secret": "s"})
    monkeypatch.setattr(angelone.time, "sleep", lambda *_: None)   # don't actually wait in tests
    # Isolate the retry mechanism itself from the freshness/market-hours
    # calculation (real wall-clock dependent, not what these tests check).
    monkeypatch.setattr(angelone, "_freshness_meta", lambda last_t, market: (0.0, True, "OPEN", "OK"))


def test_recovers_from_one_malformed_json_response(monkeypatch):
    calls = []

    def fake_http(method, url, **kw):
        calls.append(1)
        if len(calls) == 1:
            # non-empty but unparseable body -- the real symptom: resp.content
            # is truthy so .json() IS called, and raises. (An empty b""
            # content short-circuits to res={} without calling .json() at
            # all -- a different, already-handled branch, not this bug.)
            return _FakeResp(content=b" ", json_error=json.JSONDecodeError("Expecting value", "", 0))
        return _FakeResp(content=b"{}", json_data=_ok_body())

    monkeypatch.setattr(angelone, "_http", fake_http)
    out = angelone._fetch_candles_uncached("NSE", "NIFTY", "NSE", "26000", "FIVE_MINUTE",
                                           "2026-09-22 09:00", "2026-09-22 10:00", timeframe="5m")
    assert out["data_status"] == "OK"
    assert len(calls) == 2   # one failure, one recovery -- not more, not fewer


def test_returns_data_unavailable_after_all_retries_exhausted(monkeypatch):
    calls = []

    def fake_http(method, url, **kw):
        calls.append(1)
        return _FakeResp(content=b" ", json_error=json.JSONDecodeError("Expecting value", "", 0))

    monkeypatch.setattr(angelone, "_http", fake_http)
    out = angelone._fetch_candles_uncached("NSE", "NIFTY", "NSE", "26000", "FIVE_MINUTE",
                                           "2026-09-22 09:00", "2026-09-22 10:00", timeframe="5m")
    assert out["data_status"] == "DATA_UNAVAILABLE"
    assert out["reason"] == "FACT: network error contacting broker"
    assert len(calls) == angelone._RETRIES + 1   # exactly the documented attempt budget, never more


def test_first_attempt_success_makes_exactly_one_call(monkeypatch):
    calls = []

    def fake_http(method, url, **kw):
        calls.append(1)
        return _FakeResp(content=b"{}", json_data=_ok_body())

    monkeypatch.setattr(angelone, "_http", fake_http)
    out = angelone._fetch_candles_uncached("NSE", "NIFTY", "NSE", "26000", "FIVE_MINUTE",
                                           "2026-09-22 09:00", "2026-09-22 10:00", timeframe="5m")
    assert out["data_status"] == "OK"
    assert len(calls) == 1   # no retry overhead on the happy path
