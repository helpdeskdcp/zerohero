import json
from types import SimpleNamespace as NS
from datetime import datetime, timezone

import pytest

from app.arena import channel_ingest as CI, submit as SU, tg_reader as TR
from tests.test_channel_parse import HERO


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(CI, "pick_expiry", lambda u, d, p=None: "2026-10-27")
    monkeypatch.setattr(CI.CT, "check", lambda *a, **k: {"problems": [], "warnings": [], "last": None})
    monkeypatch.setattr(CI, "_market_open", lambda u: True)
    monkeypatch.setattr(TR, "QUARANTINE", str(tmp_path / "q.jsonl")); monkeypatch.setattr(TR, "DIR", str(tmp_path)); monkeypatch.setattr(TR, "STATUS", str(tmp_path / "st.json"))
    return dict(st={"seen": {}, "by_msg": {}, "last_by_chat": {}}, kw=dict(participant="INSTITUTION_TRADER", mode_fn=lambda: "ARENA_LIVE", ledger=str(tmp_path / "l.jsonl"), log=str(tmp_path / "log.jsonl")), tmp=tmp_path)


def P(mid, text): return {"chat": {"id": -1001316378442, "type": "channel"}, "message_id": mid, "date": 1760000000, "text": text}


def test_log_only_by_default_never_touches_ledger(env, monkeypatch):
    monkeypatch.delenv("TG_INGEST_SUBMIT", raising=False)
    assert TR.process(P(1, HERO), env["st"], **env["kw"])["outcome"] == "WOULD_SUBMIT"
    assert SU.load(env["kw"]["ledger"]) == []


def test_submit_flag_enables_ledger_write(env, monkeypatch):
    monkeypatch.setenv("TG_INGEST_SUBMIT", "1")
    assert TR.process(P(1, HERO), env["st"], **env["kw"])["outcome"] == "SUBMITTED" and len(SU.load(env["kw"]["ledger"])) == 1
    assert TR.process(P(1, HERO), env["st"], **env["kw"])["outcome"] == "DUPLICATE"


def test_edits_never_resubmit_and_ambiguous_is_quarantined(env, monkeypatch):
    monkeypatch.setenv("TG_INGEST_SUBMIT", "1")
    assert TR.process(P(2, HERO), env["st"], edited=True, **env["kw"])["outcome"] == "EDIT_IGNORED" and SU.load(env["kw"]["ledger"]) == []
    assert TR.process(P(3, "NIFTY 22400 CE buy somewhere around 75 target 120"), env["st"], **env["kw"])["outcome"] == "IGNORED_NOT_A_SIGNAL"
    q = [json.loads(x) for x in open(TR.QUARANTINE)]; assert len(q) == 2 and "edited" in q[0]["why"] and "did not parse" in q[1]["why"]
    assert TR.process(P(4, "Good morning"), env["st"], **env["kw"])["outcome"] == "IGNORED_NOT_A_SIGNAL" and len(open(TR.QUARANTINE).readlines()) == 2


def test_to_post_and_status_have_no_secrets(env, monkeypatch):
    m = NS(id=9, message="hi", date=datetime(2026, 10, 20, tzinfo=timezone.utc), reply_to=NS(reply_to_msg_id=5))
    p = TR.to_post(-1001316378442, m); assert p["reply_to_message"] == {"message_id": 5} and p["message_id"] == 9
    monkeypatch.setenv("TG_SOURCE_CHANNEL_IDS", "-1001316378442,-1002057935053"); TR.write_status(connected=True, last_event=1.0)
    s = TR.public_status(); assert s["source_channels"] == 2 and "TG_API_HASH" not in json.dumps(s)
