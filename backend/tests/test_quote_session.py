"""Quote requests: per-thread keep-alive session, per-call timeout override, broken connection dropped; the priority fetch uses a short timeout and tolerates clients without the argument."""
import sys, os, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest

from broker.angelone import client as cli
from app.histcap import priority as P


class FakeSession:
    made = []
    def __init__(self): self.posts = []; self.closed = False; FakeSession.made.append(self)
    def mount(self, *a, **k): pass
    def close(self): self.closed = True
    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json, timeout))
        if getattr(self, "boom", False): raise cli.requests.exceptions.ReadTimeout("slow")
        class R:
            def json(s): return {"data": {"fetched": [{"symbolToken": "1", "ltp": 10.0}], "unfetched": []}}
        return R()


@pytest.fixture
def client(monkeypatch):
    FakeSession.made.clear(); monkeypatch.setattr(cli.requests, "Session", FakeSession); cli._tls.quote_session = None
    c = cli.AngelOneClient(timeout=15); monkeypatch.setattr(c, "authenticate", lambda: True); monkeypatch.setattr(c, "_auth_headers", lambda: {})
    return c


def test_session_is_reused_within_a_thread_and_timeout_is_per_call(client):
    client.get_quotes_batch({"NSE": ["1"]}); client.get_quotes_batch({"NSE": ["1"]}, timeout=6.0)
    assert len(FakeSession.made) == 1 and [p[2] for p in FakeSession.made[0].posts] == [15, 6.0]               # one connection pool, default then override


def test_another_thread_gets_its_own_session(client):
    client.get_quotes_batch({"NSE": ["1"]}); seen = []
    t = threading.Thread(target=lambda: (client.get_quotes_batch({"NSE": ["1"]}), seen.append(len(FakeSession.made)))); t.start(); t.join()
    assert seen == [2]


def test_a_failed_request_drops_the_session_and_reports_api_error(client):
    client.get_quotes_batch({"NSE": ["1"]}); first = FakeSession.made[0]; first.boom = True
    out = client.get_quotes_batch({"NSE": ["1"]}, timeout=6.0)
    assert out["1"]["status"] == "API_ERROR" and first.closed is True
    client.get_quotes_batch({"NSE": ["1"]}); assert len(FakeSession.made) == 2                                   # the next call starts on a fresh connection


def test_priority_fetch_uses_the_short_timeout_and_tolerates_old_clients(monkeypatch):
    seen = {}
    class New:
        def get_quotes_batch(self, by_ex, mode="FULL", timeout=None): seen["t"] = timeout; return {"x": 1}
    class Old:
        def get_quotes_batch(self, by_ex, mode="FULL"): seen["old"] = True; return {"y": 2}
    class Broken:
        def get_quotes_batch(self, by_ex, mode="FULL", timeout=None): raise TypeError("unsupported operand")
    assert P._fetch(New(), {"NSE": ["1"]}) == {"x": 1} and seen["t"] == P.FETCH_TIMEOUT_SEC == 6.0
    assert P._fetch(Old(), {"NSE": ["1"]}) == {"y": 2} and seen["old"] is True
    with pytest.raises(TypeError): P._fetch(Broken(), {})                                                         # a TypeError that is not about `timeout` is not swallowed
