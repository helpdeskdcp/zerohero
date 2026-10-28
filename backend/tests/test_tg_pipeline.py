"""Telegram ingestion phases 2-3: capture journal, worker, reconciliation, catch_up, health."""
import asyncio
import json
import os
import time
from datetime import datetime, timezone

import pytest

from app.arena import channel_ingest as CI, tg_journal as TJ, tg_pipeline as TP, tg_reader as TR

T0 = 1_793_000_000.0


class Msg:
    def __init__(self, i, text="hello", age=1000.0, edit=None, reply=None, sender=None):
        self.id = i; self.message = text; self.date = datetime.fromtimestamp(T0 - age, timezone.utc); self.sender_id = sender
        self.edit_date = datetime.fromtimestamp(edit, timezone.utc) if edit else None
        self.reply_to = type("R", (), {"reply_to_msg_id": reply})() if reply else None


@pytest.fixture
def J(tmp_path): return TJ.Journal(str(tmp_path / "cap.db"))


def run(c): return asyncio.run(c)


# ---------------------------------------------------------------- journal
def test_same_post_by_push_poll_and_backfill_is_stored_once(J):
    for src in ("push", "poll", "backfill", "catch_up"): TP.capture(J, 5, Msg(10), source=src, now=T0)
    assert J.summary()["rows"] == 1 and J.pending()[0]["update_state"] == "push"             # the first arrival wins, the rest are recognised duplicates
    assert TP.capture(J, 5, Msg(10), source="poll", now=T0) == (None, False)


def test_an_edit_is_its_own_row_and_never_overwrites_the_original(J):
    TP.capture(J, 5, Msg(10, "BUY 100"), source="push", now=T0); TP.capture(J, 5, Msg(10, "BUY 120", edit=T0 + 60), source="push", edited=True, now=T0 + 61)
    rows = J.pending(); assert [(r["edit_version"] > 0, r["text"], r["update_state"]) for r in rows] == [(False, "BUY 100", "push"), (True, "BUY 120", "edit")]
    assert rows[0]["content_hash"] != rows[1]["content_hash"]


def test_ignored_senders_keep_no_text(J):
    TP.capture(J, 5, Msg(11, "member chatter", sender=999), source="push", sender_ok=False, now=T0)
    r = J._c().execute("SELECT * FROM tg_capture").fetchone(); assert r["ingestion_status"] == "IGNORED" and r["text"] is None and r["content_hash"]
    assert J.pending() == []


def test_journal_file_is_private_and_summary_has_no_text(J):
    TP.capture(J, 5, Msg(12, "secret strategy text"), source="push", now=T0)
    assert oct(os.stat(J.path).st_mode)[-3:] == "600" and "secret" not in json.dumps(J.summary())


# ---------------------------------------------------------------- worker
def fake_process(log, fail=None):
    def f(post, st, **kw):
        if fail and post["message_id"] in fail: raise RuntimeError("db locked")
        log.append((post["chat"]["id"], post["message_id"], kw["edited"], kw["now"])); return {"outcome": "SUBMITTED", "signal_id": f"H{post['message_id']}"}
    return f


def worker(J, proc, alert=None, now=None, saves=None):
    return TP.process_pending(J, {}, process_fn=proc, participant_for=lambda c, d: d, default_participant="P", mode_fn=lambda: "PRACTICE", save_state=(lambda: saves.append(1)) if saves is not None else (lambda: None), alert=alert, now=now)


def test_worker_processes_in_post_order_with_capture_time_and_marks_rows(J):
    for i in (13, 11, 12): TP.capture(J, 5, Msg(i), source="push", now=T0 + i)                # arrived out of order
    log, saves = [], []
    assert worker(J, fake_process(log), saves=saves) == 3
    assert [m for _, m, _, _ in log] == [11, 12, 13] and [n for *_, n in log] == [T0 + 11, T0 + 12, T0 + 13] and len(saves) == 3
    r = {x["message_id"]: x for x in [dict(y) for y in J._c().execute("SELECT * FROM tg_capture")]}; assert r[12]["ingestion_status"] == "PROCESSED" and r[12]["signal_id"] == "H12" and J.pending() == []


def test_a_crash_after_capture_loses_nothing(tmp_path):
    p = str(tmp_path / "cap.db"); J1 = TJ.Journal(p)
    for i in (1, 2, 3): TP.capture(J1, 5, Msg(i), source="push", now=T0)
    log = []
    class Boom(BaseException): pass
    def die_on_2(post, st, **kw):
        if post["message_id"] == 2: raise Boom()
        return fake_process(log)(post, st, **kw)
    with pytest.raises(Boom): worker(J1, die_on_2)
    J2 = TJ.Journal(p); log2 = []                                                              # "restart": a new process opens the same file
    assert [r["message_id"] for r in J2.pending()] == [2, 3]
    worker(J2, fake_process(log2)); assert [m for _, m, _, _ in log2] == [2, 3] and J2.summary()["by_status"] == {"PROCESSED": 3}


def test_failures_back_off_then_park_as_dead_letter_with_one_alert(J):
    TP.capture(J, 5, Msg(20), source="push", now=T0); alerts = []; t = T0
    for i in range(12):
        worker(J, fake_process([], fail={20}), alert=alerts.append, now=t); t += 70                # each round is past the backoff
    s = J.summary(); assert s["dead_letter"] == 1 and s["by_status"] == {"FAILED": 1} and len(alerts) == 1 and "NOT in the ledger" in alerts[0]
    h = TP.health(J.summary(T0), {"connected": True}, T0); assert h["state"] == "RECOVERY_REQUIRED"
    J.requeue_failed(1); worker(J, fake_process([]), now=t); assert J.summary()["dead_letter"] == 0


def test_backoff_prevents_a_hot_retry_loop(J):
    TP.capture(J, 5, Msg(21), source="push", now=T0); calls = []
    def f(post, st, **kw): calls.append(1); raise RuntimeError("x")
    for k in range(5): worker(J, f, now=T0 + k * 0.1)
    assert len(calls) == 1                                                                         # retried only after the backoff, not on every loop


# ---------------------------------------------------------------- reconciliation
class FakeClient:
    def __init__(self, msgs): self.msgs = sorted(msgs, key=lambda m: m.id); self.fail = 0; self.catch_up_result = None
    def iter_messages(self, cid, min_id=0, reverse=False, limit=None):
        if self.fail: self.fail -= 1; raise ConnectionError("down")
        sel = [m for m in self.msgs if m.id > min_id][: limit]
        async def gen():
            for m in sel: yield m
        return gen()
    async def catch_up(self):
        if isinstance(self.catch_up_result, Exception): raise self.catch_up_result
        if self.catch_up_result == "hang": await asyncio.sleep(10)


def recon(J, client, now=T0, **kw): return run(TP.reconcile_channel(client, J, 5, fetch=TR.fetch_new, now=now, **kw))


def test_reconciler_finds_a_missed_message_journals_it_and_records_the_gap(J):
    TP.capture(J, 5, Msg(100, age=500), source="push", now=T0 - 500); client = FakeClient([Msg(100, age=500), Msg(101, age=300), Msg(102, age=200), Msg(103, age=10)])
    J.kv_set("wm:5", 100)
    r = recon(J, client)
    assert r["missing"] == 2 and r["recovered"] == 2                                              # 101 and 102 were never journaled; 103 is too fresh to judge
    rows = {x["message_id"]: x for x in [dict(y) for y in J._c().execute("SELECT * FROM tg_capture")]}
    assert rows[101]["recovery_status"] == "BACKFILLED" and rows[101]["update_state"] == "backfill" and 103 not in rows
    assert J.kv_get("wm:5") == "102" and {g["status"] for g in J.gaps()} == {"RECOVERED"} and len(J.gaps()) == 2
    assert recon(J, client)["missing"] == 0 and J.kv_get("wm:5") == "102"                         # idempotent; the fresh message is left to push/poll
    assert recon(J, client, now=T0 + 60)["checked"] == 1 and J.kv_get("wm:5") == "103"            # once old enough it is verified (and found already journaled or backfilled)


def test_a_healthy_pipeline_reports_no_gaps(J):
    for i in (100, 101, 102): TP.capture(J, 5, Msg(i, age=400 - i), source="push", now=T0)
    J.kv_set("wm:5", 99); client = FakeClient([Msg(i, age=400 - i) for i in (100, 101, 102)])
    r = recon(J, client); assert r["missing"] == 0 and r["checked"] == 3 and J.gaps() == [] and J.kv_get("wm:5") == "102"


def test_first_run_starts_at_the_newest_journaled_id_without_historical_backfill(J):
    assert recon(J, FakeClient([Msg(1, age=900)]))["checked"] == 0                                # nothing journaled: the poll loop baselines the channel
    TP.capture(J, 5, Msg(500, age=100), source="poll", now=T0); r = recon(J, FakeClient([Msg(i, age=900 - i) for i in (1, 2, 500)]))
    assert r["missing"] == 0 and J.kv_get("wm:5") == "500" and J.summary()["rows"] == 1


def test_unreachable_source_opens_a_gap_that_escalates_but_never_claims_recovery(J):
    TP.capture(J, 5, Msg(100, age=500), source="push", now=T0); J.kv_set("wm:5", 100); client = FakeClient([Msg(101, age=300)]); client.fail = 99
    for k in range(TP.MAX_GAP_ATTEMPTS): assert recon(J, client)["failed"] is True
    g = J.gaps(); assert len(g) == 1 and g[0]["status"] == "UNRECOVERABLE" and g[0]["attempts"] == TP.MAX_GAP_ATTEMPTS and J.kv_get("wm:5") == "100"      # the watermark did not move over an unseen range
    h = TP.health(J.summary(T0), {"connected": True}, T0); assert h["state"] == "RECOVERY_REQUIRED"
    assert J.ack_gap(g[0]["id"], "checked by hand") and TP.health(J.summary(T0), {"connected": True}, T0)["state"] == "LIVE"
    assert J.gaps()[0]["status"] == "ACKED" and "ACK" in J.gaps()[0]["note"]                      # kept in the history, not erased


def test_a_transient_failure_recovers_and_closes_its_gap(J):
    TP.capture(J, 5, Msg(100, age=500), source="push", now=T0); J.kv_set("wm:5", 100); client = FakeClient([Msg(101, age=300)]); client.fail = 1
    recon(J, client); assert J.summary()["open_gaps"] == 1 and TP.health(J.summary(T0), {"connected": True}, T0)["state"] == "DEGRADED"
    r = recon(J, client); assert r["missing"] == 1 and J.summary()["open_gaps"] == 0 and TP.health(J.summary(T0), {"connected": True}, T0)["state"] == "LIVE"


def test_ack_only_applies_to_unrecoverable_gaps(J):
    gid = J.open_gap(chat_id=5, from_id=1, to_id=2, kind="MISSING_MESSAGE"); assert J.ack_gap(gid, "x") is False and J.gaps()[0]["status"] == "OPEN"


# ---------------------------------------------------------------- catch_up
def test_catch_up_success_failure_and_timeout_are_recorded(J):
    ctx = {"source": "push"}; c = FakeClient([])
    assert run(TP.run_catch_up(c, J, ctx=ctx)) is True and J.kv_get("last_catch_up_ok") == "1" and ctx["source"] == "push"
    c.catch_up_result = ConnectionError("no"); assert run(TP.run_catch_up(c, J, ctx=ctx)) is False and J.kv_get("last_catch_up_ok") == "0" and J.summary()["open_gaps"] == 1
    assert TP.health(J.summary(T0), {"connected": True}, T0)["state"] == "DEGRADED" and "catch_up" in " ".join(TP.health(J.summary(T0), {"connected": True}, T0)["reasons"])
    c.catch_up_result = "hang"; assert run(TP.run_catch_up(c, J, ctx=ctx, timeout=0.05)) is False and len(J.gaps()) == 1 and J.gaps()[0]["attempts"] == 1       # one gap, attempts counted
    c.catch_up_result = None; assert run(TP.run_catch_up(c, J, ctx=ctx)) is True and J.summary()["open_gaps"] == 0


def test_events_replayed_during_catch_up_are_labelled(J):
    ctx = {"source": "catch_up"}; TP.capture(J, 5, Msg(7), source=ctx["source"], now=T0); assert J.pending()[0]["update_state"] == "catch_up"


# ---------------------------------------------------------------- health
def test_health_states():
    ok = {"rows": 5, "pending": 0, "oldest_pending_age_s": None, "dead_letter": 0, "open_gaps": 0, "unrecoverable_gaps": 0, "last_reconcile_at": T0 - 30, "last_catch_up_ok": "1"}
    st = {"connected": True, "last_poll": T0 - 2, "started": T0 - 4000}
    assert TP.health(ok, st, T0) == {"state": "LIVE", "reasons": []}
    for patch_s, patch_st, want in (({"open_gaps": 1}, {}, "DEGRADED"), ({"oldest_pending_age_s": 45.0}, {}, "DEGRADED"), ({}, {"connected": False, "last_error": "X"}, "DEGRADED"), ({}, {"last_poll": T0 - 120}, "DEGRADED"),
                                    ({"last_reconcile_at": T0 - 900}, {}, "DEGRADED"), ({"last_catch_up_ok": "0"}, {}, "DEGRADED"), ({"unrecoverable_gaps": 1}, {}, "RECOVERY_REQUIRED"), ({"dead_letter": 2}, {}, "RECOVERY_REQUIRED"),
                                    ({"open_gaps": 1, "unrecoverable_gaps": 1}, {}, "RECOVERY_REQUIRED")):
        assert TP.health({**ok, **patch_s}, {**st, **patch_st}, T0)["state"] == want, (patch_s, patch_st)


def test_public_status_never_reports_live_from_a_stale_status_file(tmp_path, monkeypatch):
    monkeypatch.setattr(TR, "STATUS", str(tmp_path / "st.json")); json.dump({"connected": True, "updated": time.time() - 600, "state": "LIVE"}, open(tmp_path / "st.json", "w"))
    s = TR.public_status(); assert s["state"] == "DEGRADED" and any("stale" in r for r in s["reasons"]) and s["running"] is False
    json.dump({"connected": True, "updated": time.time(), "state": "RECOVERY_REQUIRED", "reasons": ["x"], "journal": {"rows": 3}}, open(tmp_path / "st.json", "w"))
    s = TR.public_status(); assert s["state"] == "RECOVERY_REQUIRED" and s["journal"] == {"rows": 3} and s["journaling"] is True


# ---------------------------------------------------------------- replay of the real log
LOG = os.path.join(CI.DIR, "channel_ingest_log.jsonl")


@pytest.mark.skipif(not os.path.exists(LOG), reason="no real ingest log on this machine")
def test_replaying_real_logged_posts_through_the_journal_path_keeps_every_classification(J, tmp_path, monkeypatch):
    recs = []
    for ln in open(LOG, errors="replace"):
        try: r = json.loads(ln)
        except Exception: continue
        if r.get("outcome") in ("IGNORED_NO_TEXT", "IGNORED_NOT_A_SIGNAL", "CLAIM_RECORDED") and r.get("message_id") and not r.get("retry_scheduled"): recs.append(r)
    assert len(recs) > 500
    seen = set(); uniq = []
    for r in recs:
        k = (r["chat_id"], r["message_id"])
        if k not in seen: seen.add(k); uniq.append(r)
    monkeypatch.setattr(TR, "_quarantine", lambda rec: None); monkeypatch.setattr(TR, "send_alert", lambda t: None)
    for r in uniq + uniq:                                                                          # every post delivered twice (push + poll)
        m = Msg(int(r["message_id"]), r.get("text") or "", age=T0 - datetime.fromisoformat(r["tg_date"]).timestamp()); TP.capture(J, r["chat_id"], m, source="push", now=T0)
    assert J.summary()["rows"] == len(uniq)                                                       # captured exactly once each, nothing lost
    state = {"seen": {}, "by_msg": {}, "last_by_chat": {}}; logf = str(tmp_path / "log.jsonl")
    proc = lambda post, st, **kw: TR.process(post, st, log=logf, **kw)
    while TP.process_pending(J, state, process_fn=proc, participant_for=TR.participant_for, default_participant="P", mode_fn=lambda: "PRACTICE", save_state=lambda: None, batch=200): pass
    out = {(x["chat_id"], x["message_id"]): x["outcome"] for x in [dict(y) for y in J._c().execute("SELECT * FROM tg_capture")]}
    want = {(r["chat_id"], int(r["message_id"])): r["outcome"] for r in uniq}
    assert {k: v for k, v in out.items()} == {(c, m): o for (c, m), o in want.items()}
    assert J.summary()["by_status"] == {"PROCESSED": len(uniq)}
