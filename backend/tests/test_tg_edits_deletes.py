"""Telegram ingestion phase 4: edited and deleted channel posts. Everything prospective; a deletion can never erase a filled trade."""
import json
import time

import pytest

from app.arena import channel_ingest as CI, submit as SU, settle as S, tg_journal as TJ, tg_pipeline as TP, tg_reader as TR
from tests.test_arena import ev, sig, snaps, H
from tests.test_channel_parse import HERO

CHAT = -1001316378442


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_INGEST_SUBMIT", "1")
    monkeypatch.setattr(CI, "pick_expiry", lambda u, d, p=None: "2026-10-27")
    monkeypatch.setattr(CI.CT, "check", lambda *a, **k: {"problems": [], "warnings": [], "last": None})
    monkeypatch.setattr(CI, "_market_open", lambda u: True)
    monkeypatch.setattr(TR, "QUARANTINE", str(tmp_path / "q.jsonl")); monkeypatch.setattr(TR, "DIR", str(tmp_path)); monkeypatch.setattr(TR, "STATUS", str(tmp_path / "st.json"))
    sent = []; monkeypatch.setattr(TR, "send_alert", sent.append)
    return dict(st={"seen": {}, "by_msg": {}, "last_by_chat": {}}, sent=sent, kw=dict(participant="INSTITUTION_TRADER", mode_fn=lambda: "ARENA_LIVE", ledger=str(tmp_path / "l.jsonl"), log=str(tmp_path / "log.jsonl")))


def P(mid, text, age=5, edit_age=None):
    p = {"chat": {"id": CHAT, "type": "channel"}, "message_id": mid, "date": int(time.time()) - age, "text": text}
    if edit_age is not None: p["edit_date"] = int(time.time()) - edit_age
    return p


NEW_LEVELS = HERO.replace("120/170/260", "130/180/270").replace("Stoploss : 05", "Stoploss : 10")


def test_edit_of_a_submitted_signal_amends_stop_and_targets_at_the_edit_time(env):
    TR.process(P(1, HERO, age=600), env["st"], **env["kw"]); sid = env["st"]["by_msg"][f"{CHAT}:1"]
    e = P(1, NEW_LEVELS, age=600, edit_age=20); r = TR.process(e, env["st"], edited=True, **env["kw"])
    assert r["outcome"] == "EDIT_APPLIED" and sorted(r["actions"]) == ["AMEND_SL", "AMEND_TARGETS"]
    recs = SU.load(env["kw"]["ledger"]); evs = [x for x in recs if x["type"] == "HUMAN_EVENT"]
    assert len(evs) == 2 and all(x["signal_id"] == sid and abs(x["server_ts"] - e["edit_date"]) < 2 for x in evs)          # stamped at the EDIT, not at the 10-minute-old post
    assert next(x for x in evs if x["action"] == "AMEND_SL")["sl"] == 10 and next(x for x in evs if x["action"] == "AMEND_TARGETS")["targets"] == [130, 180, 270]
    assert TR.process(e, env["st"], edited=True, **env["kw"])["outcome"] == "DUPLICATE" and len([x for x in SU.load(env["kw"]["ledger"]) if x["type"] == "HUMAN_EVENT"]) == 2


def test_a_second_edit_compares_with_the_levels_now_in_force(env):
    TR.process(P(1, HERO), env["st"], **env["kw"])
    TR.process(P(1, NEW_LEVELS, edit_age=30), env["st"], edited=True, **env["kw"])
    assert TR.process(P(1, NEW_LEVELS, edit_age=10), env["st"], edited=True, **env["kw"])["outcome"] == "EDIT_NO_CHANGE"          # same levels again: nothing to amend


def test_edit_naming_a_different_contract_applies_nothing_and_alerts(env):
    TR.process(P(1, HERO), env["st"], **env["kw"])
    r = TR.process(P(1, HERO.replace("22400CE", "22500CE"), edit_age=5), env["st"], edited=True, **env["kw"])
    assert r["outcome"] == "EDIT_CONTRACT_CHANGED" and [x for x in SU.load(env["kw"]["ledger"]) if x["type"] == "HUMAN_EVENT"] == [] and len(env["sent"]) == 1 and "NOT fully applied" in env["sent"][0]


def test_edit_that_turns_a_non_signal_into_a_signal_is_a_new_signal_stamped_at_the_edit_time(env):
    TR.process(P(1, "Good morning", age=900), env["st"], **env["kw"])
    e = P(1, HERO, age=900, edit_age=15); r = TR.process(e, env["st"], edited=True, **env["kw"])
    assert r["outcome"] == "SUBMITTED" and r["from_edit"]
    rec = [x for x in SU.load(env["kw"]["ledger"]) if x["type"] == "HUMAN_SIGNAL"][0]; assert abs(rec["server_ts"] - e["edit_date"]) < 2          # never backdated to the 15-minute-old original
    r2 = TR.process(P(1, NEW_LEVELS, age=900, edit_age=3), env["st"], edited=True, **env["kw"])            # a later edit of the same post finds that signal
    assert r2["outcome"] == "EDIT_APPLIED" and r2["signal_id"] == rec["id"]


def test_edit_while_the_original_waits_for_data_replaces_the_pending_post(env, monkeypatch):
    holder = {"v": None}; monkeypatch.setattr(CI, "pick_expiry", lambda u, d, p=None: holder["v"])
    r1 = TR.process(P(1, HERO, age=60), env["st"], **env["kw"]); assert r1["retry_scheduled"] and f"{CHAT}:1" in env["st"]["pending"]
    holder["v"] = "2026-10-27"
    r2 = TR.process(P(1, NEW_LEVELS, age=60, edit_age=10), env["st"], edited=True, **env["kw"]); assert r2["outcome"] == "SUBMITTED"
    assert not env["st"]["pending"] and TR.retry_pending(env["st"], mode_fn=env["kw"]["mode_fn"], default_participant="X", ledger=env["kw"]["ledger"], log=env["kw"]["log"]) == []
    sigs = [x for x in SU.load(env["kw"]["ledger"]) if x["type"] == "HUMAN_SIGNAL"]; assert len(sigs) == 1 and sigs[0]["sl"] == 10          # one signal, with the EDITED levels


def test_an_edit_read_long_after_it_was_made_is_not_applied(env):
    TR.process(P(1, HERO), env["st"], **env["kw"])
    r = TR.process(P(1, NEW_LEVELS, edit_age=3000), env["st"], edited=True, now=time.time(), **env["kw"]); assert r["outcome"] == "EDIT_STALE_NOT_APPLIED"


def test_edits_off_switch_restores_the_old_behaviour(env, monkeypatch):
    monkeypatch.setenv("TG_EDITS", "0"); assert TR.process(P(2, HERO, edit_age=1), env["st"], edited=True, **env["kw"])["outcome"] == "EDIT_IGNORED" and SU.load(env["kw"]["ledger"]) == []


def test_edit_row_carries_its_edit_time_through_the_journal():
    row = {"chat_id": str(CHAT), "message_id": 4, "source_ts": 1000.0, "received_at": 1100.0, "text": "x", "reply_to": None, "edit_version": 1050}
    assert TP.row_to_post(row)["edit_date"] == 1050 and "edit_date" not in TP.row_to_post({**row, "edit_version": 0})


# ---------------------------------------------------------------- deletions
def test_deleting_a_submitted_post_cancels_the_unfilled_order_once(env):
    TR.process(P(1, HERO), env["st"], **env["kw"]); sid = env["st"]["by_msg"][f"{CHAT}:1"]
    r = CI.handle_delete(CHAT, 1, env["st"], participant="INSTITUTION_TRADER", ledger=env["kw"]["ledger"], log=env["kw"]["log"], alert=env["sent"].append)
    assert r["outcome"] == "DELETE_CANCEL_SUBMITTED" and r["signal_id"] == sid
    ev_ = [x for x in SU.load(env["kw"]["ledger"]) if x["type"] == "HUMAN_EVENT"]; assert len(ev_) == 1 and ev_[0]["action"] == "CANCEL" and len(env["sent"]) == 1


def test_deleting_a_post_still_waiting_for_data_means_it_is_never_submitted(env, monkeypatch):
    holder = {"v": None}; monkeypatch.setattr(CI, "pick_expiry", lambda u, d, p=None: holder["v"])
    TR.process(P(1, HERO, age=60), env["st"], **env["kw"])
    assert CI.handle_delete(CHAT, 1, env["st"], participant="INSTITUTION_TRADER", ledger=env["kw"]["ledger"], log=env["kw"]["log"])["outcome"] == "DELETED_BEFORE_SUBMIT"
    holder["v"] = "2026-10-27"
    assert TR.retry_pending(env["st"], mode_fn=env["kw"]["mode_fn"], default_participant="X", ledger=env["kw"]["ledger"], log=env["kw"]["log"]) == [] and SU.load(env["kw"]["ledger"]) == []


def test_deleting_an_unrelated_post_changes_nothing(env):
    assert CI.handle_delete(CHAT, 99, env["st"], participant="INSTITUTION_TRADER", ledger=env["kw"]["ledger"], log=env["kw"]["log"])["outcome"] == "DELETE_NO_SIGNAL" and SU.load(env["kw"]["ledger"]) == []


def test_a_cancel_after_the_fill_does_not_erase_the_trade():
    sn = snaps([("10:01", 100, 99, 101), ("10:05", 110, 109, 111), ("10:10", 160, 159, 161)])
    base = S.settle(sig(entry_mode="NEAR", entry=101.0, sl=90.0, targets=[150.0]), sn, half_spread=H)
    r = S.settle(sig(entry_mode="NEAR", entry=101.0, sl=90.0, targets=[150.0], events=[ev("10:07", "CANCEL")]), sn, half_spread=H)
    assert base["status"] == "SETTLED" and r["status"] == "SETTLED" and r["exit"] == base["exit"]                  # the trade filled at 10:01: the later cancel is not honoured


# ---------------------------------------------------------------- journal + worker
@pytest.fixture
def J(tmp_path): return TJ.Journal(str(tmp_path / "cap.db"))


def test_a_deletion_seen_by_event_and_by_reconciler_is_stored_once(J):
    assert J.record_delete(str(CHAT), 5, "event") is True and J.record_delete(str(CHAT), 5, "reconcile") is False
    assert len(J.pending_deletes()) == 1 and J.summary()["deletes_pending"] == 1


def test_worker_processes_deletes_and_parks_failures(J):
    J.record_delete(str(CHAT), 1); J.record_delete(str(CHAT), 2); seen = []

    def fn(chat, mid, state, **kw):
        if mid == 2: raise RuntimeError("boom")
        seen.append(mid); return {"outcome": "DELETE_NO_SIGNAL"}
    alerts = []
    for _ in range(TJ.MAX_ATTEMPTS):
        TP.process_deletes(J, {}, delete_fn=fn, participant_for=lambda c, d: d, default_participant="X", save_state=lambda: None, alert=alerts.append)
    assert seen == [1] and J.summary()["deletes_failed"] == 1 and len(alerts) == 1
    h = TP.health(J.summary(), {"connected": True}); assert h["state"] == "RECOVERY_REQUIRED" and "deletion" in " ".join(h["reasons"])


def test_reconciler_finds_a_vanished_signal_post(J):
    now = 2_000_000.0
    for mid in (1, 2, 3):
        J.record(chat_id=CHAT, message_id=mid, source_ts=now - 100, text="t", received_at=now - 90); r = J._c().execute("SELECT id FROM tg_capture WHERE message_id=?", (mid,)).fetchone()[0]; J.mark_done(r, "SUBMITTED", f"H{mid}")

    async def fetch_ids(client, cid, ids): return {1, 3}                              # message 2 is gone at the source
    import asyncio
    assert asyncio.run(TP.detect_deletions(None, J, CHAT, fetch_ids=fetch_ids, now=now)) == 1
    assert [d["message_id"] for d in J.pending_deletes()] == [2] and asyncio.run(TP.detect_deletions(None, J, CHAT, fetch_ids=fetch_ids, now=now)) == 0


def test_a_failed_existence_check_is_never_taken_for_a_deletion(J):
    now = 2_000_000.0; J.record(chat_id=CHAT, message_id=1, source_ts=now - 10, text="t", received_at=now - 9); J.mark_done(J._c().execute("SELECT id FROM tg_capture").fetchone()[0], "SUBMITTED", "H1")

    async def boom(client, cid, ids): raise ConnectionError("down")
    import asyncio
    with pytest.raises(ConnectionError): asyncio.run(TP.detect_deletions(None, J, CHAT, fetch_ids=boom, now=now))
    assert J.pending_deletes() == []
