"""Small JSON file helpers shared by the arena modules (one implementation instead of six copies of `json.load(open(p))` / `json.dump(d, open(tmp, "w")); os.replace(...)`).
read_json  : parse a file, `default` when it is missing or unreadable; the file handle is always closed.
write_json : atomic replace (unique temporary file in the SAME directory, then os.replace): a crash or kill at any instant leaves either the whole old file or the whole new one, and two writers (threads or
             processes) can never share a temporary name. `fsync=True` also survives power loss (slower on this shared disk)."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time

_LOCK = threading.Lock()


def read_json(path: str, default=None, *, encoding: str = "utf-8"):
    try:
        with open(path, encoding=encoding) as f: return json.load(f)
    except Exception:
        return default


def write_json(path: str, obj, *, indent: int | None = None, fsync: bool = False) -> None:
    d = os.path.dirname(os.path.abspath(path)); os.makedirs(d, exist_ok=True)
    with _LOCK:
        fd, tmp = tempfile.mkstemp(dir=d, prefix=os.path.basename(path) + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(obj, f, indent=indent)
                if fsync: f.flush(); os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try: os.unlink(tmp)
            except OSError: pass
            raise


_CACHE: dict = {}


def read_json_cached(path: str, default=None):
    """read_json for small configuration files that are consulted on every request (participants, contest dates): re-read only when the file changes (inode / mtime / size); a file touched in the last 2 s is read
    every time, because timestamps are coarse and two quick rewrites can look identical. The cached object is shared: callers must not modify it."""
    try: st = os.stat(path)
    except OSError: return default
    key = (st.st_ino, st.st_mtime_ns, st.st_size); hit = _CACHE.get(path)
    if hit is not None and hit[0] == key and time.time() - st.st_mtime >= 2.0: return hit[1]
    val = read_json(path, default); _CACHE[path] = (key, val); return val
