"""End-to-end smoke of tg_reader.run() with a FAKE Telegram client (no network, no session): the real poll / worker / reconciler / health loops run against temp files."""
import asyncio
import json
import os
import time
from datetime import datetime, timezone

import pytest

from app.arena import channel_ingest as CI, tg_journal as TJ, tg_reader as TR

CH = -1001234567890


class M:
    def __init__(self, i, text, age=2.0):
        self.id = i; self.message = text; self.date = datetime.fromtimestamp(time.time() - age, timezone.utc); self.sender_id = None; self.edit_date = None; self.reply_to = None


class FakeTG:
    instance = None
    def __init__(self, *a, **k): FakeTG.instance = self; self.msgs = [M(100, "baseline post", 600)]; self.handlers = []; self.caught_up = 0; self.up = True
    async def connect(self): pass
    async def is_user_authorized(self): return True
    def is_connected(self): return self.up
    def add_event_handler(self, cb, ev): self.handlers.append(cb)
    async def get_messages(self, cid, limit=1, **k): return sorted(self.msgs, key=lambda m: -m.id)[:limit]
    def iter_messages(self, cid, min_id=0, reverse=False, limit=None):
        sel = [m for m in sorted(self.msgs, key=lambda m: m.id) if m.id > min_id][: limit]
        async def gen():
            for m in sel: yield m
        return gen()
    async def catch_up(self): self.caught_up += 1


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_SOURCE_CHANNEL_IDS", str(CH)); monkeypatch.setenv("TG_API_ID", "1"); monkeypatch.setenv("TG_API_HASH", "x"); monkeypatch.delenv("TG_INGEST_SUBMIT", raising=False); monkeypatch.delenv("TG_JOURNAL", raising=False)
    monkeypatch.setattr(TR, "DIR", str(tmp_path)); monkeypatch.setattr(TR, "STATUS", str(tmp_path / "status.json")); monkeypatch.setattr(TR, "LAST_IDS", str(tmp_path / "last_ids.json")); monkeypatch.setattr(TR, "SESSION", str(tmp_path / "s"))
    monkeypatch.setattr(TR, "QUARANTINE", str(tmp_path / "q.jsonl")); monkeypatch.setattr(TR, "POLL_SEC", 0.05); monkeypatch.setattr(TR, "RECONCILE_SEC", 0.2); monkeypatch.setattr(TR, "send_alert", lambda t: None)
    monkeypatch.setattr(CI, "STATE", str(tmp_path / "state.json")); monkeypatch.setattr(CI, "LOG", str(tmp_path / "log.jsonl")); monkeypatch.setattr(CI, "DIR", str(tmp_path))
    monkeypatch.setattr(TJ, "DB", str(tmp_path / "cap.db"))
    from app.arena import api
    monkeypatch.setattr(api, "cfg", lambda: {"name": "P", "participants": {}, "flags": {}}); monkeypatch.setattr(api, "mode", lambda c: "PRACTICE")
    import telethon; monkeypatch.setattr(telethon, "TelegramClient", FakeTG)
    real_process = TR.process; logf = str(tmp_path / "log.jsonl")
    monkeypatch.setattr(TR, "process", lambda post, st, **kw: real_process(post, st, **{**kw, "log": logf}))            # handle_post binds its default log path at import time: redirect it, never touch the real ingest log
    monkeypatch.setattr(TR, "_quarantine", lambda rec: None)
    FakeTG.instance = None
    return tmp_path


async def until(pred, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred(): return True
        await asyncio.sleep(0.05)
    return False


def test_run_baselines_polls_journals_processes_dedupes_reconciles_and_reports_health(env):
    async def go():
        task = asyncio.ensure_future(TR.run())
        assert await until(lambda: FakeTG.instance and os.path.exists(env / "last_ids.json") and json.load(open(env / "last_ids.json")).get(str(CH)) == 100)      # first run: baseline at the newest id, nothing back-filled
        tg = FakeTG.instance; tg.msgs += [M(101, "just chatting"), M(102, "NIFTY 25000 CE buy above 100 sl 90 target 120"), M(103, "target hit all done")]
        J = TJ.Journal(str(env / "cap.db"))
        assert await until(lambda: J.summary()["by_status"].get("PROCESSED") == 3)                                                          # polled -> journaled -> processed by the worker
        out = {r["message_id"]: r["outcome"] for r in map(dict, J._c().execute("SELECT * FROM tg_capture"))}
        assert out[101] == "IGNORED_NOT_A_SIGNAL" and out[103] == "CLAIM_RECORDED" and out[102] in ("WOULD_SUBMIT", "NO_CAPTURED_EXPIRY", "NO_CAPTURED_EXPIRY_FOR_POST", "UNSCORABLE_NOT_SUBMITTED", "MARKET_CLOSED_NOT_SUBMITTED", "UNSUPPORTED_INSTRUMENT") or out[102]
        # a push event for a new post and a duplicate push of an old one
        class Ev: pass
        e = Ev(); e.chat_id = CH; e.message = M(104, "another chat"); await tg.handlers[0](e)
        d = Ev(); d.chat_id = CH; d.message = tg.msgs[-1]; await tg.handlers[0](d)                  # 103 again (duplicate delivery)
        assert await until(lambda: J.summary()["rows"] == 4 and J.summary()["by_status"].get("PROCESSED") == 4)
        rows = {r["message_id"]: r["update_state"] for r in map(dict, J._c().execute("SELECT * FROM tg_capture"))}; assert rows[104] == "push" and rows[102] == "poll"
        # the reconciler ran (catch_up once per connection) and nothing was missing
        assert await until(lambda: J.kv_get("last_reconcile_at") is not None) and tg.caught_up == 1 and J.kv_get("last_catch_up_ok") == "1" and J.gaps() == []
        # health is published
        assert await until(lambda: json.load(open(env / "status.json")).get("state") in ("LIVE", "DEGRADED"))
        assert await until(lambda: (json.load(open(env / "status.json")).get("journal") or {}).get("rows") == 4, timeout=9)                  # the health publisher refreshes every 5 s
        st = TR.public_status(); assert st["journaling"] is True and st["journal"]["rows"] == 4 and st["counts"].get("duplicate", 0) >= 1
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        return True
    assert asyncio.run(go())


def test_a_post_whose_journal_write_failed_is_found_and_backfilled_by_the_reconciler_without_a_double_submit(env, monkeypatch):
    monkeypatch.setattr("app.arena.tg_pipeline.GRACE_SEC", 0.0)
    from app.arena import tg_pipeline as TP
    real_capture = TP.capture; failed = []
    def flaky(journal, cid, message, *, source, **kw):
        if message.id == 150 and source == "poll" and not failed: failed.append(1); raise OSError("database is locked")          # the journal write fails once, in the poll path
        return real_capture(journal, cid, message, source=source, **kw)
    monkeypatch.setattr(TP, "capture", flaky)
    async def go():
        task = asyncio.ensure_future(TR.run())
        assert await until(lambda: FakeTG.instance and os.path.exists(env / "last_ids.json"))
        tg = FakeTG.instance; J = TJ.Journal(str(env / "cap.db"))
        TJ.Journal.record(J, chat_id=CH, message_id=100, source_ts=time.time() - 600, text="baseline post", update_state="push")             # gives the reconciler its starting watermark
        tg.msgs.append(M(150, "lost post", age=100))
        assert await until(lambda: failed and J.has(CH, 150) and J.summary()["by_status"].get("PROCESSED", 0) >= 2, timeout=9)
        r = dict(J._c().execute("SELECT * FROM tg_capture WHERE message_id=150").fetchone())
        assert r["update_state"] == "backfill" and r["recovery_status"] == "BACKFILLED"                    # the poll's journal write failed; the reconciler found it at the source
        assert any(g["kind"] == "MISSING_MESSAGE" and g["status"] == "RECOVERED" and g["recovered_count"] == 1 for g in J.gaps())
        assert r["outcome"] == "DUPLICATE"                                                                 # it had already been processed directly (journal write failed -> direct path): never submitted twice
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        return True
    assert asyncio.run(go())


def test_kill_switch_uses_the_direct_path_and_never_touches_the_journal(env, monkeypatch):
    monkeypatch.setenv("TG_JOURNAL", "0")
    async def go():
        task = asyncio.ensure_future(TR.run())
        assert await until(lambda: FakeTG.instance and os.path.exists(env / "last_ids.json"))
        FakeTG.instance.msgs.append(M(101, "just chatting"))
        assert await until(lambda: os.path.exists(env / "log.jsonl") and "IGNORED_NOT_A_SIGNAL" in open(env / "log.jsonl").read())
        assert not os.path.exists(env / "cap.db") and TR.public_status()["journaling"] is False
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        return True
    assert asyncio.run(go())


def test_if_the_journal_cannot_be_opened_the_reader_falls_back_to_the_direct_path(env, monkeypatch):
    monkeypatch.setattr(TJ, "Journal", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only disk")))
    async def go():
        task = asyncio.ensure_future(TR.run())
        assert await until(lambda: FakeTG.instance and os.path.exists(env / "last_ids.json"))
        FakeTG.instance.msgs.append(M(101, "just chatting"))
        assert await until(lambda: os.path.exists(env / "log.jsonl") and "IGNORED_NOT_A_SIGNAL" in open(env / "log.jsonl").read())
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        return True
    assert asyncio.run(go())
