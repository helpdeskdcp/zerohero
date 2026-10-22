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


def P(mid, text, age=5): return {"chat": {"id": -1001316378442, "type": "channel"}, "message_id": mid, "date": int(__import__("time").time()) - age, "text": text}


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


def test_ledger_stamp_is_the_post_time_and_stale_posts_are_not_submitted(env, monkeypatch):
    monkeypatch.setenv("TG_INGEST_SUBMIT", "1")
    p = P(1, HERO, age=600)                                              # posted 10 minutes before the reader saw it
    r = TR.process(p, env["st"], **env["kw"]); assert r["outcome"] == "SUBMITTED" and 590 <= r["latency_sec"] <= 620
    assert abs(SU.load(env["kw"]["ledger"])[0]["server_ts"] - p["date"]) < 1                                   # stamped with the POST time, not the receipt time
    assert TR.process(P(2, HERO, age=2400), env["st"], **env["kw"])["outcome"] == "STALE_NOT_SUBMITTED" and len(SU.load(env["kw"]["ledger"])) == 1


def test_timestamp_correction_is_earlier_only_and_channel_only(env, monkeypatch):
    from app.arena import run as AR
    monkeypatch.setenv("TG_INGEST_SUBMIT", "1")
    led = env["kw"]["ledger"]; TR.process(P(1, HERO, age=0), env["st"], **env["kw"]); rec = SU.load(led)[0]
    with pytest.raises(SU.SubmissionError): SU.submit_ts_correction("H00001", rec["server_ts"] + 5, "later", path=led)          # never later
    c = SU.submit_ts_correction("H00001", rec["server_ts"] - 600, "reader latency", path=led); assert c["type"] == "HUMAN_TS_CORRECTION"
    assert AR.human_signals(led, include_practice=True)[0]["t"] == rec["server_ts"] - 600
    SU.submit({"participant": "INSTITUTION_TRADER", "underlying": "NIFTY", "expiry": "2026-10-27", "strike": 22400, "option_type": "CE", "entry_mode": "LIMIT", "entry": 80, "sl": 50, "targets": [100]}, path=led)
    with pytest.raises(SU.SubmissionError): SU.submit_ts_correction("H00002", rec["server_ts"] - 900, "x", path=led)                    # manual submissions cannot be moved


def test_anonymous_admin_groups_ignore_ordinary_members(monkeypatch):
    monkeypatch.setenv("TG_ANON_ONLY_CHATS", "-1001383836599")
    assert TR.sender_allowed(-1001383836599, None) and TR.sender_allowed(-1001383836599, -1001383836599)               # anonymous admin / the group itself
    assert not TR.sender_allowed(-1001383836599, 123456789)                                                           # an ordinary member
    assert TR.sender_allowed(-1002288564423, 123456789)                                                               # channels are not restricted
