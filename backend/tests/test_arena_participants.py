import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.arena import api as A, run as R, submit as SU

T1, T2 = "tok-mohit-" + "a" * 20, "tok-neeraj-" + "b" * 20
BODY = {"underlying": "NIFTY", "expiry": "27OCT2026", "strike": 23000, "option_type": "CE", "entry_mode": "ABOVE", "entry": 100, "sl": 85, "targets": [120, 140], "participant": "HACKER"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(SU, "PATH", str(tmp_path / "h.jsonl"))
    monkeypatch.setattr(A, "PARTICIPANTS_FILE", str(tmp_path / "no_participants.json"))                      # tests never read the real participants file
    monkeypatch.setenv("ARENA_TOKEN_HUMAN", T1); monkeypatch.setenv("ARENA_HUMAN_NAME", "INSTITUTION_TRADER")
    monkeypatch.setenv("ARENA_EXTRA_PARTICIPANTS", "NEERAJ_JOSHI"); monkeypatch.setenv("ARENA_TOKEN_NEERAJ_JOSHI", T2)
    monkeypatch.setenv("ARENA_START", "2999-01-01"); monkeypatch.setenv("ARENA_END", "2999-02-01"); monkeypatch.delenv("ARENA_PUBLIC", raising=False)
    from app.arena import contracts as _CT
    monkeypatch.setattr(_CT, "DB", str(tmp_path / "none.db")); _CT._cache.clear()
    A._hits.clear(); A._cache.update(t=0.0, v=None, by={})
    app = FastAPI(); app.include_router(A.router); return TestClient(app)


def H(t): return {"X-Arena-Token": t}


def test_each_token_is_its_own_participant_and_cannot_see_the_other(client):
    assert client.get("/api/arena/public/state", headers=H(T1)).json()["participant"] == "INSTITUTION_TRADER"
    assert client.get("/api/arena/public/state", headers=H(T2)).json()["participant"] == "NEERAJ_JOSHI"
    assert client.get("/api/arena/public/state", headers=H("nope")).status_code == 401
    a = client.post("/api/arena/public/submit", json=BODY, headers=H(T1)).json(); b = client.post("/api/arena/public/submit", json={**BODY, "strike": 23100}, headers=H(T2)).json()
    assert a["id"] != b["id"]
    s1 = client.get("/api/arena/public/signals", headers=H(T1)).json()["signals"]; s2 = client.get("/api/arena/public/signals", headers=H(T2)).json()["signals"]
    assert [x["id"] for x in s1] == [a["id"]] and [x["id"] for x in s2] == [b["id"]]               # never mixed
    recs = SU.load(); assert {r["id"]: r["participant"] for r in recs} == {a["id"]: "INSTITUTION_TRADER", b["id"]: "NEERAJ_JOSHI"}
    # a participant cannot manage the other one's signal
    assert client.post("/api/arena/public/amend", json={"signal_id": b["id"], "action": "EXIT_NOW"}, headers=H(T1)).status_code == 422
    assert client.post("/api/arena/public/amend", json={"signal_id": b["id"], "action": "EXIT_NOW"}, headers=H(T2)).status_code == 200


def test_participant_correction_moves_a_manual_signal_only_and_scoring_follows(client, tmp_path):
    a = client.post("/api/arena/public/submit", json=BODY, headers=H(T1)).json()                  # submitted with the wrong token (testing)
    SU.submit_participant_correction(a["id"], "neeraj_joshi", "tester: second institution member")
    assert [x["id"] for x in client.get("/api/arena/public/signals", headers=H(T2)).json()["signals"]] == [a["id"]]
    assert client.get("/api/arena/public/signals", headers=H(T1)).json()["signals"] == []
    assert R.human_signals(include_practice=True)[0]["participant"] == "NEERAJ_JOSHI"
    assert client.post("/api/arena/public/amend", json={"signal_id": a["id"], "action": "CANCEL"}, headers=H(T2)).status_code == 200      # events follow the effective owner
    ch = SU.submit({**BODY, "participant": "INSTITUTION_TRADER", "note": "via channel post 7"}, practice=True)
    with pytest.raises(SU.SubmissionError): SU.submit_participant_correction(ch["id"], "NEERAJ_JOSHI", "no")                          # channel signals cannot be re-attributed


def test_risk_module_profile_may_leave_stop_and_targets_blank_others_may_not(client, monkeypatch, tmp_path):
    import hashlib, json
    from app import sl_detector as D
    tok = "riskmod-" + "r" * 20
    (tmp_path / "pf.json").write_text(json.dumps({"TEST_PROFILE": {"display": "Test (entry) + ZeroHero stop/targets", "token_sha256": hashlib.sha256(tok.encode()).hexdigest(), "risk_module": True, "hidden": True}}))
    monkeypatch.setattr(A, "PARTICIPANTS_FILE", str(tmp_path / "pf.json")); A._cache.update(by={}, lb={})
    monkeypatch.setattr(D, "prior_ltps", lambda *a, **k: [150.0, 140.0, 130.0])
    body = {"underlying": "NIFTY", "expiry": "27OCT2026", "strike": 23000, "option_type": "CE", "entry_mode": "ABOVE", "entry": 160}
    H2 = {"X-Arena-Token": tok}
    assert client.get("/api/arena/public/state", headers=H2).json()["risk_module"] is True
    r = client.post("/api/arena/public/submit", json=body, headers=H2); assert r.status_code == 200, r.text
    j = r.json(); assert j["sl"] == 126.1 and j["targets"] == [193.9, 227.8, 261.7]
    rec = SU.load()[-1]; assert rec["participant"] == "TEST_PROFILE" and rec["valid_sec"] == 900 and "risk module" in rec["note"]
    keep = client.post("/api/arena/public/submit", json={**body, "sl": 140, "targets": [200]}, headers=H2).json(); assert keep["sl"] == 140.0 and keep["targets"] == [200.0]          # what the participant gives is kept
    assert client.get("/api/arena/public/state", headers={"X-Arena-Token": T1}).json()["risk_module"] is False
    assert client.post("/api/arena/public/submit", json=body, headers={"X-Arena-Token": T1}).status_code == 200          # stop and targets are optional for every profile now (the entry-only profile gets them filled by the module)
    names = [x["participant"] for x in client.get("/api/arena/public/leaderboard?scope=all", headers={"X-Arena-Token": T1}).json()["rows"]]; assert "TEST_PROFILE" not in names           # hidden test profile
    n_rows = len(SU.load())
    pv = client.get("/api/arena/public/risk-preview?underlying=NIFTY&option_type=CE&strike=23000&entry=160&expiry=27OCT2026", headers=H2).json()
    assert pv["sl"] == 126.1 and pv["targets"] == [193.9, 227.8, 261.7] and len(SU.load()) == n_rows                                                                                  # preview records nothing


def test_live_endpoint_waiting_active_closed_and_the_open_pnl(client, monkeypatch):
    import time as _t
    from app.arena import contracts as CT, run as RR
    now = _t.time()
    mk = lambda i, mode, entry, t=now - 60, **k: {"id": i, "participant": "INSTITUTION_TRADER", "underlying": "NIFTY", "expiry": "2026-10-27", "strike": 23000.0, "option_type": "CE", "entry_mode": mode, "entry": entry, "entry_hi": entry,
                                                     "sl": entry - 20, "targets": [entry + 20, entry + 40], "t": t, "events": [], **k}
    sigs = [mk("W", "LIMIT", 100.0), mk("A", "NEAR", 100.0), mk("C", "NEAR", 100.0), mk("E", "LIMIT", 100.0, valid_sec=30)]
    res = {"W": {"id": "W", "status": "NOT_TRIGGERED", "valid_until": None}, "A": {"id": "A", "status": "NO_DATA:no_exit_snapshot", "fill": 100.0, "lot": 65, "sl_now": 80.0, "targets_now": [120.0, 140.0], "last": {}},
           "C": {"id": "C", "status": "SETTLED", "fill": 100.0, "exit": 120.0, "exit_reason": "TARGET", "net_rs": 1200.0}, "E": {"id": "E", "status": "NOT_TRIGGERED", "valid_until": now - 30}}
    monkeypatch.setattr(RR, "human_signals", lambda *a, **k: sigs); monkeypatch.setattr(A.R, "human_signals", lambda *a, **k: sigs)
    monkeypatch.setattr(A.R, "run", lambda s, **k: {"INSTITUTION_TRADER": {"results": [res[x["id"]] for x in s], "summary": {}}})
    monkeypatch.setattr(CT, "latest_quote", lambda *a, **k: {"ltp": 110.0, "bid": 109.5, "ask": 110.5, "t": _t.time() - 2})
    monkeypatch.setattr("app.market_calendar.is_trading", lambda *a, **k: True)
    A._cache.update(by={}, lb={}, live={})
    j = client.get("/api/arena/public/live", headers={"X-Arena-Token": T1}).json(); by = {r["id"]: r for r in j["rows"]}
    assert [r["id"] for r in j["rows"]][:2] == ["A", "W"] and by["A"]["state"] == "ACTIVE" and by["W"]["state"] == "WAITING" and by["C"]["state"] == "CLOSED" and by["E"]["state"] == "EXPIRED"
    assert "ask to come down to 100" in by["W"]["waiting_note"] and by["A"]["ltp"] == 110.0 and 0 <= by["A"]["quote_age_s"] < 5
    gross = (109.5 - 100.0) * 65; assert by["A"]["pnl_rs"] < gross and by["A"]["pnl_rs"] > gross - 200 and by["A"]["to_sl"] == 29.5 and by["A"]["to_t1"] == 10.5           # open P&L = mark-to-bid minus estimated costs
    assert j["total_open_pnl_rs"] == by["A"]["pnl_rs"] and j["realised_today_rs"] == 1200.0 and by["C"]["ltp"] is None
    assert client.get("/api/arena/public/live").status_code == 401


def test_normal_profile_may_submit_without_a_stop_loss_but_not_with_a_wrong_one(client):
    body = {"underlying": "NIFTY", "expiry": "27OCT2026", "strike": 23000, "option_type": "CE", "entry_mode": "ABOVE", "entry": 160, "targets": [200]}
    r = client.post("/api/arena/public/submit", json=body, headers={"X-Arena-Token": T1}); assert r.status_code == 200, r.text
    j = r.json(); assert j["sl"] is None and j["targets"] == [200.0] and SU.load()[-1]["sl"] is None
    sid = j["id"]; ev = client.post("/api/arena/public/amend", json={"signal_id": sid, "action": "AMEND_SL", "sl": 140}, headers={"X-Arena-Token": T1}); assert ev.status_code == 200      # added later, from then on
    assert client.post("/api/arena/public/submit", json={**body, "sl": 170}, headers={"X-Arena-Token": T1}).status_code == 422                                                       # a given stop must be below the entry
    assert client.get("/api/arena/public/live", headers={"X-Arena-Token": T1}).status_code == 200                                                                                    # the live screen copes with a signal that has no stop


def test_default_lots_per_profile_and_the_quantity_freeze_after_the_trade_is_active(client, monkeypatch, tmp_path):
    import hashlib, json
    from app.arena import run as RR
    tok = "mohit2-" + "m" * 20
    (tmp_path / "pf.json").write_text(json.dumps({"BIG_PROFILE": {"display": "Big", "token_sha256": hashlib.sha256(tok.encode()).hexdigest(), "default_lots": 2}}))
    monkeypatch.setattr(A, "PARTICIPANTS_FILE", str(tmp_path / "pf.json")); A._cache.update(by={}, lb={}, live={}); H2 = {"X-Arena-Token": tok}
    st = client.get("/api/arena/public/state", headers=H2).json(); assert st["default_lots"] == 2 and st["max_lots"] == 10
    assert client.get("/api/arena/public/state", headers={"X-Arena-Token": T1}).json()["default_lots"] == 1
    body = {"underlying": "NIFTY", "expiry": "27OCT2026", "strike": 23000, "option_type": "CE", "entry_mode": "ABOVE", "entry": 160}
    assert client.post("/api/arena/public/submit", json=body, headers=H2).json()["lots"] == 2                                          # the profile default
    assert client.post("/api/arena/public/submit", json={**body, "lots": 5}, headers=H2).json()["lots"] == 5                          # chosen at submit
    assert client.post("/api/arena/public/submit", json=body, headers={"X-Arena-Token": T1}).json()["lots"] == 1                      # everybody else: 1 lot
    assert client.post("/api/arena/public/submit", json={**body, "lots": 11}, headers=H2).status_code == 422
    sid = SU.load()[-1]["id"]
    monkeypatch.setattr(A.R, "run", lambda s, **k: {"BIG_PROFILE": {"results": [{"id": sid, "status": "NOT_TRIGGERED"}], "summary": {}}})
    sid2 = [r["id"] for r in SU.load() if r.get("participant") == "BIG_PROFILE"][0]
    ok = client.post("/api/arena/public/amend", json={"signal_id": sid2, "action": "AMEND_LOTS", "lots": 3}, headers=H2); assert ok.status_code == 200 and ok.json()["lots"] == 3        # not active yet: allowed
    monkeypatch.setattr(A.R, "run", lambda s, **k: {"BIG_PROFILE": {"results": [{"id": sid2, "status": "NO_DATA:no_exit_snapshot", "fill": 160.0}], "summary": {}}})
    assert client.post("/api/arena/public/amend", json={"signal_id": sid2, "action": "AMEND_LOTS", "lots": 4}, headers=H2).status_code == 409                                              # active: no change
