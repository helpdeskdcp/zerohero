"""Telegram ingestion, phase 1: no silent gap on a burst or restart, atomic state files, non-blocking alerts."""
import asyncio
import json
import os
import threading
import time

import pytest

from app.arena import channel_ingest as CI, tg_reader as TR


class Msg:
    def __init__(self, i): self.id = i


class FakeClient:
    """iter_messages with Telethon's reverse=True semantics: ids strictly above min_id, ascending, at most `limit`."""
    def __init__(self, ids): self.ids = sorted(ids); self.calls = 0
    def iter_messages(self, cid, min_id=0, reverse=False, limit=None):
        self.calls += 1; assert reverse is True, "must iterate oldest-first"
        sel = [i for i in self.ids if i > min_id][: limit]
        async def gen():
            for i in sel: yield Msg(i)
        return gen()


def run(coro): return asyncio.run(coro)


def drain_all(client, last, handled, cap):
    TR.FETCH_CAP = cap; saved = []
    async def go():
        n = 0
        while True:
            got = await TR.drain_channel(client, 5, last, lambda c, m: handled.append(m.id), lambda: saved.append(dict(last)))
            n += 1
            if got < cap: return n
    return run(go()), saved


def test_burst_of_50_posts_is_captured_in_order_with_no_gap(monkeypatch):
    monkeypatch.setattr(TR, "FETCH_CAP", 20); last = {"5": 100}; handled = []
    polls, saved = drain_all(FakeClient(range(101, 151)), last, handled, 20)
    assert handled == list(range(101, 151)) and last["5"] == 150 and polls == 3            # 20 + 20 + 10: the old limit=20 call would have handled only 131..150 and jumped past 101..130


def test_restart_resumes_exactly_after_the_last_persisted_id(monkeypatch):
    client = FakeClient(range(101, 131)); handled1 = []
    last = {"5": 100}; TR.FETCH_CAP = 200
    class Crash(Exception): pass
    def boom(c, m):
        if m.id == 115: raise Crash()
        handled1.append(m.id)
    saved = []
    with pytest.raises(Crash): run(TR.drain_channel(client, 5, last, boom, lambda: saved.append(dict(last))))
    assert handled1 == list(range(101, 115)) and last["5"] == 114                         # high-water mark advanced only past messages that were handled
    handled2 = []; run(TR.drain_channel(client, 5, last, lambda c, m: handled2.append(m.id), lambda: None))
    assert handled2 == list(range(115, 131))                                              # nothing skipped, nothing repeated


def test_nothing_new_costs_one_call_and_changes_nothing():
    last = {"5": 150}; c = FakeClient(range(101, 151)); TR.FETCH_CAP = 200
    assert run(TR.drain_channel(c, 5, last, lambda *a: 1 / 0, lambda: 1 / 0)) == 0 and c.calls == 1 and last["5"] == 150


def test_save_json_replaces_atomically_and_leaves_no_temp_files(tmp_path):
    p = str(tmp_path / "state.json"); CI.save_json(p, {"seen": {"a:1": "H1"}}); CI.save_json(p, {"seen": {"a:1": "H1", "a:2": "H2"}})
    assert json.load(open(p)) == {"seen": {"a:1": "H1", "a:2": "H2"}} and os.listdir(tmp_path) == ["state.json"]


def test_a_failure_mid_write_keeps_the_old_file_intact(tmp_path, monkeypatch):
    p = str(tmp_path / "state.json"); CI.save_json(p, {"seen": {"old": 1}})
    real = json.dump
    def partial(obj, f, *a, **k): f.write('{"seen": {"ne'); raise OSError("disk full")          # a half-written new file, then failure
    monkeypatch.setattr(json, "dump", partial)
    with pytest.raises(OSError): CI.save_json(p, {"seen": {"new": 2}})
    monkeypatch.setattr(json, "dump", real)
    assert json.load(open(p)) == {"seen": {"old": 1}} and os.listdir(tmp_path) == ["state.json"]       # the old file is untouched and the temp file is cleaned up


def test_concurrent_writers_never_leave_an_unreadable_file(tmp_path):
    p = str(tmp_path / "state.json"); errs = []; stop = time.time() + 1.0
    def writer(n):
        i = 0
        while time.time() < stop: CI.save_json(p, {"w": n, "i": i, "pad": "x" * 5000}); i += 1
    def reader():
        while time.time() < stop:
            try: json.load(open(p))
            except FileNotFoundError: pass
            except Exception as e: errs.append(repr(e))
    ts = [threading.Thread(target=writer, args=(n,)) for n in range(3)] + [threading.Thread(target=reader) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert errs == [] and [f for f in os.listdir(tmp_path) if f != "state.json"] == []


def test_alert_never_blocks_the_caller(monkeypatch):
    from app.connectors import telegram
    done = threading.Event(); monkeypatch.setattr(telegram, "_send", lambda *a, **k: (time.sleep(0.4), done.set()))
    t = time.perf_counter(); TR.send_alert("x"); took = time.perf_counter() - t
    assert took < 0.1 and done.wait(2)                                                      # returned at once; the send still happened


def test_bot_route_poll_state_is_saved_atomically(tmp_path):
    st = str(tmp_path / "s.json"); res = CI.poll("t", participant="P", mode_fn=lambda: "PRACTICE", call=lambda m, **q: {"result": []}, ledger=str(tmp_path / "l.jsonl"), log=str(tmp_path / "log"), state_path=st)
    assert res == [] and json.load(open(st))["offset"] == 0 and os.listdir(tmp_path) == ["s.json"]
