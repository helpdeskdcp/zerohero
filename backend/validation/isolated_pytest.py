"""Investigation-only pytest runner; execute from backend/ with a clean environment.

No production behavior is patched. All original Phase 3 safety guards remain:
no network/credential reads/subprocesses/non-temporary DB access or writes.
VALIDATION_RUN_DIR selects a /tmp report directory. VALIDATION_BASELINE optionally
selects the saved pre-Phase-3 source JSON for a read-only app import overlay;
it never restores or edits the working tree. Use only reviewed local snapshots.
VALIDATION_TEST_TIMEOUT controls the diagnostic deadline (default 10 seconds).
Tracebacks/progress identify hangs; budget failures are not assertion failures.
See PHASE3_VALIDATION_INVESTIGATION.md for results and limitations.
"""
import os
import pathlib
import sys
import tempfile

root = tempfile.mkdtemp(prefix="phase1-tests-")
for key in ("CHANAKYA_DB_PATH", "TEST_DATABASE_URL", "CHANAKYA_HIST_DB_PATH",
            "CHANAKYA_HIST_OPTIONS_DB", "CHANAKYA_L2_DB_PATH"):
    os.environ[key] = os.path.join(root, key + ".db")
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

def guard(event, args):
    if event in ('subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn'):
        raise RuntimeError('Phase 3 tests prohibit subprocesses')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = pathlib.Path(os.fsdecode(args[0])).resolve()
        flags = args[2] or 0
        if (flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)) and not (str(path).startswith('/tmp/') or str(path) in ('/tmp', '/dev/null')):
            raise RuntimeError('Phase 3 tests prohibit writes outside temporary storage')
    if event in ('os.remove', 'os.rename', 'os.rmdir', 'os.mkdir'):
        for idx in (range(2) if event == 'os.rename' else range(1)):
            arg = args[idx]
            if isinstance(arg, (str, bytes)):
                path = pathlib.Path(os.fsdecode(arg))
                fdidx = idx + 2 if event == 'os.rename' else 2 if event == 'os.mkdir' else 1
                fd = args[fdidx]
                if not path.is_absolute() and fd != -1:
                    path = pathlib.Path(os.readlink(f'/proc/self/fd/{fd}')) / path
                if not (str(path.resolve()).startswith('/tmp/') or str(path.resolve()) == '/tmp' or (event == 'os.mkdir' and path.is_dir())):
                    raise RuntimeError('Phase 3 tests prohibit mutations outside temporary storage')
    if event in ("socket.connect", "socket.getaddrinfo", "socket.sendto"):
        raise RuntimeError("Phase 1 tests prohibit real network access")
    if event == "open" and isinstance(args[0], (str, bytes)):
        if pathlib.Path(os.fsdecode(args[0])).name.startswith(".env"):
            raise RuntimeError("Phase 1 tests prohibit environment-file reads")
    if event == "sqlite3.connect":
        target = str(args[0])
        if target != ":memory:" and not target.removeprefix("file:").startswith("/tmp/"):
            raise RuntimeError("Phase 1 tests require temporary databases")


import json, importlib.machinery, importlib.util, time, faulthandler
validation_root = pathlib.Path(os.environ['VALIDATION_RUN_DIR']) if os.environ.get('VALIDATION_RUN_DIR') else pathlib.Path(tempfile.mkdtemp(prefix='phase3-validation-'))
validation_root.mkdir(parents=True, exist_ok=True)
phase3_snapshot = os.environ.get('VALIDATION_BASELINE')
if phase3_snapshot:
    snapshot = json.loads(pathlib.Path(phase3_snapshot).read_text())
    class SnapshotLoader(importlib.machinery.SourceFileLoader):
        def get_code(self, fullname):
            key = 'backend/' + str(pathlib.Path(self.path).relative_to(pathlib.Path.cwd()))
            return compile(snapshot[key], self.path, 'exec')
    class SnapshotFinder:
        def find_spec(self, fullname, path=None, target=None):
            if fullname != 'app' and not fullname.startswith('app.'):
                return None
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
            if spec and spec.origin and spec.origin.endswith('.py'):
                key = 'backend/' + str(pathlib.Path(spec.origin).relative_to(pathlib.Path.cwd()))
                if key in snapshot:
                    spec.loader = SnapshotLoader(fullname, spec.origin)
            return spec
    sys.meta_path.insert(0, SnapshotFinder())

def attribution(event, args):
    if event == 'sqlite3.connect':
        target = str(args[0]).removeprefix('file:').split('?', 1)[0]
        if target != ':memory:' and not target.startswith('/tmp/'):
            print('VALIDATION_BLOCKED_DB ' + target, file=sys.stderr, flush=True)
sys.addaudithook(attribution)

sys.addaudithook(guard)
sys.path.insert(0, os.getcwd())
import pytest
import signal
class Deadline:
    def pytest_runtest_logstart(self, nodeid, location):
        with (validation_root / 'progress.jsonl').open('a') as f:
            f.write(json.dumps({'nodeid': nodeid, 'stage': 'start'}) + '\n')
        faulthandler.dump_traceback_later(6)
    def pytest_runtest_logreport(self, report):
        with (validation_root / 'progress.jsonl').open('a') as f:
            f.write(json.dumps({'nodeid': report.nodeid, 'stage': report.when, 'outcome': report.outcome}) + '\n')
    def pytest_runtest_logfinish(self, nodeid, location):
        faulthandler.cancel_dump_traceback_later()
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        def expired(signum, frame):
            raise TimeoutError('Phase 3 isolated test exceeded 10 seconds')
        old = signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, float(os.environ.get('VALIDATION_TEST_TIMEOUT', '10')))
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)
raise SystemExit(pytest.main(["-p", "no:cacheprovider", *sys.argv[1:]], plugins=[Deadline()]))
