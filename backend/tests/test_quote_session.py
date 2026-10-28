"""Angel quote requests: keep-alive session per pool thread, hedged retry against the gateway's latency tail, concurrent exchanges, bounded by the caller's timeout."""
import os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest

from broker.angelone import client as cli
from app.histcap import priority as P

OK = {"data": {"fetched": [{"symbolToken": "1", "ltp": 10.0}], "unfetched": []}}


class FakeSession:
    made = []
    def __init__(self): self.posts = []; self.closed = False; self.boom = False; FakeSession.made.append(self)
    def mount(self, *a, **k): pass
    def close(self): self.closed = True
    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json, timeout))
        if self.boom: raise cli.requests.exceptions.ReadTimeout("slow")
        class R:
            def json(s): return OK
        return R()


def test_post_quote_reuses_the_session_within_a_thread_and_drops_it_on_error(monkeypatch):
    FakeSession.made.clear(); monkeypatch.setattr(cli.requests, "Session", FakeSession); cli._tls.quote_session = None
    cli._post_quote("NSE", ["1"], "FULL", {}, 6.0); cli._post_quote("NSE", ["1"], "FULL", {}, 4.0)
    assert len(FakeSession.made) == 1 and [p[2] for p in FakeSession.made[0].posts] == [6.0, 4.0]
    FakeSession.made[0].boom = True
    with pytest.raises(Exception): cli._post_quote("NSE", ["1"], "FULL", {}, 6.0)
    assert FakeSession.made[0].closed is True; cli._post_quote("NSE", ["1"], "FULL", {}, 6.0) if not FakeSession.made[0].boom else None


def scripted(monkeypatch, behaviours):
    """_post_quote replacement: behaviours is a list of (sleep_s, result_or_exception) consumed in call order."""
    calls = []; lock = threading.Lock()
    def fake(exch, chunk, mode, headers, timeout):
        with lock: i = len(calls); calls.append((exch, time.monotonic()))
        sl, res = behaviours[min(i, len(behaviours) - 1)]
        time.sleep(sl)
        if isinstance(res, Exception): raise res
        return res
    monkeypatch.setattr(cli, "_post_quote", fake); return calls


def test_fast_answer_sends_one_request_only(monkeypatch):
    calls = scripted(monkeypatch, [(0.02, OK)]); t = time.monotonic()
    r = cli._fetch_quote_jobs([("NSE", ["1"])], "FULL", {}, 6.0, hedge=0.3)
    assert r == [OK] and len(calls) == 1 and time.monotonic() - t < 0.25


def test_a_stalled_first_request_is_overtaken_by_the_hedge(monkeypatch):
    calls = scripted(monkeypatch, [(2.0, OK), (0.02, OK)]); t = time.monotonic()
    r = cli._fetch_quote_jobs([("NSE", ["1"])], "FULL", {}, 6.0, hedge=0.15)
    assert r == [OK] and len(calls) == 2 and time.monotonic() - t < 0.8                          # answered ~0.17 s after the hedge, not after the 2 s stall


def test_a_fast_failure_is_retried_immediately(monkeypatch):
    calls = scripted(monkeypatch, [(0.01, ConnectionError("reset")), (0.02, OK)]); t = time.monotonic()
    r = cli._fetch_quote_jobs([("NSE", ["1"])], "FULL", {}, 6.0, hedge=2.0)
    assert r == [OK] and len(calls) == 2 and time.monotonic() - t < 0.6                          # no 2 s wait for the hedge window


def test_when_every_attempt_fails_the_exception_is_returned_not_raised(monkeypatch):
    scripted(monkeypatch, [(0.01, ConnectionError("a")), (0.01, ConnectionError("b"))])
    r = cli._fetch_quote_jobs([("NSE", ["1"])], "FULL", {}, 6.0, hedge=0.1); assert isinstance(r[0], ConnectionError)


def test_without_hedging_a_stall_runs_into_the_callers_timeout(monkeypatch):
    calls = scripted(monkeypatch, [(3.0, OK)]); t = time.monotonic()
    r = cli._fetch_quote_jobs([("NSE", ["1"])], "FULL", {}, 0.4, hedge=0)
    assert isinstance(r[0], TimeoutError) and len(calls) == 1 and 0.35 < time.monotonic() - t < 1.2


def test_exchanges_are_fetched_concurrently_not_one_after_another(monkeypatch):
    calls = scripted(monkeypatch, [(0.3, OK)]); t = time.monotonic()
    r = cli._fetch_quote_jobs([("NFO", ["1"]), ("BFO", ["2"]), ("MCX", ["3"])], "FULL", {}, 6.0, hedge=2.0)
    assert r == [OK, OK, OK] and len(calls) == 3 and time.monotonic() - t < 0.7                  # sequential would be 0.9 s


def test_get_quotes_batch_maps_rows_and_reports_api_error_per_chunk(monkeypatch):
    c = cli.AngelOneClient(timeout=15); monkeypatch.setattr(c, "authenticate", lambda: True); monkeypatch.setattr(c, "_auth_headers", lambda: {})
    scripted(monkeypatch, [(0.0, OK)]); out = c.get_quotes_batch({"NSE": ["1", "2"]})
    assert out["1"]["status"] == "OK" and out["1"]["ltp"] == 10.0 and out["2"]["status"] == "DATA_UNAVAILABLE"
    scripted(monkeypatch, [(0.0, ConnectionError("x"))]); monkeypatch.setenv("ANGEL_QUOTE_HEDGE_SEC", "0")
    assert c.get_quotes_batch({"NSE": ["1"]})["1"]["status"] == "API_ERROR"


def test_hedge_setting_from_the_environment(monkeypatch):
    monkeypatch.delenv("ANGEL_QUOTE_HEDGE_SEC", raising=False); assert cli._hedge_sec() == 2.0
    monkeypatch.setenv("ANGEL_QUOTE_HEDGE_SEC", "0"); assert cli._hedge_sec() == 0.0
    monkeypatch.setenv("ANGEL_QUOTE_HEDGE_SEC", "junk"); assert cli._hedge_sec() == 2.0


def test_priority_fetch_uses_the_short_timeout_and_tolerates_old_clients():
    seen = {}
    class New:
        def get_quotes_batch(self, by_ex, mode="FULL", timeout=None): seen["t"] = timeout; return {"x": 1}
    class Old:
        def get_quotes_batch(self, by_ex, mode="FULL"): seen["old"] = True; return {"y": 2}
    class Broken:
        def get_quotes_batch(self, by_ex, mode="FULL", timeout=None): raise TypeError("unsupported operand")
    assert P._fetch(New(), {"NSE": ["1"]}) == {"x": 1} and seen["t"] == P.FETCH_TIMEOUT_SEC == 6.0
    assert P._fetch(Old(), {"NSE": ["1"]}) == {"y": 2} and seen["old"] is True
    with pytest.raises(TypeError): P._fetch(Broken(), {})
