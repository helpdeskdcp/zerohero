"""The dashboard and the Telegram reader are different processes writing priority_watch.json: no registration may be lost, and the file is always complete JSON."""
import json
import subprocess
import sys
import time

from app.histcap import priority as P

SCRIPT = r"""
import sys, time
sys.path.insert(0, ".")
from app.histcap import priority as P
path, i, go = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
while time.time() < go: pass
for k in range(12): P.request_watch("NIFTY", "2026-11-03", 25000 + i * 100 + k, "CE", path=path)
"""


def test_parallel_processes_lose_no_registration(tmp_path):
    path = str(tmp_path / "watch.json"); go = time.time() + 2.0
    procs = [subprocess.Popen([sys.executable, "-I", "-c", SCRIPT, path, str(i), str(go)], cwd=".") for i in range(8)]
    assert [p.wait(60) for p in procs] == [0] * 8
    items = json.load(open(path)); assert len(items) == 8 * 12 and len({(x["strike"]) for x in items}) == 96 and all(x["kind"] == "signal" for x in items)       # every one of the 96 contracts survived
    assert not [f for f in tmp_path.iterdir() if f.name.endswith(".tmp")]                                                                                         # no temporary file left behind


def test_repeat_registration_does_not_rewrite_and_a_view_never_downgrades_a_signal(tmp_path):
    path = str(tmp_path / "w.json"); t = 1_800_000_000.0
    a = P.request_watch("NIFTY", "2026-11-03", 25000, "CE", now=t, path=path); m1 = (tmp_path / "w.json").stat().st_mtime_ns
    P.request_watch("NIFTY", "2026-11-03", 25000, "CE", now=t + 5, path=path, ttl_sec=300); assert (tmp_path / "w.json").stat().st_mtime_ns == m1          # a view of a signal's contract: already covered
    assert json.load(open(path))[0]["kind"] == "signal" and a["kind"] == "signal"
    P.request_watch_equity("RELIANCE", now=t, path=path, ttl_sec=300); P.request_watch_equity("RELIANCE", now=t + 1, path=path)                                # a later signal upgrades the view
    eq = [x for x in json.load(open(path)) if x["symbol"] == "RELIANCE"]; assert len(eq) == 1 and eq[0]["kind"] == "signal" and eq[0]["inst"] == "EQUITY"
