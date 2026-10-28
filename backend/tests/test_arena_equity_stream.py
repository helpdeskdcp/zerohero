"""Equity order ticket data path: symbol search (prefix first, NIFTY 50 first), the quote router (websocket push vs the 3-second tape), and the server-sent-events stream."""
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.arena import api as A, equity as EQ, submit as SU

TOK = "t0k-" + "z" * 24
H = {"X-Arena-Token": TOK}
ROWS = [{"exch_seg": "NSE", "symbol": f"{n}-EQ", "name": n, "token": str(i)} for i, n in enumerate(
    ["RAILTEL", "RELAXO", "RELIANCE", "RELIABLE", "RECLTD", "RBLBANK", "TCS", "INFY", "BRELIANCE", "RELTD"], start=100)] + [{"exch_seg": "NFO", "symbol": "RELIANCE26NOVFUT", "name": "RELIANCE", "token": "9"}]


class FakeSdk:
    def load_instrument_master(self): return ROWS
    def search_instruments(self, symbol=None, exchange=None): return [r for r in ROWS if r["name"] == symbol and r["exch_seg"] == exchange]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(EQ, "_idx", None); EQ._tok_cache.clear() if hasattr(EQ, "_tok_cache") else None
    from app.histcap import equity_master as EM; EM._tok_cache.clear(); EQ._want.clear(); EQ._lq_cache.clear()
    monkeypatch.setattr(EQ, "_NIFTY50_CSV", "/nonexistent.csv")
    monkeypatch.setattr(EQ, "_nse_open", lambda: False)                                                  # never depend on the wall clock or the network: the failover tests switch it on explicitly


def test_search_prefix_first_then_contains_and_only_cash_equity():
    r = [x["symbol"] for x in EQ.search("r", 20, FakeSdk())]; assert r[:6] == ["RAILTEL", "RBLBANK", "RECLTD", "RELAXO", "RELIABLE", "RELIANCE"] and "RELIANCE26NOVFUT" not in r and "TCS" not in r
    assert [x["symbol"] for x in EQ.search("rel", 20, FakeSdk())][:4] == ["RELAXO", "RELIABLE", "RELIANCE", "RELTD"]
    assert [x["symbol"] for x in EQ.search("reliance", 20, FakeSdk())] == ["RELIANCE", "BRELIANCE"]          # prefix, then a name that only contains it
    assert EQ.search("", 5, FakeSdk()) == [] and EQ.search("ZZZZ", 5, FakeSdk()) == []


def test_nifty50_names_rank_first_and_carry_their_company(tmp_path, monkeypatch):
    f = tmp_path / "c.csv"; f.write_text("Company Name,Industry,Symbol,Series,ISIN Code\nReliance Industries Ltd.,Oil,RELIANCE,EQ,X\n"); monkeypatch.setattr(EQ, "_NIFTY50_CSV", str(f)); monkeypatch.setattr(EQ, "_idx", None)
    r = EQ.search("rel", 5, FakeSdk()); assert r[0]["symbol"] == "RELIANCE" and r[0]["company"] == "Reliance Industries Ltd."


class FakeFeed:
    def __init__(self): self.ltp = {}; self.connected = True; self.subs = []
    def subscribe(self, tokens, owner="default"): self.subs.append((owner, [t["token"] for t in tokens]))


def test_ticks_prefer_the_newer_websocket_push_and_keep_bid_ask_from_the_tape(monkeypatch):
    feed = FakeFeed(); monkeypatch.setattr(EQ, "_ws_feed", lambda: feed); now = 1000.0
    monkeypatch.setattr(EQ, "latest_quote", lambda s, path=None: {"ltp": 100.0, "bid": 99.9, "ask": 100.1, "t": now - 2})
    feed.ltp["102"] = {"ltp": 100.4, "ts_ms": 0, "recv": now - 0.2}                                             # RELIANCE token = 102: pushed after the tape quote
    t = EQ.ticks(["RELIANCE"], sdk=FakeSdk(), now=now)["RELIANCE"]
    assert t["src"] == "ws" and t["ltp"] == 100.4 and t["bid"] == 99.9 and t["ask"] == 100.1 and t["age_s"] == 0.2
    assert feed.subs[-1] == (EQ.EQ_OWNER, ["102"])                                                              # subscribed on demand, under its own owner
    feed.ltp["102"]["recv"] = now - 30                                                                          # stale push -> fall back to the tape
    t = EQ.ticks(["RELIANCE"], sdk=FakeSdk(), now=now)["RELIANCE"]; assert t["src"] == "db" and t["ltp"] == 100.0
    monkeypatch.setattr(EQ, "latest_quote", lambda s, path=None: None); feed.ltp.clear()
    t = EQ.ticks(["RELIANCE"], sdk=FakeSdk(), now=now)["RELIANCE"]; assert t["src"] is None and t["ltp"] is None                                    # nothing known: no number invented


def test_idle_symbols_leave_the_websocket(monkeypatch):
    feed = FakeFeed(); monkeypatch.setattr(EQ, "_ws_feed", lambda: feed); monkeypatch.setattr(EQ, "latest_quote", lambda s, path=None: None)
    EQ.ticks(["RELIANCE"], sdk=FakeSdk(), now=1000.0); EQ.ticks(["TCS"], sdk=FakeSdk(), now=1000.0 + EQ.IDLE_SEC + 5)
    assert feed.subs[-1][1] == ["106"] and "RELIANCE" not in EQ._want                                          # TCS only; RELIANCE timed out


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(SU, "PATH", str(tmp_path / "h.jsonl")); monkeypatch.setattr(A, "PARTICIPANTS_FILE", str(tmp_path / "none.json"))
    monkeypatch.setenv("ARENA_TOKEN_HUMAN", TOK); monkeypatch.setenv("ARENA_HUMAN_NAME", "INSTITUTION_TRADER"); monkeypatch.delenv("ARENA_PUBLIC", raising=False)
    monkeypatch.setenv("ARENA_START", "2020-01-01"); monkeypatch.setenv("ARENA_END", "2999-01-01")
    monkeypatch.setattr(EQ, "resolve", lambda sym, sdk=None: {"symbol": sym, "token": "102", "name": sym} if sym in ("RELIANCE", "TCS") else None)
    monkeypatch.setattr(EQ, "search", lambda q, limit=12, sdk=None: [{"symbol": "RELIANCE", "company": "", "token": "102"}] if q.upper().startswith("R") else [])
    A._hits.clear(); A._streams.clear(); app = FastAPI(); app.include_router(A.router); return TestClient(app)


def test_search_and_ticks_endpoints(client, monkeypatch):
    j = client.get("/api/arena/public/equity/search", params={"q": "re"}, headers=H).json(); assert j["results"][0]["symbol"] == "RELIANCE" and j["q"] == "RE"
    assert client.get("/api/arena/public/equity/search", params={"q": "x" * 30}, headers=H).status_code == 422
    assert client.get("/api/arena/public/equity/search", params={"q": "re"}).status_code == 401
    monkeypatch.setattr(EQ, "ticks", lambda syms, **k: {s: {"ltp": 1.5, "bid": 1.4, "ask": 1.6, "t": 1.0, "src": "ws"} for s in syms})
    t = client.get("/api/arena/public/equity/ticks", params={"symbols": "reliance,tcs,bad sym"}, headers=H).json(); assert sorted(t["ticks"]) == ["RELIANCE", "TCS"] and "sources" in t
    assert client.get("/api/arena/public/equity/ticks", params={"symbols": "bad sym"}, headers=H).status_code == 422


def test_stream_sends_changes_then_ends_and_releases_its_slot(client, monkeypatch):
    seq = iter([1.0, 1.0, 2.0] + [3.0] * 100)
    monkeypatch.setattr(EQ, "ticks", lambda syms, **k: {s: {"ltp": next(seq), "bid": 0.9, "ask": 1.1, "t": 1.0, "src": "ws"} for s in syms}); monkeypatch.setattr(A, "STREAM_MAX_SEC", 1.2)
    with client.stream("GET", "/api/arena/public/equity/stream", params={"symbols": "RELIANCE"}, headers=H) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream") and r.headers["x-accel-buffering"] == "no"
        ev = [json.loads(l[5:]) for l in r.iter_lines() if l.startswith("data:")]
    ltps = [e["ticks"]["RELIANCE"]["ltp"] for e in ev]; assert ltps[:3] == [1.0, 2.0, 3.0]                       # only CHANGES are sent (the repeated 1.0 is not)
    assert A._streams["INSTITUTION_TRADER"] == 0
    assert client.get("/api/arena/public/equity/stream", params={"symbols": "NOSUCH"}, headers=H).status_code == 404
    A._streams["INSTITUTION_TRADER"] = 6; assert client.get("/api/arena/public/equity/stream", params={"symbols": "RELIANCE"}, headers=H).status_code == 429


def test_failover_to_upstox_only_when_angel_has_nothing_fresh_and_nse_is_open(monkeypatch):
    feed = FakeFeed(); monkeypatch.setattr(EQ, "_ws_feed", lambda: feed); now = 1000.0
    monkeypatch.setattr(EQ, "latest_quote", lambda s, path=None: None); calls = []
    monkeypatch.setattr(EQ, "_upstox_ltp", lambda syms: (calls.append(list(syms)), {s: 55.5 for s in syms})[1]); monkeypatch.setattr(EQ, "_nse_open", lambda: True); monkeypatch.delenv("ARENA_EQUITY_FAILOVER", raising=False)
    t = EQ.ticks(["RELIANCE"], sdk=FakeSdk(), now=now)["RELIANCE"]; assert t["src"] == "upstox" and t["ltp"] == 55.5 and t["bid"] is None and calls == [["RELIANCE"]]      # display price only: no bid / ask invented
    feed.ltp["102"] = {"ltp": 100.4, "ts_ms": 0, "recv": now - 0.1}; calls.clear()
    t = EQ.ticks(["RELIANCE"], sdk=FakeSdk(), now=now)["RELIANCE"]; assert t["src"] == "ws" and calls == []                                       # Angel fresh: the second broker is not asked
    feed.ltp.clear(); monkeypatch.setattr(EQ, "_nse_open", lambda: False); assert EQ.ticks(["RELIANCE"], sdk=FakeSdk(), now=now)["RELIANCE"]["src"] is None and calls == []   # market closed: no failover
    monkeypatch.setattr(EQ, "_nse_open", lambda: True); monkeypatch.setenv("ARENA_EQUITY_FAILOVER", "0"); assert EQ.ticks(["RELIANCE"], sdk=FakeSdk(), now=now)["RELIANCE"]["src"] is None   # kill switch
    monkeypatch.delenv("ARENA_EQUITY_FAILOVER"); monkeypatch.setattr(EQ, "_upstox_ltp", lambda syms: (_ for _ in ()).throw(RuntimeError("down")))
    before = EQ._src_stats["upstox"]["errors"]; assert EQ.ticks(["RELIANCE"], sdk=FakeSdk(), now=now)["RELIANCE"]["src"] is None and EQ._src_stats["upstox"]["errors"] == before + 1   # a failing backup never breaks the answer


def test_upstox_ltp_maps_answers_by_instrument_token_and_caches(monkeypatch):
    monkeypatch.setattr(EQ, "_up_keys", {"RELIANCE": "NSE_EQ|INE002A01018", "TCS": "NSE_EQ|INE467B01029"}); EQ._up_cache.clear(); n = []
    from app.connectors import upstox as U
    def fake(keys, token=None): n.append(keys); return {"json": {"data": {"NSE_EQ:RELIANCE": {"last_price": 1196.2, "instrument_token": "NSE_EQ|INE002A01018"}, "NSE_EQ:TCS": {"last_price": 2496.9, "instrument_token": "NSE_EQ|INE467B01029"}}}}
    monkeypatch.setattr(U, "get_ltp", fake)
    assert EQ._upstox_ltp(["RELIANCE", "TCS", "NOSUCH"]) == {"RELIANCE": 1196.2, "TCS": 2496.9} and len(n) == 1 and n[0].count(",") == 1
    EQ._upstox_ltp(["RELIANCE"]); assert len(n) == 1                                                            # 2-second cache: no second request
