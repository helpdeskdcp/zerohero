import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.arena import api as A, run as R, submit as SU

T1, T2 = "tok-mohit-" + "a" * 20, "tok-neeraj-" + "b" * 20
BODY = {"underlying": "NIFTY", "expiry": "27OCT2026", "strike": 23000, "option_type": "CE", "entry_mode": "ABOVE", "entry": 100, "sl": 85, "targets": [120, 140], "participant": "HACKER"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(SU, "PATH", str(tmp_path / "h.jsonl"))
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
