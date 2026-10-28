"""Phase 5: server-sent events for a participant's own orders, driven by the ledger file (written by ANOTHER process, the Telegram reader)."""
import json
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.arena import api as A, submit as SU

TOK = "t0k-" + "z" * 24
H = {"X-Arena-Token": TOK}


def test_ledger_tail_reads_only_complete_new_lines(tmp_path):
    p = tmp_path / "l.jsonl"; assert A.ledger_tail(str(p), 0) == ([], 0)
    p.write_text(json.dumps({"id": 1}) + "\n" + json.dumps({"id": 2}) + "\n" + '{"id": 3')                 # last line half written
    recs, off = A.ledger_tail(str(p), 0); assert [r["id"] for r in recs] == [1, 2] and A.ledger_tail(str(p), off) == ([], off)
    with open(p, "a") as f: f.write("}\n")
    recs, off2 = A.ledger_tail(str(p), off); assert [r["id"] for r in recs] == [3] and off2 == p.stat().st_size
    p.write_text(""); assert A.ledger_tail(str(p), off2) == ([], 0)                                           # file replaced / truncated: restart from its end


def test_notice_names_only_this_participants_changes_and_carries_no_prices():
    mine = {"type": "HUMAN_EVENT", "id": "E1", "signal_id": "H1", "participant": "ME", "action": "AMEND_SL", "sl": 12.0, "server_ts": 5.0}
    n = A.stream_notice(mine, "ME"); assert n == {"type": "HUMAN_EVENT", "id": "E1", "signal_id": "H1", "action": "AMEND_SL", "t": 5.0} and "sl" not in n
    assert A.stream_notice({**mine, "participant": "OTHER"}, "ME") is None and A.stream_notice({"type": "HUMAN_STATUS_CORRECTION", "signal_id": "H1"}, "ME") is None
    assert A.stream_notice({"type": "HUMAN_SIGNAL", "id": "H2", "participant": "ME", "entry": 100}, "ME")["signal_id"] == "H2"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(SU, "PATH", str(tmp_path / "h.jsonl")); monkeypatch.setattr(A, "PARTICIPANTS_FILE", str(tmp_path / "none.json"))
    monkeypatch.setenv("ARENA_TOKEN_HUMAN", TOK); monkeypatch.setenv("ARENA_HUMAN_NAME", "INSTITUTION_TRADER"); monkeypatch.delenv("ARENA_PUBLIC", raising=False)
    monkeypatch.setenv("ARENA_START", "2020-01-01"); monkeypatch.setenv("ARENA_END", "2999-01-01")
    A._hits.clear(); A._streams.clear(); app = FastAPI(); app.include_router(A.router); return TestClient(app)


def test_stream_announces_a_write_from_another_process_and_releases_its_slot(client, monkeypatch, tmp_path):
    monkeypatch.setattr(A, "STREAM_MAX_SEC", 2.0); path = tmp_path / "h.jsonl"

    def writer():
        time.sleep(0.6)
        with open(path, "a") as f:
            f.write(json.dumps({"type": "HUMAN_SIGNAL", "id": "H00001", "participant": "INSTITUTION_TRADER", "server_ts": 1.0}) + "\n")
            f.write(json.dumps({"type": "HUMAN_SIGNAL", "id": "H00002", "participant": "SOMEONE_ELSE", "server_ts": 2.0}) + "\n")
    th = threading.Thread(target=writer); th.start()
    with client.stream("GET", "/api/arena/public/signals/stream", headers=H) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream") and r.headers["x-accel-buffering"] == "no"
        ev = [json.loads(l[5:]) for l in r.iter_lines() if l.startswith("data:")]
    th.join()
    assert ev[0].get("hello") and [e["signal_id"] for e in ev[1:]] == ["H00001"]                                # the other participant's order is never announced
    assert A._streams[("sig", "INSTITUTION_TRADER")] == 0


def test_stream_needs_the_token_and_limits_open_streams(client):
    assert client.get("/api/arena/public/signals/stream").status_code == 401
    A._streams[("sig", "INSTITUTION_TRADER")] = 4; assert client.get("/api/arena/public/signals/stream", headers=H).status_code == 429
