"""Concurrent writers to the hash-chained arena ledger must never share an id or fork the chain (SU.submit reads the count + last hash, then appends)."""
import threading
import time

from app.arena import submit as SU
from app.forward_test import ledger as L

BASE = {"participant": "P", "underlying": "NIFTY", "expiry": "2026-10-29", "strike": 25000, "option_type": "CE", "entry_mode": "NEAR", "entry": 100, "sl": 90, "targets": [120], "lots": 1}


def _slow_append(orig):
    def f(path, record):                                                                              # widen the race window: yield between reading the chain tip and writing
        prev = L.last_hash(path); time.sleep(0.002)
        return orig(path, record)
    return f


def test_parallel_submits_get_unique_ids_and_one_unbroken_chain(tmp_path, monkeypatch):
    p = str(tmp_path / "h.jsonl"); monkeypatch.setattr(L, "append", _slow_append(L.append)); errs = []
    def work():
        try: SU.submit(BASE, path=p)
        except Exception as e: errs.append(repr(e))
    ts = [threading.Thread(target=work) for _ in range(24)]; [t.start() for t in ts]; [t.join() for t in ts]
    recs = SU.load(p); ids = [r["id"] for r in recs]
    assert not errs and len(recs) == 24 and len(set(ids)) == 24 and sorted(ids) == [f"H{i:05d}" for i in range(1, 25)]
    ok, n, why = L.verify_chain(p); assert ok and n == 24, why


def test_events_and_corrections_share_the_same_lock(tmp_path, monkeypatch):
    p = str(tmp_path / "h.jsonl"); monkeypatch.setattr(L, "append", _slow_append(L.append)); sig = SU.submit(BASE, path=p); errs = []
    def ev(i):
        try: SU.submit_event({"signal_id": sig["id"], "action": "AMEND_SL", "sl": 80 + (i % 5)}, "P", path=p)
        except Exception as e: errs.append(repr(e))
    ts = [threading.Thread(target=ev, args=(i,)) for i in range(12)]; [t.start() for t in ts]; [t.join() for t in ts]
    evs = [r for r in SU.load(p) if r.get("type") == "HUMAN_EVENT"]
    assert len(evs) + len(errs) == 12 and len({e["id"] for e in evs}) == len(evs)                       # event ids unique (the per-signal event limit may reject the rest)
    assert L.verify_chain(p)[0]


def test_nested_calls_in_one_thread_do_not_deadlock(tmp_path):
    p = str(tmp_path / "h.jsonl")
    with SU.ledger_lock(p):
        with SU.ledger_lock(p):
            SU.submit(BASE, path=p)
    assert len(SU.load(p)) == 1


def test_parallel_processes_share_the_lock_too(tmp_path):
    """The per-minute channel-ingest cron is a separate process: its writes must interleave safely with the service's."""
    import os, subprocess, sys
    p = str(tmp_path / "h.jsonl"); code = ("import sys; sys.path.insert(0, %r); from app.arena import submit as SU\n"
        "for _ in range(6): SU.submit(%r, path=%r)\n") % (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), BASE, p)
    procs = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) for _ in range(4)]
    errs = [pr.communicate()[1].decode()[-300:] for pr in procs]; assert all(pr.returncode == 0 for pr in procs), errs
    ids = [r["id"] for r in SU.load(p)]; assert sorted(ids) == [f"H{i:05d}" for i in range(1, 25)]
    assert L.verify_chain(p)[0]
