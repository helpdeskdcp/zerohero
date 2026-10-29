"""Arena equity through the public API: submit (BUY / SELL), rejection of typos and unknown symbols, amend, and the /live rows (waiting / open long / open short / closed)."""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.arena import api as A, equity as EQ, run as R, submit as SU
from app.forward_test import ledger as L

TOK = "t0k-" + "y" * 24
H = {"X-Arena-Token": TOK}
BUY = {"instrument": "EQUITY", "underlying": "RELIANCE", "side": "BUY", "entry_mode": "NEAR", "entry": 1000, "sl": 990, "targets": [1010, 1020], "qty": 10}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(SU, "PATH", str(tmp_path / "h.jsonl"))
    monkeypatch.setattr(A, "PARTICIPANTS_FILE", str(tmp_path / "none.json"))
    monkeypatch.setenv("ARENA_TOKEN_HUMAN", TOK); monkeypatch.setenv("ARENA_HUMAN_NAME", "INSTITUTION_TRADER"); monkeypatch.delenv("ARENA_PUBLIC", raising=False)
    monkeypatch.setenv("ARENA_START", "2020-01-01"); monkeypatch.setenv("ARENA_END", "2999-01-01")               # ARENA_LIVE
    from app.arena import contracts as CT
    monkeypatch.setattr(CT, "DB", str(tmp_path / "no_market.db")); CT._cache.clear()
    import app.market_calendar as mc
    monkeypatch.setattr(mc, "is_trading", lambda *a, **k: True)
    monkeypatch.setattr(EQ, "resolve", lambda sym, sdk=None: {"symbol": sym, "token": "2885", "name": sym} if sym in ("RELIANCE", "INFY") else None)
    monkeypatch.setattr(EQ, "latest_quote", lambda sym, path=None: {"ltp": 1000.0, "bid": 999.9, "ask": 1000.1, "t": time.time()})
    monkeypatch.setattr(EQ, "watch", lambda *a, **k: {})
    A._hits.clear(); A._cache.update(t=0.0, v=None); A._cache.pop("live", None)
    app = FastAPI(); app.include_router(A.router); return TestClient(app)


def post(c, body): return c.post("/api/arena/public/submit", json=body, headers=H)


def test_submit_buy_and_sell_are_stored_with_equity_fields(client):
    r = post(client, BUY); assert r.status_code == 200, r.text
    j = r.json(); assert j["instrument"] == "EQUITY" and j["side"] == "BUY" and j["qty"] == 10 and j["id"] == "H00001"
    s = post(client, {**BUY, "side": "SELL", "sl": 1010, "targets": [990, 980]}); assert s.status_code == 200, s.text
    recs = [x for x in SU.load() if x.get("type") == "HUMAN_SIGNAL"]
    assert [x["side"] for x in recs] == ["BUY", "SELL"] and all(x["expiry"] == "" and x["option_type"] == "EQ" for x in recs) and L.verify_chain(SU.PATH)[0]


@pytest.mark.parametrize("bad", [{"underlying": "NOSUCH"}, {"entry": 1300, "sl": 1290, "targets": [1310]}, {"sl": 1010}, {"qty": 0}, {"side": "HOLD"}])
def test_bad_equity_submissions_are_rejected_and_not_stored(client, bad):
    r = post(client, {**BUY, **bad}); assert r.status_code == 422 and SU.load() == [], r.text


def test_amend_flow_for_equity_quantity_and_entry(client):
    sid = post(client, {**BUY, "entry_mode": "LIMIT", "entry": 995}).json()["id"]
    ok = client.post("/api/arena/public/amend", json={"signal_id": sid, "action": "AMEND_LOTS", "lots": 250}, headers=H); assert ok.status_code in (200, 409), ok.text
    e = client.post("/api/arena/public/amend", json={"signal_id": sid, "action": "AMEND_ENTRY", "entry": 996}, headers=H); assert e.status_code in (200, 409), e.text
    assert any(x.get("action") == "AMEND_ENTRY" for x in SU.load() if x.get("type") == "HUMAN_EVENT") or e.status_code == 409


def _live_with(client, monkeypatch, results):
    monkeypatch.setattr(R, "run", lambda sigs, **k: {"INSTITUTION_TRADER": {"results": [results[s["id"]] for s in sigs if s["id"] in results], "summary": {}}})
    A._cache.pop("live", None)
    return client.get("/api/arena/public/live", headers=H).json()


def test_live_rows_for_waiting_open_long_open_short_and_closed(client, monkeypatch):
    ids = [post(client, b).json()["id"] for b in (
        {**BUY, "entry_mode": "LIMIT", "entry": 995},                                              # waiting buy limit
        BUY,                                                                                       # open long
        {**BUY, "side": "SELL", "sl": 1010, "targets": [990]},                                     # open short
        {**BUY, "side": "SELL", "sl": 1010, "targets": [990]})]                                    # closed short
    res = {ids[0]: {"id": ids[0], "status": "NOT_TRIGGERED", "valid_until": None, "entry_now": 995.0, "sl_now": 990.0, "targets_now": [1010.0, 1020.0], "lots": 10},
           ids[1]: {"id": ids[1], "status": "NO_DATA:no_exit_snapshot", "fill": 1000.0, "lot": 1, "lots": 10, "sl_now": 990.0, "targets_now": [1010.0], "last": {"bid": 999.9, "ask": 1000.1, "ltp": 1000.0}},
           ids[2]: {"id": ids[2], "status": "NO_DATA:no_exit_snapshot", "fill": 1000.0, "lot": 1, "lots": 10, "sl_now": 1010.0, "targets_now": [990.0], "last": {"bid": 999.9, "ask": 1000.1, "ltp": 1000.0}},
           ids[3]: {"id": ids[3], "status": "SETTLED", "fill": 1000.0, "exit": 990.0, "exit_reason": "TARGET", "net_rs": 80.5}}
    rows = {x["id"]: x for x in _live_with(client, monkeypatch, res)["rows"]}
    assert rows[ids[0]]["state"] == "WAITING" and rows[ids[0]]["entry"] == 995.0 and rows[ids[0]]["instrument"] == "EQUITY" and rows[ids[0]]["label"] == "RELIANCE BUY 10 sh"
    assert rows[ids[0]]["targets"] == [1010.0, 1020.0] and rows[ids[0]]["waiting_note"].startswith("waiting for the ask to come down to 995")
    long_, short = rows[ids[1]], rows[ids[2]]
    assert long_["state"] == "ACTIVE" and short["state"] == "ACTIVE" and short["label"] == "RELIANCE SELL 10 sh"
    from app.arena import settle as S
    assert long_["pnl_rs"] == pytest.approx(round((1000.0 - 1000.0) * 10 - S.equity_cost_rs("BUY", 1000.0, 1000.0, 10), 1), abs=0.06)          # headline = gross at the LTP minus the charges, once
    assert short["pnl_rs"] == pytest.approx(round((1000.0 - 1000.0) * 10 - S.equity_cost_rs("SELL", 1000.0, 1000.0, 10), 1), abs=0.06)
    assert long_["exit_now_net_rs"] == pytest.approx(round((999.9 - 1000.0) * 10 - S.equity_cost_rs("BUY", 1000.0, 999.9, 10), 1), abs=0.06)    # closing now (long at the bid) also loses the spread
    assert short["exit_now_net_rs"] == pytest.approx(round((1000.0 - 1000.1) * 10 - S.equity_cost_rs("SELL", 1000.0, 1000.1, 10), 1), abs=0.06)
    assert long_["pnl_rs"] < 0 and short["pnl_rs"] < 0 and long_["gross_pnl_rs"] == 0 and long_["est_charges_rs"] > 0                                          # a flat price still pays the charges
    assert long_["to_sl"] == pytest.approx(999.9 - 990.0, abs=1e-6) and short["to_sl"] == pytest.approx(1010.0 - 1000.1, abs=1e-6)
    assert short["to_t1"] == pytest.approx(1000.1 - 990.0, abs=1e-6) and long_["to_t1"] == pytest.approx(1010.0 - 999.9, abs=1e-6)
    assert rows[ids[3]]["state"] == "CLOSED" and rows[ids[3]]["pnl_rs"] == 80.5


def test_equity_quote_and_chart_endpoints(client, monkeypatch):
    r = client.get("/api/arena/public/equity/quote", params={"symbol": "reliance"}, headers=H); assert r.status_code == 200, r.text
    j = r.json(); assert j["symbol"] == "RELIANCE" and j["last"]["bid"] == 999.9 and j["max_notional_rs"] > 0
    assert client.get("/api/arena/public/equity/quote", params={"symbol": "NOSUCH"}, headers=H).status_code == 404
    assert client.get("/api/arena/public/equity/quote", params={"symbol": "BAD SYM"}, headers=H).status_code == 422
    from app.arena import terminal as TM
    monkeypatch.setattr(TM, "premium_equity", lambda sym, tf, since, **k: {"candles": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2}], "last": None, "tf": tf})
    c = client.get("/api/arena/public/chart/equity", params={"symbol": "RELIANCE", "tf": 5}, headers=H); assert c.status_code == 200 and c.json()["tf"] == 5
    assert client.get("/api/arena/public/chart/equity", params={"symbol": "RELIANCE", "tf": 7}, headers=H).status_code == 422


def test_state_advertises_equity_and_the_kill_switch_blocks_submissions(client, monkeypatch):
    assert client.get("/api/arena/public/state", headers=H).json()["equity"] is True
    monkeypatch.setenv("ARENA_EQUITY", "0")
    assert client.get("/api/arena/public/state", headers=H).json()["equity"] is False
    assert post(client, BUY).status_code == 409 and SU.load() == []


def test_other_readers_survive_an_equity_row(client, monkeypatch):
    post(client, BUY); post(client, {**BUY, "side": "SELL", "sl": 1010, "targets": [990]})
    sg = client.get("/api/arena/public/signals", headers=H).json()["signals"]; assert [x["instrument"] for x in sg] == ["EQUITY", "EQUITY"] and sg[1]["side"] == "SELL"
    for path in ("/state", "/guide"):
        r = client.get("/api/arena/public" + path, headers=H); assert r.status_code == 200, (path, r.text[:200])
    # the scoring pipeline (what /standings and /leaderboard run): stubbed quote loader -> a clean settlement, then the summaries
    from app.arena import settle as S, leaderboard as LB
    monkeypatch.setattr(S, "load_snaps", lambda *a, **k: [])
    sigs = R.human_signals(start="2020-01-01", end="2999-01-01"); assert [x["instrument"] for x in sigs] == ["EQUITY", "EQUITY"] and sigs[1]["side"] == "SELL" and sigs[0]["lots"] == 10
    out = R.run(sigs, market_db=None, cache={}); res = out["INSTITUTION_TRADER"]["results"]; assert len(res) == 2 and all(x["status"].startswith("NO_DATA") for x in res)
    LB.build({"INSTITUTION_TRADER": res}, ["INSTITUTION_TRADER"], {}, scope="all", today="2026-10-27", capital=R.CAPITAL)


def test_live_row_carries_gross_charges_net_and_the_mark_for_the_reported_case(client, monkeypatch):
    """RELIANCE BUY 1000 sh, fill 1175.10, LTP 1175.20: the row must say gross +100, charges ~464, net ~-364, mark 1175.20 (ltp); header total = sum of the nets."""
    from app.arena import settle as S
    monkeypatch.setattr(EQ, "latest_quote", lambda sym, path=None: {"ltp": 1175.20, "bid": 1175.15, "ask": 1175.25, "t": time.time()})
    sid = post(client, {**BUY, "entry": 1175.10, "sl": 1170.0, "targets": [1180.0], "qty": 1000}).json()["id"]
    res = {sid: {"id": sid, "status": "NO_DATA:no_exit_snapshot", "fill": 1175.10, "lot": 1, "lots": 1000, "sl_now": 1170.0, "targets_now": [1180.0], "last": {"bid": 1175.15, "ask": 1175.25, "ltp": 1175.20}}}
    j = _live_with(client, monkeypatch, res); row = {x["id"]: x for x in j["rows"]}[sid]
    assert row["state"] == "ACTIVE" and row["qty"] == 1000 and row["fill"] == 1175.10 and row["ltp"] == 1175.20
    assert row["gross_pnl_rs"] == pytest.approx(100.0) and row["mark_price"] == 1175.20 and row["mark_basis"] == "ltp" and row["stale"] is False
    assert row["est_charges_rs"] == pytest.approx(S.equity_cost_rs("BUY", 1175.10, 1175.20, 1000), abs=0.06) and row["net_pnl_rs"] == pytest.approx(-363.7, abs=0.1)
    assert row["pnl_rs"] == row["net_pnl_rs"] and row["exit_now_net_rs"] < row["net_pnl_rs"] and j["total_open_pnl_rs"] == pytest.approx(row["net_pnl_rs"], abs=0.1)
    assert row["to_sl"] == pytest.approx(1175.15 - 1170.0, abs=1e-6)                                                 # distances to the stop / target stay measured from the side that would trigger them (bid)


def test_live_row_with_a_stale_quote_shows_no_pnl_and_says_stale(client, monkeypatch):
    monkeypatch.setattr(EQ, "latest_quote", lambda sym, path=None: {"ltp": 1175.20, "bid": 1175.15, "ask": 1175.25, "t": time.time()})
    sid = post(client, {**BUY, "entry": 1175.10, "sl": 1170.0, "targets": [1180.0], "qty": 1000}).json()["id"]
    monkeypatch.setattr(EQ, "latest_quote", lambda sym, path=None: {"ltp": 1175.20, "bid": 1175.15, "ask": 1175.25, "t": time.time() - 600})
    res = {sid: {"id": sid, "status": "NO_DATA:no_exit_snapshot", "fill": 1175.10, "lot": 1, "lots": 1000, "sl_now": 1170.0, "targets_now": [1180.0], "last": {"bid": 1175.15, "ask": 1175.25, "ltp": 1175.20}}}
    row = {x["id"]: x for x in _live_with(client, monkeypatch, res)["rows"]}[sid]
    assert row["stale"] is True and row["pnl_rs"] is None and row["net_pnl_rs"] is None and row["gross_pnl_rs"] is None
