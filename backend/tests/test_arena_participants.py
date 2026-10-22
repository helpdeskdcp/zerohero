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
    assert client.post("/api/arena/public/submit", json=body, headers={"X-Arena-Token": T1}).status_code == 422                                                                    # a normal profile still needs its own stop and targets
    names = [x["participant"] for x in client.get("/api/arena/public/leaderboard?scope=all", headers={"X-Arena-Token": T1}).json()["rows"]]; assert "TEST_PROFILE" not in names           # hidden test profile
    pv = client.get("/api/arena/public/risk-preview?underlying=NIFTY&option_type=CE&strike=23000&entry=160&expiry=27OCT2026", headers=H2).json()
    assert pv["sl"] == 126.1 and pv["targets"] == [193.9, 227.8, 261.7] and len(SU.load()) == 2                                                                                  # preview records nothing
