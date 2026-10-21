import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import dhan_routes
from app.arena import api as A, compare as CMP, run as R, submit as SU
from app.forward_test import ledger as L

TOK = "t0k-" + "x" * 24
GOOD = {"underlying": "NIFTY", "expiry": "27OCT2026", "strike": 23000, "option_type": "CE", "entry_mode": "ABOVE", "entry": 100, "sl": 85, "targets": [120, 140], "participant": "HACKER"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(SU, "PATH", str(tmp_path / "h.jsonl"))
    monkeypatch.setattr(A, "PARTICIPANTS_FILE", str(tmp_path / "no_participants.json"))                      # tests never read the real participants file
    monkeypatch.setenv("ARENA_TOKEN_HUMAN", TOK); monkeypatch.setenv("ARENA_HUMAN_NAME", "INSTITUTION_TRADER"); monkeypatch.delenv("ARENA_PUBLIC", raising=False)
    monkeypatch.setenv("ARENA_START", "2999-01-01"); monkeypatch.setenv("ARENA_END", "2999-02-01")          # default: PRACTICE
    from app.arena import contracts as _CT
    monkeypatch.setattr(_CT, "DB", str(tmp_path / "no_such_market.db")); _CT._cache.clear()                    # tests never touch the real 12 GB quote DB
    A._hits.clear(); A._cache.update(t=0.0, v=None)
    app = FastAPI(); app.include_router(A.router); return TestClient(app)


H = {"X-Arena-Token": TOK}


def test_auth_required_and_wrong_token_rejected(client):
    assert client.get("/api/arena/public/state").status_code == 401
    assert client.get("/api/arena/public/state", headers={"X-Arena-Token": "nope"}).status_code == 401
    assert client.post("/api/arena/public/submit", json=GOOD).status_code == 401
    r = client.get("/api/arena/public/state", headers=H).json()
    assert r["mode"] == "PRACTICE" and r["participant"] == "INSTITUTION_TRADER" and "NIFTY" in r["allowed_underlyings"]


def test_not_configured_and_kill_switch(client, monkeypatch):
    monkeypatch.setenv("ARENA_TOKEN_HUMAN", ""); assert client.get("/api/arena/public/state", headers=H).status_code == 503
    monkeypatch.setenv("ARENA_TOKEN_HUMAN", TOK); monkeypatch.setenv("ARENA_PUBLIC", "0"); assert client.get("/api/arena/public/state", headers=H).status_code == 404


def test_submit_practice_forces_server_participant_and_hash_chain(client):
    r = client.post("/api/arena/public/submit", json=GOOD, headers=H); assert r.status_code == 200, r.text
    j = r.json(); assert j["practice"] is True and j["id"] == "H00001" and j["expiry"] == "2026-10-27"
    rec = SU.load()[0]; assert rec["participant"] == "INSTITUTION_TRADER" and rec["practice"] is True                      # client-supplied participant ignored
    assert L.verify_chain(SU.PATH)[0]
    assert len(client.get("/api/arena/public/signals", headers=H).json()["signals"]) == 1


@pytest.mark.parametrize("bad", [{"sl": 120}, {"underlying": "TCS"}, {"targets": []}, {"entry": 0}, {"expiry": "soon"}, {"option_type": "XX"}])
def test_invalid_submissions_rejected_422_and_not_stored(client, bad):
    r = client.post("/api/arena/public/submit", json={**GOOD, **bad}, headers=H); assert r.status_code == 422 and SU.load() == []


def test_body_size_and_bad_json(client):
    assert client.post("/api/arena/public/submit", content=b"{" + b" " * 5000 + b"}", headers={**H, "Content-Type": "application/json"}).status_code in (413, 400)
    assert client.post("/api/arena/public/submit", content=b"not json", headers={**H, "Content-Type": "application/json"}).status_code == 400
    assert client.post("/api/arena/public/submit", json=[1, 2], headers=H).status_code == 400


def test_rate_limit(client):
    codes = [client.get("/api/arena/public/state", headers=H).status_code for _ in range(A.RATE_PER_MIN + 3)]
    assert codes[: A.RATE_PER_MIN] == [200] * A.RATE_PER_MIN and set(codes[A.RATE_PER_MIN:]) == {429}


def test_live_mode_requires_open_market_and_scores(client, monkeypatch):
    monkeypatch.setenv("ARENA_START", "2000-01-01"); monkeypatch.setenv("ARENA_END", "2999-01-01")
    from app import market_calendar as mc
    monkeypatch.setattr(mc, "is_trading", lambda ex, *a, **k: False)
    r = client.post("/api/arena/public/submit", json=GOOD, headers=H); assert r.status_code == 409 and SU.load() == []
    monkeypatch.setattr(mc, "is_trading", lambda ex, *a, **k: True)
    r = client.post("/api/arena/public/submit", json=GOOD, headers=H); assert r.status_code == 200 and r.json()["practice"] is False
    monkeypatch.setenv("ARENA_END", "2000-02-01"); assert client.post("/api/arena/public/submit", json=GOOD, headers=H).status_code == 409        # ended


def test_scoring_loader_excludes_practice_and_out_of_window(tmp_path):
    p = str(tmp_path / "h.jsonl"); base = {k: v for k, v in GOOD.items() if k != "participant"}
    day = lambda d: datetime.fromisoformat(f"{d}T05:00:00+00:00").timestamp()
    SU.submit({**base, "participant": "X"}, p, now=day("2026-10-20"), practice=True); SU.submit({**base, "participant": "X"}, p, now=day("2026-10-27"))
    SU.submit({**base, "participant": "X"}, p, now=day("2026-12-01"))
    got = R.human_signals(p, start="2026-10-26", end="2026-11-25"); assert [g["id"] for g in got] == ["H00002"]
    assert len(R.human_signals(p, include_practice=True)) == 3


def test_standings_shape_and_cache(client, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(R, "ai_signals", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or [])
    monkeypatch.setattr(R, "human_signals", lambda *a, **k: [])
    j = client.get("/api/arena/public/standings", headers=H).json()
    assert {"ai", "human", "comparison", "caveats", "spread_models", "mode"} <= set(j) and j["comparison"]["verdict"] == "INSUFFICIENT_DAYS" and any("noise" in c or "DRAW" in c for c in j["caveats"])
    client.get("/api/arena/public/standings", headers=H); assert calls["n"] == 1


def test_guide_served(client):
    assert "Institution-side trader guide" in client.get("/api/arena/public/guide", headers=H).json()["markdown"]


# ---------------- auth exemption stays narrow ----------------
def test_only_arena_paths_are_exempt_from_dashboard_auth(monkeypatch):
    monkeypatch.delenv("ARENA_PUBLIC", raising=False)
    for p in ("/api/arena/public/state", "/api/arena/public/submit", "/static/arena.html", "/static/js/arena.js", "/api/dhan/redirect"): assert dhan_routes.is_auth_exempt_path(p), p
    for p in ("/api/arena/admin", "/api/arena", "/static/js/app.js", "/static/pa_of_lab.html", "/api/rules/inventory", "/api/execution/kill", "/api/signals/unified", "/api/arena/publicx/state"): assert not dhan_routes.is_auth_exempt_path(p), p
    monkeypatch.setenv("ARENA_PUBLIC", "0"); assert not dhan_routes.is_auth_exempt_path("/api/arena/public/state") and not dhan_routes.is_auth_exempt_path("/static/arena.html")


def test_arena_api_has_no_order_or_network_code_and_files_exist():
    s = open(A.__file__).read()
    for bad in ("requests", "urllib", "app.execution", "place_order", "subprocess"): assert bad not in s
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for f in ("frontend/static/arena.html", "frontend/static/js/arena.js", "backend/INSTITUTION_ARENA_GUIDE.md"): assert os.path.exists(os.path.join(root, f)), f


# ---------------- comparison ----------------
def _res(days_pnl):
    base = datetime.fromisoformat("2026-10-26T06:00:00+00:00").timestamp()
    return [{"status": "SETTLED", "net_rs": x, "fill_t": base + i * 86400} for i, x in enumerate(days_pnl)]


def test_compare_draw_ahead_and_insufficient():
    assert CMP.compare(_res([100] * 3), _res([0] * 3))["verdict"] == "INSUFFICIENT_DAYS"
    noisy_a, noisy_b = _res([500, -400, 300, -200, 100, 50, -100, 400]), _res([-300, 450, -100, 200, -50, 120, 80, -350])
    assert CMP.compare(noisy_a, noisy_b)["verdict"] == "DRAW"
    assert CMP.compare(_res([300, 310, 290, 305, 295, 300, 310, 290]), _res([-10, -5, -12, -8, -9, -11, -7, -6]))["verdict"] == "A_AHEAD"


# ---------------- management events ----------------
def _sub(client, **kw):
    r = client.post("/api/arena/public/submit", json={**GOOD, "entry_mode": "LIMIT", **kw}, headers=H); assert r.status_code == 200, r.text; return r.json()["id"]


def test_amend_flow_events_listed_and_validated(client):
    sid = _sub(client)
    assert client.post("/api/arena/public/amend", json={"signal_id": sid, "action": "AMEND_SL", "sl": 95}).status_code == 401
    r = client.post("/api/arena/public/amend", json={"signal_id": sid, "action": "AMEND_TARGETS", "targets": [150, 180]}, headers=H); assert r.status_code == 200 and r.json()["practice"] is True and r.json()["id"] == "E00001"
    assert client.post("/api/arena/public/amend", json={"signal_id": sid, "action": "TRAIL_SL", "trail_pct": 5}, headers=H).status_code == 200
    assert client.post("/api/arena/public/amend", json={"signal_id": sid, "action": "EXIT_NOW"}, headers=H).status_code == 200
    for bad in ({"action": "AMEND_SL", "sl": 0}, {"action": "HACK"}, {"signal_id": "H99999", "action": "CANCEL"}, {"action": "TRAIL_SL", "trail_points": 1, "trail_pct": 5}):
        assert client.post("/api/arena/public/amend", json={"signal_id": sid, "action": "CANCEL", **bad}, headers=H).status_code == 422
    sig = client.get("/api/arena/public/signals", headers=H).json()["signals"][0]
    assert sig["entry_mode"] == "LIMIT" and [e["action"] for e in sig["events"]] == ["AMEND_TARGETS", "TRAIL_SL", "EXIT_NOW"]
    assert L.verify_chain(SU.PATH)[0] and len(client.get("/api/arena/public/signals", headers=H).json()["signals"]) == 1                      # events are not signals


def test_amend_live_mode_needs_open_market_and_ended_rejects(client, monkeypatch):
    sid = _sub(client)                                                                                            # practice signal
    monkeypatch.setenv("ARENA_START", "2000-01-01"); monkeypatch.setenv("ARENA_END", "2999-01-01")
    from app import market_calendar as mc
    monkeypatch.setattr(mc, "is_trading", lambda ex, *a, **k: False)
    assert client.post("/api/arena/public/amend", json={"signal_id": sid, "action": "EXIT_NOW"}, headers=H).status_code == 409
    monkeypatch.setattr(mc, "is_trading", lambda ex, *a, **k: True)
    assert client.post("/api/arena/public/amend", json={"signal_id": sid, "action": "EXIT_NOW"}, headers=H).status_code == 200
    monkeypatch.setenv("ARENA_END", "2000-02-01"); assert client.post("/api/arena/public/amend", json={"signal_id": sid, "action": "EXIT_NOW"}, headers=H).status_code == 409


def test_events_flow_into_scored_signals_and_settlement(tmp_path):
    from app.arena import settle as S
    p = str(tmp_path / "h.jsonl"); day = lambda hhmm: datetime.fromisoformat(f"2026-10-27T{hhmm}:00+05:30").timestamp()
    base = {k: v for k, v in GOOD.items() if k != "participant"}
    a = SU.submit({**base, "participant": "INSTITUTION_TRADER", "entry_mode": "LIMIT", "entry": 100, "sl": 90, "targets": [120]}, p, now=day("10:00"))
    SU.submit_event({"signal_id": a["id"], "action": "AMEND_TARGETS", "targets": [150]}, "INSTITUTION_TRADER", p, now=day("10:04"))
    sig = R.human_signals(p, start="2026-10-26", end="2026-11-25")[0]
    sn = [{"t": day(t), "ltp": l, "bid": b, "ask": k} for t, l, b, k in [("10:01", 100, 99, 100), ("10:05", 125, 124, 126), ("10:10", 151, 150, 152), ("10:15", 140, 139, 141)]]
    r = S.settle(sig, sn, half_spread=0.01); assert r["status"] == "SETTLED" and r["exit"] == 150 and r["exit_reason"] == "TARGET" and r["events_applied"] == 1


# ---------------- contract validation ----------------
def _mkdb(tmp_path):
    import sqlite3
    p = str(tmp_path / "m.db"); c = sqlite3.connect(p)
    c.execute("CREATE TABLE quote_snapshots (id INTEGER PRIMARY KEY, received_ts TEXT, symbol TEXT, kind TEXT, expiry TEXT, strike REAL, option_type TEXT, session_date_ist TEXT, ltp REAL, bid REAL, ask REAL)")
    rows = [("2026-10-20T13:20:00Z", "CRUDEOIL", "OPTION", "17NOV2026", k, ot, "2026-10-20", ltp, ltp - 1, ltp + 1) for k, ot, ltp in [(9400.0, "CE", 578.0), (9500.0, "CE", 534.0), (9500.0, "PE", 516.0), (9600.0, "CE", 490.0)]]
    c.executemany("INSERT INTO quote_snapshots (received_ts,symbol,kind,expiry,strike,option_type,session_date_ist,ltp,bid,ask) VALUES (?,?,?,?,?,?,?,?,?,?)", rows); c.commit(); c.close(); return p


def test_check_flags_wrong_expiry_strike_and_premium(tmp_path):
    from app.arena import contracts as CT
    p = _mkdb(tmp_path); CT._cache.clear()
    r = CT.check("CRUDEOIL", "2026-10-27", 8800.0, "CE", 150.0, "LIMIT", day="2026-10-20", path=p)                     # the real-world mistake: NIFTY's expiry, wrong premium level
    assert not r["found"] and "17NOV2026" not in r["problems"][0] and "2026-11-17" in r["problems"][0] and r["available_expiries"] == ["2026-11-17"]
    r = CT.check("CRUDEOIL", "2026-11-17", 8800.0, "CE", 150.0, "LIMIT", day="2026-10-20", path=p); assert not r["found"] and "not captured" in r["problems"][0] and r["strike_range"] == [9400.0, 9600.0]
    ok = CT.check("CRUDEOIL", "2026-11-17", 9500.0, "CE", 534.0, "NEAR", day="2026-10-20", path=p); assert ok["found"] and ok["problems"] == [] and ok["warnings"] == [] and ok["last"]["ltp"] == 534.0
    far = CT.check("CRUDEOIL", "2026-11-17", 9500.0, "CE", 150.0, "NEAR", day="2026-10-20", path=p); assert far["found"] and any("far from the last traded price" in w for w in far["warnings"])
    lim = CT.check("CRUDEOIL", "2026-11-17", 9500.0, "CE", 150.0, "LIMIT", day="2026-10-20", path=p); assert any("never fill" in w for w in lim["warnings"])


def test_submit_practice_warns_live_rejects_unscorable_and_contract_endpoints(client, tmp_path, monkeypatch):
    from app.arena import contracts as CT
    p = _mkdb(tmp_path); CT._cache.clear(); monkeypatch.setattr(CT, "DB", p)
    bad = {**GOOD, "underlying": "CRUDEOIL", "expiry": "27OCT2026", "strike": 8800, "option_type": "CE", "entry_mode": "LIMIT", "entry": 150, "sl": 125, "targets": [210, 240]}
    r = client.post("/api/arena/public/submit", json=bad, headers=H); assert r.status_code == 200 and r.json()["practice"] is True and r.json()["warnings"] and "2026-11-17" in " ".join(r.json()["warnings"])
    monkeypatch.setenv("ARENA_START", "2000-01-01"); monkeypatch.setenv("ARENA_END", "2999-01-01")
    from app import market_calendar as mc; monkeypatch.setattr(mc, "is_trading", lambda ex, *a, **k: True)
    r = client.post("/api/arena/public/submit", json=bad, headers=H); assert r.status_code == 422 and "unscorable contract" in r.json()["detail"]
    good = {**bad, "expiry": "17NOV2026", "strike": 9500, "entry_mode": "NEAR", "entry": 534, "sl": 480, "targets": [600]}
    r = client.post("/api/arena/public/submit", json=good, headers=H); assert r.status_code == 200 and r.json()["warnings"] == [] and r.json()["last_quote"]["ltp"] == 534.0
    j = client.get("/api/arena/public/contracts?underlying=crudeoil", headers=H).json(); assert j["expiries"][0]["raw"] == "17NOV2026" and j["expiries"][0]["strike_min"] == 9400.0
    q = client.get("/api/arena/public/quote?underlying=CRUDEOIL&expiry=17NOV2026&strike=9500&option_type=ce", headers=H).json(); assert q["last"]["ltp"] == 534.0
    assert client.get("/api/arena/public/contracts?underlying=TCS", headers=H).status_code == 422 and client.get("/api/arena/public/contracts?underlying=NIFTY").status_code == 401


def test_tight_stop_inside_the_spread_is_warned(tmp_path):
    from app.arena import contracts as CT
    p = _mkdb(tmp_path); CT._cache.clear()                                                                  # fixture quotes: bid = ltp-1, ask = ltp+1 (spread 2)
    r = CT.check("CRUDEOIL", "2026-11-17", 9500.0, "CE", 534.0, "NEAR", sl=533.0, day="2026-10-20", path=p); assert any("likely to be hit immediately" in w for w in r["warnings"])
    r = CT.check("CRUDEOIL", "2026-11-17", 9500.0, "CE", 534.0, "NEAR", sl=480.0, day="2026-10-20", path=p); assert not any("hit immediately" in w for w in r["warnings"])


# ---------------- window decides scoring (start moved earlier by agreement) ----------------
def test_window_decides_not_the_stored_practice_flag_and_excluded_ids(tmp_path):
    p = str(tmp_path / "h.jsonl"); day = lambda d: datetime.fromisoformat(f"{d}T13:00:00+00:00").timestamp()
    base = {k: v for k, v in GOOD.items() if k != "participant"}
    a = SU.submit({**base, "participant": "X"}, p, now=day("2026-10-20"), practice=True)              # stored as practice when it was submitted
    b = SU.submit({**base, "participant": "X"}, p, now=day("2026-10-20"), practice=True)
    c = SU.submit({**base, "participant": "X"}, p, now=day("2026-10-19"), practice=True)              # before the (moved) start
    got = R.human_signals(p, start="2026-10-20", end="2026-11-25", exclude_ids={a["id"]})
    assert [g["id"] for g in got] == [b["id"]]                                                          # a excluded by agreement, c outside the window, b counts despite its stored practice flag
    assert [g["id"] for g in R.human_signals(p)] == []                                                  # no window: practice entries stay excluded
    assert len(R.human_signals(p, include_practice=True)) == 3


def test_signals_endpoint_reports_scored_and_excluded(client, monkeypatch):
    monkeypatch.setenv("ARENA_START", "2000-01-01"); monkeypatch.setenv("ARENA_END", "2999-01-01"); monkeypatch.setenv("ARENA_EXCLUDE_IDS", "H00001")
    from app import market_calendar as mc; monkeypatch.setattr(mc, "is_trading", lambda ex, *a, **k: True)
    for _ in range(2): assert client.post("/api/arena/public/submit", json=GOOD, headers=H).status_code == 200
    j = client.get("/api/arena/public/signals", headers=H).json()["signals"]
    assert [(s["id"], s["scored"], s["excluded"]) for s in j] == [("H00001", False, True), ("H00002", True, False)]
