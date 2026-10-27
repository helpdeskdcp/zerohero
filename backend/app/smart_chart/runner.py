"""Isolated Pine execution: one short-lived Node process per request (compile / run).

Containment, defence in depth (user Pine is untrusted input):
  * `node --permission` with read access to this package only: no fs writes, no child processes, no worker threads,
    no native addons (Node's permission model denies them by default);
  * EMPTY environment (no .env values, tokens, PATH) - the process cannot see any secret;
  * network globals deleted inside the worker; PineTS gets a local data provider that never touches the network;
  * wall-clock timeout (kills the whole process), RLIMIT_CPU, RLIMIT_FSIZE=0, V8 heap cap;
  * global concurrency cap + per-owner rate limit; bounded stdin/stdout sizes.
Pine source is transpiled by PineTS - this module never passes it to eval()/exec().
"""
from __future__ import annotations

import json
import os
import resource
import shutil
import subprocess
import threading
import time
from collections import defaultdict, deque

from . import config

PKG_DIR = config.HERE
WORKER = os.path.join(PKG_DIR, "pine_runner", "worker.mjs")
_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_RUNS)
_rate_lock = threading.Lock()
_rate: dict[str, deque] = defaultdict(deque)


class RunnerError(RuntimeError):
    """`kind`: busy | rate_limited | timeout | crashed | bad_output | unavailable | too_large"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def node_binary() -> str | None:
    return os.environ.get("SMARTCHART_NODE") or shutil.which("node")


def available() -> dict:
    node = node_binary()
    ok_pkg = os.path.isdir(os.path.join(PKG_DIR, "node_modules", "pinets"))
    ver = None
    if node:
        try:
            ver = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5, env={}).stdout.strip()
        except Exception:  # noqa: BLE001
            ver = None
    return {"node": bool(node), "node_version": ver, "pinets_installed": ok_pkg, "ok": bool(node and ok_pkg and os.path.exists(WORKER))}


def check_rate(owner: str) -> None:
    now = time.monotonic()
    with _rate_lock:
        q = _rate[owner]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= config.RUNS_PER_MINUTE:
            raise RunnerError("rate_limited", f"script execution limit reached ({config.RUNS_PER_MINUTE}/min); retry shortly")
        q.append(now)


def _limit(pid: int) -> None:
    """Apply rlimits to the freshly spawned worker (prlimit, not preexec_fn: fork hooks are unsafe in a threaded server)."""
    for res, val in ((resource.RLIMIT_CPU, config.NODE_CPU_SEC), (resource.RLIMIT_FSIZE, 0), (resource.RLIMIT_NOFILE, 128)):
        try:
            resource.prlimit(pid, res, (val, val))
        except (OSError, ValueError):
            pass


def execute(request: dict, owner: str = "system", *, timeout: float | None = None, count: bool = True) -> dict:
    """Run one worker request; returns the worker's JSON dict. Raises RunnerError on infrastructure failure
    (Pine/compile problems are returned as {"ok": false, "diagnostics": [...]}, not raised)."""
    node = node_binary()
    if not node or not os.path.exists(WORKER):
        raise RunnerError("unavailable", "Node.js or the Pine worker is not installed")
    if not os.path.isdir(os.path.join(PKG_DIR, "node_modules", "pinets")):
        raise RunnerError("unavailable", "pinets is not installed (run `npm ci` in backend/app/smart_chart)")
    if count:
        check_rate(owner)                 # internal re-runs of one user action (causality check) pass count=False
    payload = json.dumps(request).encode("utf-8")
    if len(payload) > config.MAX_RUNNER_OUTPUT_BYTES:
        raise RunnerError("too_large", "request too large")
    if not _slots.acquire(timeout=2.0):
        raise RunnerError("busy", "the Pine runtime is busy; retry in a moment")
    t = timeout or config.EXEC_TIMEOUT_SEC
    proc = None
    try:
        cmd = [node, "--permission", f"--allow-fs-read={PKG_DIR}", f"--max-old-space-size={config.NODE_HEAP_MB}", WORKER]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={},
                                process_group=0, close_fds=True, cwd=PKG_DIR)
        _limit(proc.pid)
        try:
            out, err = proc.communicate(payload, timeout=t)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, 9)
            except ProcessLookupError:
                pass
            proc.communicate()
            raise RunnerError("timeout", f"script exceeded the {t:.0f}s execution limit") from None
        if proc.returncode != 0:
            raise RunnerError("crashed", f"Pine worker exited with code {proc.returncode} (out of memory or CPU limit?)")
        if len(out) > config.MAX_RUNNER_OUTPUT_BYTES:
            raise RunnerError("too_large", "script output exceeds the size limit")
        try:
            return json.loads(out.decode("utf-8").strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise RunnerError("bad_output", "Pine worker returned unreadable output") from None
    finally:
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, 9)
            except ProcessLookupError:
                pass
        _slots.release()
