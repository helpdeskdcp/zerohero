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
    monkeypatch.setenv("TG_INGEST_SUBMIT", "1"); monkeypatch.setenv("TG_EDITS", "0")                  # the pre-phase-4 behaviour (phase 4 applies edits: tests/test_tg_edits_deletes.py)
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


# ---------------------------------------------------------------- never lose a signal silently (2026-10-23 incident)
def _alerts(monkeypatch):
    sent = []; monkeypatch.setattr(TR, "send_alert", sent.append); return sent


def test_missing_contract_data_is_retried_and_submitted_at_post_time(env, monkeypatch):
    import time
    monkeypatch.setenv("TG_INGEST_SUBMIT", "1"); sent = _alerts(monkeypatch); holder = {"v": None}
    monkeypatch.setattr(CI, "pick_expiry", lambda u, d, p=None: holder["v"])
    post = P(1, HERO, age=120); key = f"{post['chat']['id']}:1"
    r1 = TR.process(post, env["st"], **env["kw"])
    assert r1["outcome"] == "NO_CAPTURED_EXPIRY" and r1["retry_scheduled"] and SU.load(env["kw"]["ledger"]) == [] and key in env["st"]["pending"] and key not in env["st"]["seen"] and sent == []
    holder["v"] = "2026-10-27"
    out = TR.retry_pending(env["st"], mode_fn=env["kw"]["mode_fn"], default_participant="INSTITUTION_TRADER", ledger=env["kw"]["ledger"], log=env["kw"]["log"])
    assert [o["outcome"] for o in out] == ["SUBMITTED"] and not env["st"]["pending"]
    rec = SU.load(env["kw"]["ledger"])[0]; assert abs(rec["server_ts"] - post["date"]) < 2                    # stamped at the Telegram post time, not at the retry
    assert len(sent) == 1 and sent[0].startswith("Recovered") and TR.retry_pending(env["st"], mode_fn=env["kw"]["mode_fn"], default_participant="X", ledger=env["kw"]["ledger"], log=env["kw"]["log"]) == []
    lines = [json.loads(x) for x in open(env["kw"]["log"])]; assert [x["outcome"] for x in lines] == ["NO_CAPTURED_EXPIRY", "SUBMITTED"] and lines[1]["after_retries"] == 1      # first attempt + final outcome only


def test_unresolved_post_is_dropped_after_30_minutes_and_alerted_once(env, monkeypatch):
    monkeypatch.setenv("TG_INGEST_SUBMIT", "1"); sent = _alerts(monkeypatch); monkeypatch.setattr(CI, "pick_expiry", lambda u, d, p=None: None)
    post = P(2, HERO, age=100); TR.process(post, env["st"], **env["kw"])
    out = TR.retry_pending(env["st"], mode_fn=env["kw"]["mode_fn"], default_participant="X", now=post["date"] + CI.STALE_SEC + 5, ledger=env["kw"]["ledger"], log=env["kw"]["log"])
    assert [o["outcome"] for o in out] == ["STALE_NOT_SUBMITTED"] and not env["st"]["pending"] and len(sent) == 1 and "DROPPED" in sent[0] and "NOT in the ledger" in sent[0]
    assert SU.load(env["kw"]["ledger"]) == [] and TR.retry_pending(env["st"], mode_fn=lambda: "ARENA_LIVE", default_participant="X") == []


def test_unparsed_signal_like_post_alerts_but_result_chatter_does_not(env, monkeypatch):
    monkeypatch.setenv("TG_INGEST_SUBMIT", "1"); sent = _alerts(monkeypatch)
    assert TR.process(P(3, "NIFTY 22850 CE soon, target will be shared"), env["st"], **env["kw"])["outcome"] == "IGNORED_NOT_A_SIGNAL" and len(sent) == 1 and "NOT parsed" in sent[0]
    TR.process(P(4, "490 TO 590 #SENSEX 73700PE\n\nGAINING RS- 4,000-/ 2 LOTS\n\nFIRST TARGET ALMOST HIT"), env["st"], **env["kw"]); assert len(sent) == 1       # claim chatter: no alert


def test_log_only_mode_sends_no_alerts_and_retries_nothing(env, monkeypatch):
    monkeypatch.delenv("TG_INGEST_SUBMIT", raising=False); sent = _alerts(monkeypatch); monkeypatch.setattr(CI, "pick_expiry", lambda u, d, p=None: None)
    r = TR.process(P(5, HERO), env["st"], **env["kw"]); assert r["outcome"] == "NO_CAPTURED_EXPIRY" and not r.get("retry_scheduled") and not env["st"].get("pending") and sent == []


def test_watchdog_evaluate():
    import importlib.util, os
    sp = importlib.util.spec_from_file_location("wd", os.path.join(os.path.dirname(__file__), "..", "scripts", "arena_ingest_watchdog.py")); wd = importlib.util.module_from_spec(sp); sp.loader.exec_module(wd)
    n = 1_000_000.0; good = {"connected": True, "updated": n - 10, "last_poll": n - 20}
    assert wd.evaluate(good, n) == (True, "ok") and not wd.evaluate(None, n)[0] and not wd.evaluate({**good, "connected": False}, n)[0]
    assert "stale" in wd.evaluate({**good, "updated": n - 600}, n)[1] and "poll loop" in wd.evaluate({**good, "last_poll": n - 600}, n)[1]


def test_check_looks_again_fresh_before_calling_a_real_contract_unscorable(monkeypatch):
    from app.arena import contracts as CT_
    stale = {"day": "2026-10-22", "expiries": {"2026-10-22": {"strikes": [22850.0], "raw": "22OCT2026"}}}
    fresh = {"day": "2026-10-23", "expiries": {"2026-10-29": {"strikes": [22850.0], "raw": "29OCT2026"}}}
    monkeypatch.setattr(CT_, "captured", lambda u, d=None, p=None: stale); monkeypatch.setattr(CT_, "captured_fresh", lambda u, d=None, p=None, max_age=30.0: fresh); monkeypatch.setattr(CT_, "last_quote", lambda *a, **k: None)
    assert CT_.check("NIFTY", "2026-10-29", 22850.0, "PE", 155.0, "LIMIT")["found"] is True


def test_poll_interval_is_fast_by_default_backs_off_after_a_flood_wait_and_recovers():
    from app.arena import tg_reader as T
    assert T.POLL_SEC <= 3.0                                                                        # was 15 s: median post -> reader lag 9 s
    p = T.next_poll(T.POLL_SEC, True, 0.0); assert p == min(T.POLL_MAX_SEC, T.POLL_SEC * 2)         # FloodWait: slow down
    for _ in range(6): p = T.next_poll(p, True, 0.0)
    assert p == T.POLL_MAX_SEC                                                                      # bounded
    assert T.next_poll(p, False, 10.0) == p                                                         # not calm yet: keep the slow rate
    for _ in range(6): p = T.next_poll(p, False, 301.0)
    assert p == T.POLL_SEC                                                                          # calm for 5 min: back to the fast rate, never below base


def test_event_handlers_are_coroutines_not_lambdas():
    import re
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "arena", "tg_reader.py"), encoding="utf-8").read() if "os" in globals() else open("app/arena/tg_reader.py", encoding="utf-8").read()
    assert not re.search(r"add_event_handler\(\s*lambda", src) and "async def on_new" in src and "async def on_edit" in src
