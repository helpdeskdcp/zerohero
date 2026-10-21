import hashlib
import json
import time
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.arena import api as A, leaderboard as LB, submit as SU, tg_reader as TR

TODAY = "2026-10-21"
T = lambda h, m: datetime.fromisoformat(f"{TODAY}T{h:02d}:{m:02d}:00+05:30").timestamp()


def res(t, net, status="SETTLED"):
    r = {"t": t, "status": status}
    if status == "SETTLED": r.update(net_rs=net, gross_rs=net + 10, cost_rs=10, fill_t=t + 60, fill_real_quote=True, exit_real_quote=True)
    return r


def test_ranking_is_by_net_profit_and_split_by_day():
    data = {"A": [res(T(10, 0), 3000), res(T(11, 0), -500)], "B": [res(T(10, 5), 4000)], "C": [res(T(10, 5), -100, "NOT_TRIGGERED")], "OLD": [res(T(10, 0) - 86400, 9999)]}
    d = LB.build(data, ["A", "B", "C", "D", "OLD"], {"A": "Alpha"}, scope="today", today=TODAY)
    assert [(r["participant"], r["rank"], r["net_rs"]) for r in d["rows"][:2]] == [("B", 1, 4000.0), ("A", 2, 2500.0)]           # B's 4000 beats A's 3000-500
    assert {r["participant"]: r["rank"] for r in d["rows"]}["OLD"] is None and [r["display"] for r in d["rows"] if r["participant"] == "A"] == ["Alpha"]   # yesterday's 9999 is not "today"
    assert next(r for r in d["rows"] if r["participant"] == "C")["not_filled"] == 1 and len(d["rows"]) == 5
    assert LB.build(data, ["OLD"], {}, scope="all", today=TODAY)["rows"][0]["rank"] == 1 and len(LB.build(data, [f"P{i}" for i in range(15)], {}, scope="all", today=TODAY)["rows"]) == 10


def test_channel_to_participant_mapping(monkeypatch):
    monkeypatch.setenv("TG_CHANNEL_PARTICIPANTS", "-100111:NEERAJ_JOSHI, -100222:institution_trader")
    assert TR.participant_for(-100111, "X") == "NEERAJ_JOSHI" and TR.participant_for("-100222", "X") == "INSTITUTION_TRADER" and TR.participant_for(-100999, "DEFAULT") == "DEFAULT"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(SU, "PATH", str(tmp_path / "h.jsonl"))
    tok = "dhananjay-" + "z" * 20
    pf = tmp_path / "participants.json"; pf.write_text(json.dumps({"DHANANJAY_DALVI": {"display": "Dhananjay Dalvi", "token_sha256": hashlib.sha256(tok.encode()).hexdigest()}}))
    monkeypatch.setattr(A, "PARTICIPANTS_FILE", str(pf)); monkeypatch.setenv("ARENA_TOKEN_HUMAN", "primary-" + "p" * 20); monkeypatch.setenv("ARENA_HUMAN_NAME", "INSTITUTION_TRADER")
    monkeypatch.delenv("ARENA_EXTRA_PARTICIPANTS", raising=False); monkeypatch.delenv("ARENA_PUBLIC", raising=False)
    monkeypatch.setenv("ARENA_START", "2999-01-01"); monkeypatch.setenv("ARENA_END", "2999-02-01")
    from app.arena import contracts as CT
    monkeypatch.setattr(CT, "DB", str(tmp_path / "none.db")); CT._cache.clear(); A._hits.clear(); A._cache.update(t=0.0, v=None, by={}, lb={})
    app = FastAPI(); app.include_router(A.router); c = TestClient(app); c.tok = tok; return c


def test_runtime_participant_token_works_without_restart_and_leaderboard_lists_everyone(client):
    H = {"X-Arena-Token": client.tok}
    assert client.get("/api/arena/public/state", headers=H).json()["participant"] == "DHANANJAY_DALVI"
    assert client.get("/api/arena/public/state", headers={"X-Arena-Token": "wrong"}).status_code == 401
    d = client.get("/api/arena/public/leaderboard?scope=today", headers=H)
    assert d.status_code == 200 and d.json()["you"] == "DHANANJAY_DALVI"
    names = {r["participant"] for r in d.json()["rows"]}; assert {"ZEROHERO_AI", "INSTITUTION_TRADER", "DHANANJAY_DALVI"} <= names
    assert client.get("/api/arena/public/leaderboard?scope=bad", headers=H).status_code == 422 and client.get("/api/arena/public/leaderboard").status_code == 401



def test_daily_report_script_tolerates_log_rows_without_a_telegram_date():
    import os
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "arena_daily_report.py")).read()
    assert 'r.get("tg_date") and' in src                      # regression: edit-ignored rows have no tg_date and crashed the 23:40 report
